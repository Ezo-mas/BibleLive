"""Command line for ``python -m launcher``."""

import argparse
import json
import logging
import os
import signal
import sys
import webbrowser
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from .commands import check_gpu_command, download_speech_command, gpu_self_test_command
from .network import (
    ensure_network_token,
    firewall_applies,
    firewall_rule_exists,
    lan_ip,
    operator_url_with_token,
)
from .process import Launcher, LaunchPlan, build_plan
from .settings import (
    AUTO_MODES,
    COMPUTE_TYPES,
    DEVICES,
    LANGUAGES,
    LOG_LEVELS,
    MODELS,
    SPEECH_ENGINES,
    LauncherSettings,
    default_settings_path,
    load_settings,
    models_dir,
    save_settings,
)
from .viewmodel import network_view


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m launcher",
        description="Start the BibleLive server and audio pipeline together.",
        epilog="Flags override the saved settings; use --save to keep them. "
        "Arguments after '--' are passed to audio.cli.",
    )
    p.add_argument("--language", choices=LANGUAGES, help="spoken language")
    p.add_argument("--mic", dest="input_device", help="microphone name (see --list-mics)")
    p.add_argument("--list-mics", action="store_true", help="list audio inputs and exit")
    p.add_argument("--device", choices=DEVICES, help="Whisper device")
    p.add_argument(
        "--compute-type",
        choices=COMPUTE_TYPES,
        help="Whisper compute type (auto = best the GPU supports)",
    )
    p.add_argument("--model", choices=MODELS, help="Whisper model (English, and Whisper engine)")
    p.add_argument(
        "--speech-engine",
        choices=SPEECH_ENGINES,
        help="Lithuanian speech model: auto (by graphics card), whisper, kmynas (processor), "
        "kmynas+canary (NVIDIA GPU)",
    )
    p.add_argument("--port", type=int, help="server port (default 8000)")
    p.add_argument("--auto-mode", choices=AUTO_MODES)
    p.add_argument("--log-level", choices=LOG_LEVELS)
    p.add_argument(
        "--session-audio",
        type=_on_off,
        metavar="on|off",
        help="also record each service as a WAV in logs\\sessions (default on)",
    )
    p.add_argument(
        "--network",
        dest="allow_network",
        type=_on_off,
        metavar="on|off",
        help="let other devices on this network open the pages; they need the token (default off)",
    )
    p.add_argument("--ffmpeg-dir", help="directory containing ffmpeg (default: <app>/ffmpeg)")
    p.add_argument("--settings-file", type=Path, help="launcher settings JSON path")
    p.add_argument("--save", action="store_true", help="persist the resolved settings")
    p.add_argument("--open", dest="open_page", choices=["operator", "display", "none"])
    p.add_argument("--gui", action="store_true", help="open the graphical window")
    p.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--download-model",
        action="store_true",
        help="download the speech models the settings need (Whisper too, unless --no-whisper) "
        "and exit",
    )
    p.add_argument(
        "--no-whisper",
        action="store_true",
        help="with --download-model: skip the Whisper model (English) unless the engine is whisper",
    )
    p.add_argument(
        "--gpu-self-test",
        action="store_true",
        help="load the speech models on their device, transcribe 1 s of silence, "
        "print OK/FAIL, exit 0/1",
    )
    p.add_argument(
        "--check-gpu",
        action="store_true",
        help="fast CUDA probe: exit 0 if a usable GPU exists, 3 if not",
    )
    p.add_argument("--dry-run", action="store_true", help="print commands and env, then exit")
    p.add_argument("audio_args", nargs="*", help=argparse.SUPPRESS)
    return p


_OVERRIDES = (
    "language",
    "input_device",
    "device",
    "compute_type",
    "model",
    "speech_engine",
    "port",
    "auto_mode",
    "log_level",
    "ffmpeg_dir",
    "session_audio",
    "allow_network",
)


def _on_off(value: str) -> bool:
    if value not in ("on", "off"):
        raise argparse.ArgumentTypeError(f"expected on or off, got {value!r}")
    return value == "on"


def apply_overrides(settings: LauncherSettings, args: argparse.Namespace) -> LauncherSettings:
    """Settings with every flag that was actually given laid over the saved values."""
    given = {k: getattr(args, k) for k in _OVERRIDES if getattr(args, k, None) is not None}
    extra = [a for a in (args.audio_args or []) if a != "--"]
    if extra:
        given["extra_audio_args"] = extra
    return replace(settings, **given)


def format_plan(plan: LaunchPlan) -> str:
    interesting = [k for k in sorted(plan.env) if k.startswith("BIBLELIVE_")]
    interesting += ["PYTHONPATH", "PATH"]
    lines = [
        f"cwd:    {plan.cwd}",
        f"device: {plan.device}  compute type: {plan.compute_type}",
        f"speech: {plan.speech_engine}",
        f"server: {json.dumps(plan.server_cmd)}",
        f"audio:  {json.dumps(plan.audio_cmd)}",
        f"logs:   {plan.log_dir}",
        "env:",
    ]
    for k in interesting:
        # The operator token is a secret: never print it, even in a dry run.
        shown = "(hidden)" if k == "BIBLELIVE_OPERATOR_TOKEN" else plan.env.get(k, "")
        lines.append(f"  {k}={shown}")
    lines += [f"WARNING: {w}" for w in plan.warnings]
    return "\n".join(lines)


def page_url(plan: LaunchPlan, page: str) -> str:
    """Local URL of a page; in network mode the operator page carries the token."""
    if page == "operator" and plan.network_token:
        return operator_url_with_token(plan.port, plan.network_token)
    return f"{plan.base_url}/{page}"


def network_lines(port: int, token: str) -> list[str]:
    """The addresses other devices use and the token they need (printed once ready)."""
    view = network_view(port, token, lan_ip())
    if not view.display_url:
        return [f"No network address found for this computer (try ipconfig). Token: {token}"]
    return [
        f"Other devices on this network: display {view.display_url}  operator {view.operator_url}",
        f"Operator token (type it on the other device): {view.token}",
    ]


def firewall_hint() -> str | None:
    """A console line when Windows Firewall has no BibleLive rule (Windows only), else None."""
    if not firewall_applies() or firewall_rule_exists():
        return None
    return (
        "Windows Firewall has no BibleLive rule, so other devices may be blocked. "
        "Allow it from the launcher window (python -m launcher --gui)."
    )


def run_oneshot(args: argparse.Namespace, settings: LauncherSettings) -> int | None:
    """Handle --check-gpu / --download-model / --gpu-self-test; None if none was given."""
    if args.check_gpu:
        return check_gpu_command()
    if args.download_model:
        return download_speech_command(settings, models_dir(), with_whisper=not args.no_whisper)
    if args.gpu_self_test:
        return gpu_self_test_command(settings, models_dir())
    return None


def _is_oneshot(args: argparse.Namespace) -> bool:
    return bool(args.check_gpu or args.download_model or args.gpu_self_test)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # No console (pythonw / double-click): the GUI is the only usable interface.
    if (
        args.gui
        or args.smoke
        or (sys.stdout is None and not (args.dry_run or args.list_mics or _is_oneshot(args)))
    ):
        from .gui import main as gui_main

        return gui_main(smoke=args.smoke)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings_path = args.settings_file or default_settings_path()
    saved = load_settings(settings_path)
    settings = apply_overrides(saved, args)
    if settings.allow_network and not saved.network_token:
        # Made once and kept, so the other devices keep working after the next start. Only
        # the token is written here: other flags given on this run stay per run unless --save.
        settings = ensure_network_token(settings)
        save_settings(replace(saved, network_token=settings.network_token), settings_path)

    if args.save:
        print(f"Saved settings to {save_settings(settings, settings_path)}")

    oneshot = run_oneshot(args, settings)
    if oneshot is not None:
        return oneshot

    plan = build_plan(settings, data_dir=settings_path.parent)

    if args.list_mics:
        # Reuse the pipeline's listing; make a bundled ffmpeg visible first.
        os.environ["PATH"] = plan.env["PATH"]
        from audio.capture import list_input_devices

        try:
            devices = list_input_devices()
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print("Audio inputs (pass one to --mic, quoted):" if devices else "No audio inputs found.")
        for name in devices:
            print(f"  {name}")
        return 0

    if args.dry_run:
        print(format_plan(plan))
        return 0

    def on_event(kind: str, message: str) -> None:
        stream = sys.stderr if kind in ("error", "warning") else sys.stdout
        print(f"[{kind}] {message}", file=stream, flush=True)
        if kind == "ready" and plan.network_token:
            for line in network_lines(plan.port, plan.network_token):
                print(f"[network] {line}", flush=True)
            hint = firewall_hint()
            if hint:
                print(f"[network] {hint}", file=sys.stderr, flush=True)
        if kind == "ready" and args.open_page and args.open_page != "none":
            webbrowser.open(page_url(plan, args.open_page))

    launcher = Launcher(plan, on_event)
    signal.signal(signal.SIGINT, lambda *_: launcher.request_stop())
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_: launcher.request_stop())
    return launcher.run()


if __name__ == "__main__":
    sys.exit(main())
