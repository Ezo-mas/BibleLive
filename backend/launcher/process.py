"""Orchestrates the BibleLive server and audio pipeline as child processes.

UI-agnostic: progress is reported through an ``on_event(kind, message)`` callback
so a CLI (or a later GUI) can sit on top.
"""

import contextlib
import logging
import os
import signal as signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from core.bible_versions import discover_versions_in

from . import platform
from .gpu import SupportedProbe, cuda_supported_compute_types, resolve_compute_type, resolve_device
from .i18n import resolve_ui_language
from .settings import LauncherSettings, app_data_dir
from .settings import models_dir as default_models_dir
from .speech import GGUF_LANGUAGES, resolve_speech, speech_threads

logger = logging.getLogger(__name__)

LOOPBACK = "127.0.0.1"
READY_TIMEOUT_S = 60.0

EventCallback = Callable[[str, str], None]

# Fixed texts of "status" events, so UIs can translate them by identity.
MSG_STARTING_SERVER_PREFIX = "Starting server"
MSG_STARTING_AUDIO = "Starting audio pipeline..."


class RestartMessage(str):
    """The run thread sets attempt/delay once; callback and GUI threads only read them."""

    attempt: int
    delay: int

    def __new__(cls, name: str, attempt: int, delay: int) -> "RestartMessage":
        message = super().__new__(
            cls, f"{name.capitalize()} exited; restarting in {delay} s (attempt {attempt})"
        )
        message.attempt = attempt
        message.delay = delay
        return message


def _ignore_event(_kind: str, _message: str) -> None:
    pass


def _default_app_root() -> Path:
    # backend/launcher/process.py -> repo / install root
    return Path(__file__).resolve().parents[2]


@dataclass
class LaunchPlan:
    """Everything needed to start the two child processes."""

    server_cmd: list[str]
    audio_cmd: list[str]
    env: dict[str, str]
    cwd: Path
    log_dir: Path
    port: int
    device: str
    compute_type: str
    warnings: list[str] = field(default_factory=list)
    speech_engine: str = "whisper"  # the Lithuanian engine actually run (after fallbacks)
    network_token: str | None = None  # set only in network mode (see network.py)
    user_bible_dir: Path | None = None  # created at launch, so the user can find it

    @property
    def base_url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}"

    @property
    def ready_url(self) -> str:
        return f"{self.base_url}/readyz"


LITHUANIAN_VERSION = "ltb"


def user_bible_dir(data_dir: Path) -> Path:
    """Where translations not shipped with the app go on this machine (read-only to BibleLive).

    Outside the installed app, so an update keeps them and the macOS app bundle stays
    unmodified (its signature covers every file inside).
    """
    return data_dir / "bible_data"


def build_plan(
    settings: LauncherSettings,
    *,
    app_root: Path | None = None,
    data_dir: Path | None = None,
    models_dir: Path | None = None,
    base_env: Mapping[str, str] | None = None,
    python: str | None = None,
    probe: SupportedProbe = cuda_supported_compute_types,
) -> LaunchPlan:
    """Resolve settings into concrete commands and environment (no side effects)."""
    app_root = app_root or _default_app_root()
    data_dir = data_dir or app_data_dir()
    env = dict(base_env if base_env is not None else os.environ)
    python = python or sys.executable

    device, device_warnings = resolve_device(settings.device, probe)
    compute_type = resolve_compute_type(device, settings.compute_type, probe)
    hf_models = models_dir or default_models_dir()
    speech = resolve_speech(settings.speech_engine, hf_models, probe)
    # The Whisper device only matters when Whisper transcribes (English, or the Whisper engine).
    whisper_used = speech.engine == "whisper" or settings.language not in GGUF_LANGUAGES
    warnings = list(device_warnings) if whisper_used else []
    if speech.engine == "kmynas+canary" and device == "cpu":
        warnings.append("Kmynas + Canary on the processor will be slow; use an NVIDIA GPU for it.")
    warnings += speech.warnings

    backend = str(app_root / "backend")
    existing = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p and p != backend]
    env["PYTHONPATH"] = os.pathsep.join([backend, *existing])
    network_token = settings.network_token if settings.allow_network else None
    if settings.allow_network and not network_token:
        raise ValueError("network mode needs a token; see network.ensure_network_token")
    env["BIBLELIVE_HOST"] = "0.0.0.0" if network_token else LOOPBACK
    env["BIBLELIVE_PORT"] = str(settings.port)
    if network_token:
        # Other devices reach the server over the LAN, so every operator action needs the
        # token. The audio pipeline reads the same variable (core.config, audio/cli.py), so
        # its POSTs to /api/reference stay authorized. The launcher still uses 127.0.0.1.
        env["BIBLELIVE_REQUIRE_AUTH"] = "true"
        env["BIBLELIVE_OPERATOR_TOKEN"] = network_token
    env["BIBLELIVE_SETTINGS_PATH"] = str(data_dir / "server_settings.json")
    # Translations added on this machine (not shipped with the app): see user_bible_dir().
    bibles = user_bible_dir(data_dir)
    env["BIBLELIVE_USER_BIBLE_DIR"] = str(bibles)
    env["BIBLELIVE_SERVICE_LOG_DIR"] = str(platform.current().log_dir(data_dir) / "services")
    env["BIBLELIVE_AUTO_MODE"] = settings.auto_mode
    # "auto" is not a language the server knows: resolve it here to the Windows UI language.
    env["BIBLELIVE_UI_LANGUAGE"] = resolve_ui_language(settings.ui_language)
    env["BIBLELIVE_LOG_LEVEL"] = settings.log_level
    # The server opens this language's Bible at startup (the pipeline gets --language).
    env["BIBLELIVE_START_LANGUAGE"] = settings.language
    env["PYTHONUNBUFFERED"] = "1"
    # Output goes to log files; Windows would otherwise write them in cp1252, which
    # cannot hold Lithuanian letters ("ų"), and every such log line would fail.
    env["PYTHONIOENCODING"] = "utf-8"
    # faster-whisper (huggingface_hub) caches models under HF_HOME/hub: keep them in the
    # machine-local models dir, not the user profile's ~/.cache.
    env["HF_HOME"] = str(hf_models)
    # Keep huggingface_hub's advice out of the service logs: Windows without Developer
    # Mode cannot make cache symlinks (copies work), and downloads need no HF_TOKEN.
    # Its errors are still logged.
    env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    env.setdefault("HF_HUB_VERBOSITY", "error")
    # XET_NOTE: model downloads use plain HTTPS, not Hugging Face's Xet transfer (hf_xet).
    # On 2026-11-17 Xet stalled at 0 MB on both test machines (Windows and the Mac) while
    # HTTPS fetched the same file at ~8 MB/s, and a stalled download left a lock that made
    # every retry wait forever. Set the variable yourself to opt back in.
    env.setdefault("HF_HUB_DISABLE_XET", "1")

    ffmpeg_dir = Path(settings.ffmpeg_dir) if settings.ffmpeg_dir else app_root / "ffmpeg"
    if ffmpeg_dir.is_dir():
        env["PATH"] = os.pathsep.join([str(ffmpeg_dir), env.get("PATH", "")])

    audio_cmd = [
        python,
        "-m",
        "audio.cli",
        "--server",
        f"http://{LOOPBACK}:{settings.port}",
        "--language",
        settings.language,
        "--model",
        settings.model,
        "--device",
        device,
        "--compute-type",
        compute_type,
        "--log-level",
        settings.log_level,
        "--stt",
        speech.engine,
        "--cpu-threads",
        str(speech_threads()),
        "--session-log-dir",
        str(platform.current().log_dir(data_dir) / "sessions"),
        "--session-audio",
        "on" if settings.session_audio else "off",
    ]
    audio_cmd += ["--user-bible-dir", str(bibles)]
    # The Lithuanian text for the verse read aloud: the same 'ltb' file the server shows,
    # found the same way (the app's bible_data first, then the user folder).
    ltb = discover_versions_in([app_root / "bible_data", bibles]).get(LITHUANIAN_VERSION)
    if ltb is not None:
        audio_cmd += ["--verse-text-db", str(ltb.path)]
    for name, path in speech.model_paths.items():
        audio_cmd += [f"--{name}-model", path]
    if settings.input_device:
        audio_cmd += ["--input-device", settings.input_device]
    audio_cmd += settings.extra_audio_args

    return LaunchPlan(
        server_cmd=[python, "-m", "core.server"],
        audio_cmd=audio_cmd,
        env=env,
        cwd=app_root,
        log_dir=platform.current().log_dir(data_dir),
        port=settings.port,
        device=device,
        compute_type=compute_type,
        warnings=warnings,
        speech_engine=speech.engine,
        network_token=network_token,
        user_bible_dir=bibles,
    )


def port_in_use(port: int, host: str = LOOPBACK) -> bool:
    """True if something is already listening on ``host:port``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def http_ready(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (loopback)
            return bool(resp.status == 200)
    except (urllib.error.URLError, OSError, ValueError):
        return False


def wait_for_ready(
    url: str,
    is_alive: Callable[[], bool],
    *,
    timeout: float = READY_TIMEOUT_S,
    interval: float = 0.5,
    check: Callable[[str], bool] = http_ready,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Poll ``url`` until it answers. Returns "ready", "died" or "timeout"."""
    deadline = clock() + timeout
    while True:
        if check(url):
            return "ready"
        if not is_alive():
            return "died"
        if clock() >= deadline:
            return "timeout"
        sleep(interval)


def kill_tree(proc: subprocess.Popen[bytes], *, grace: float = 5.0) -> None:
    """Stop a child tree; POSIX also signals the group after its leader has exited."""
    platform.current().kill_tree(proc, grace=grace)


class Launcher:
    """Runs a :class:`LaunchPlan`: startup, supervision and shutdown.

    Each child must first stay alive for 60 seconds before it can be restarted.

    The run thread owns restart bookkeeping and error fields. Child references,
    log handles and stopped-emission state are shared with stop() under _lock.
    _stop is an Event shared by the run and GUI/CLI threads; plan is read-only.
    """

    def __init__(
        self,
        plan: LaunchPlan,
        on_event: EventCallback | None = None,
        *,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        ready_timeout: float = READY_TIMEOUT_S,
        poll_interval: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.plan = plan
        self._on_event = on_event or _ignore_event
        self._popen = popen
        self._ready_timeout = ready_timeout
        self._poll_interval = poll_interval
        self._clock = clock
        self._stop = threading.Event()
        self._server: subprocess.Popen[bytes] | None = None
        self._audio: subprocess.Popen[bytes] | None = None
        self._logs: list[IO[bytes]] = []
        self._lock = threading.Lock()
        self._stopped_emitted = False
        # Machine-readable reason for the last "error" event (for translated UIs).
        self.error_code: str | None = None
        self.error_detail: str = ""

    def _emit(self, kind: str, message: str) -> None:
        logger.info("%s: %s", kind, message)
        self._on_event(kind, message)

    def _fail(self, code: str, message: str, detail: str = "") -> None:
        self.error_code = code
        self.error_detail = detail
        self._emit("error", message)

    def request_stop(self) -> None:
        # Also called from CLI signal handlers, including while _lock is held.
        self._stop.set()

    def _spawn(self, name: str, cmd: list[str]) -> subprocess.Popen[bytes]:
        self.plan.log_dir.mkdir(parents=True, exist_ok=True)
        if self.plan.user_bible_dir is not None:
            self.plan.user_bible_dir.mkdir(parents=True, exist_ok=True)
        path = self.plan.log_dir / f"{name}.log"
        log = next((stream for stream in self._logs if stream.name == str(path)), None)
        if log is None:
            log = open(path, "ab")  # noqa: SIM115 (closed in stop())
            self._logs.append(log)
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(cmd)}\n".encode())
        log.flush()
        extra = platform.current().child_process_options()
        return self._popen(
            cmd,
            cwd=str(self.plan.cwd),
            env=self.plan.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **extra,
        )

    def _wait(self, delay: float) -> None:
        self._stop.wait(delay)

    def _start_child(self, name: str, cmd: list[str]) -> subprocess.Popen[bytes] | None:
        # Publish under the shutdown lock: stop() can never miss a new child.
        with self._lock:
            if self._stop.is_set():
                return None
            proc = self._spawn(name, cmd)
            if self._stop.is_set():
                kill_tree(proc)
                return None
            if name == "server":
                self._server = proc
            else:
                self._audio = proc
            return proc

    def _child_exited(self, name: str, code: int) -> int:
        self._fail(
            f"{name}_exited",
            f"The {name} process exited (code {code}); see {self.plan.log_dir / f'{name}.log'}",
            str(self.plan.log_dir / f"{name}.log"),
        )
        return code or 1

    def run(self) -> int:
        """Start children and supervise those that have each stayed up for 60 seconds.

        Returns 0 on a requested stop, otherwise a non-zero code.
        """
        try:
            return self._run()
        finally:
            self.stop()

    def _run(self) -> int:
        for warning in self.plan.warnings:
            self._emit("warning", warning)
        if port_in_use(self.plan.port):
            self._fail(
                "port_in_use",
                f"Port {self.plan.port} is already in use. Close the other BibleLive "
                "(or whatever uses it) or choose another port with --port.",
                str(self.plan.port),
            )
            return 2

        self._emit("status", f"{MSG_STARTING_SERVER_PREFIX} on {LOOPBACK}:{self.plan.port}...")
        try:
            server = self._start_child("server", self.plan.server_cmd)
        except OSError as exc:
            self._fail("server_spawn", f"Could not start the server: {exc}", str(exc))
            return 1
        if server is None:
            return 0
        server_started = self._clock()
        outcome = wait_for_ready(
            self.plan.ready_url,
            lambda: server.poll() is None and not self._stop.is_set(),
            timeout=self._ready_timeout,
            interval=self._poll_interval,
            sleep=self._wait,
        )
        if self._stop.is_set():
            return 0
        if outcome != "ready":
            reason = (
                f"The server exited during startup (code {server.poll()}); see "
                f"{self.plan.log_dir / 'server.log'}"
                if outcome == "died"
                else f"The server was not ready after {self._ready_timeout:.0f}s"
            )
            self._fail(
                "server_failed" if outcome == "died" else "server_timeout",
                reason,
                str(self.plan.log_dir / "server.log"),
            )
            return 1
        self._emit("ready", self.plan.base_url)

        self._emit("status", MSG_STARTING_AUDIO)
        try:
            audio = self._start_child("audio", self.plan.audio_cmd)
        except OSError as exc:
            self._fail("audio_spawn", f"Could not start the audio pipeline: {exc}", str(exc))
            return 1
        if audio is None or self._stop.is_set():
            return 0
        audio_started = self._clock()
        for name, initial_proc in (("server", server), ("audio", audio)):
            code = initial_proc.poll()
            if code is not None:
                return self._child_exited(name, code)
        self._emit("running", f"Audio pipeline running; logs in {self.plan.log_dir}")

        started = {"server": server_started, "audio": audio_started}
        proven = dict.fromkeys(started, False)
        exits: dict[str, list[float]] = {"server": [], "audio": []}
        attempts = dict.fromkeys(started, 0)
        pending: dict[str, float] = {}
        while not self._stop.wait(self._poll_interval):
            for name in started:
                if self._stop.is_set():
                    return 0
                now = self._clock()
                proc = self._server if name == "server" else self._audio
                if name not in pending:
                    if proc is None:  # Concurrent stop() has already cleaned up.
                        return 0
                    code = proc.poll()
                    if code is None:
                        if now - started[name] >= 60:
                            proven[name] = True
                        continue
                    if not proven[name]:
                        with self._lock:
                            if self._stop.is_set():
                                return 0
                            return self._child_exited(name, code)
                    if now - started[name] >= 60:
                        attempts[name] = 0
                    exits[name] = [t for t in exits[name] if now - t < 60]
                    exits[name].append(now)
                    # Even an exited leader can leave ffmpeg alive on POSIX.
                    with self._lock:
                        if self._stop.is_set():
                            return 0
                        kill_tree(proc)
                        if name == "server":
                            self._server = None
                        else:
                            self._audio = None
                    if self._stop.is_set():
                        return 0
                    if len(exits[name]) >= 3:
                        detail = str(self.plan.log_dir / f"{name}.log")
                        self._fail(
                            f"{name}_restart_gave_up",
                            f"Repeated {name} exits; see {detail}",
                            detail,
                        )
                        return code or 1
                    attempts[name] += 1
                    delay = min(2 ** min(attempts[name] - 1, 5), 30)
                    pending[name] = self._clock() + delay
                    self._emit(f"{name}_restarting", RestartMessage(name, attempts[name], delay))
                    continue
                if now < pending[name]:
                    continue
                cmd = self.plan.server_cmd if name == "server" else self.plan.audio_cmd
                try:
                    proc = self._start_child(name, cmd)
                except OSError as exc:
                    self._fail(f"{name}_spawn", f"Could not start {name}: {exc}", str(exc))
                    return 1
                if proc is None:
                    return 0
                del pending[name]
                started[name] = self._clock()
                if name == "server":

                    def is_alive(child: subprocess.Popen[bytes] = proc) -> bool:
                        audio_child = self._audio
                        if (
                            audio_child is not None
                            and audio_child.poll() is None
                            and self._clock() - started["audio"] >= 60
                        ):
                            proven["audio"] = True
                        return child.poll() is None and not self._stop.is_set()

                    outcome = wait_for_ready(
                        self.plan.ready_url,
                        is_alive,
                        timeout=self._ready_timeout,
                        interval=self._poll_interval,
                        sleep=self._wait,
                    )
                    if self._stop.is_set():
                        return 0
                    if outcome == "died":
                        continue  # Count this replacement's exit on the next monitor pass.
                    if outcome != "ready":
                        self._fail(
                            "server_timeout",
                            "The restarted server was not ready in time",
                            str(self.plan.log_dir / "server.log"),
                        )
                        return 1
                    self._emit("ready", self.plan.base_url)
                else:
                    self._emit("running", f"Audio pipeline running; logs in {self.plan.log_dir}")
        return 0

    def stop(self) -> None:
        """Kill both process trees and close log files.

        Thread-safe and idempotent: may be called from a GUI thread while ``run()`` is
        active; "stopped" is emitted once.
        """
        self.request_stop()
        with self._lock:
            for proc in (self._audio, self._server):
                if proc is not None:
                    try:
                        kill_tree(proc)
                    except Exception:
                        logger.exception("Could not stop child process %s", proc.pid)
            self._audio = self._server = None
            for log in self._logs:
                with contextlib.suppress(OSError):
                    log.close()
            self._logs.clear()
            first = not self._stopped_emitted
            self._stopped_emitted = True
        if first:
            self._emit("stopped", "BibleLive stopped.")
