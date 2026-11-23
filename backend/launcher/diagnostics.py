"""Collect a bounded, token-redacted snapshot for launcher support."""

import json
import os
import platform
import re
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict
from datetime import datetime
from itertools import count
from pathlib import Path
from shutil import copyfileobj
from tempfile import TemporaryFile
from typing import IO, Any
from zipfile import ZIP_DEFLATED, ZipFile

from core.server.log_redaction import REDACTED, redact_tokens
from core.version import version_string

from .commands import describe_gpu, query_gpus
from .monitors import Monitor
from .platform import current
from .settings import LauncherSettings, models_dir
from .speech import (
    GGUF_MODELS,
    WHISPER_MODELS,
    cached_gguf_path,
    cached_whisper_dir,
    resolve_speech,
)

_LOG_LIMIT = 5 * 1024 * 1024
_JSONL_LIMIT = 20 * 1024 * 1024
_RECENT_COUNT = 10
_TOKEN_FRAGMENT = re.compile(r"(#token=)[^&\s\"']*", re.IGNORECASE)
_JSON_TOKEN = re.compile(r'("token"\s*:\s*")[^"\\]*(?:\\.[^"\\]*)*"', re.IGNORECASE)
_BEARER = re.compile(r"""(\bBearer\s+)[^\s"',;]+""", re.IGNORECASE)


def _redactor(*tokens: str | None) -> Callable[[str], str]:
    # JSON strings may contain an escaped version of the same secret.
    literals = {
        value
        for token in tokens
        if token
        for value in (token, json.dumps(token, ensure_ascii=False)[1:-1], json.dumps(token)[1:-1])
    }
    pattern = (
        re.compile("|".join(map(re.escape, sorted(literals, key=len, reverse=True))))
        if literals
        else None
    )

    def redact(line: str) -> str:
        if pattern:
            line = pattern.sub(lambda _: REDACTED, line)
        line = _JSON_TOKEN.sub(rf'\g<1>{REDACTED}"', line)
        line = _BEARER.sub(rf"\g<1>{REDACTED}", line)
        return redact_tokens(_TOKEN_FRAGMENT.sub(rf"\g<1>{REDACTED}", line))

    return redact


def _settings_snapshot(data_dir: Path, settings: LauncherSettings) -> dict[str, Any]:
    try:
        with (data_dir / "launcher.json").open(encoding="utf-8") as source:
            raw = json.loads(source.read(1024 * 1024))
        if isinstance(raw, dict):
            return raw
    except (OSError, ValueError):
        pass
    return asdict(settings)


def _redact_json(value: Any, redact: Callable[[str], str]) -> Any:
    # Redact before serialization so a replacement cannot break JSON escaping.
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {
            redact(key): REDACTED if key.casefold() == "token" else _redact_json(item, redact)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_json(item, redact) for item in value]
    return value


def _copy_text(
    archive: ZipFile,
    path: Path,
    name: str,
    limit: int,
    redact: Callable[[str], str],
    *,
    tail: bool,
) -> tuple[int, str]:
    # Stage only the sanitized text so a read failure never leaves a partial ZIP entry.
    with TemporaryFile() as staged:
        try:
            if not stat.S_ISREG(path.lstat().st_mode):
                return 0, "skipped: not a regular file"
            with path.open("rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode):
                    return 0, "skipped: not a regular file"
                size = info.st_size
                if size > limit and not tail:
                    return 0, "skipped: JSONL size limit"
                remaining = min(size, limit)
                offset = size - remaining
                if offset:
                    source.seek(offset - 1)
                    if source.read(1) != b"\n":
                        # The tail starts inside a line. Omit its fragment.
                        remaining -= len(source.readline(remaining))
                while remaining:
                    raw = source.readline(remaining)
                    if not raw:
                        break
                    remaining -= len(raw)
                    staged.write(redact(raw.decode("utf-8", errors="replace")).encode("utf-8"))
        except OSError:
            return 0, "skipped: could not read"
        staged.seek(0)
        with archive.open(name, "w") as entry:
            copyfileobj(staged, entry)
        status = "truncated: latest 5 MiB (starting at a complete line)" if offset else "included"
        return size - offset - remaining, status


def _recent_jsonl(directory: Path, manifest: dict[str, str]) -> list[Path]:
    if directory.is_symlink() or directory.is_junction():
        manifest[directory.name] = "skipped: linked directory"
        return []
    dated = []
    for path in directory.glob("*.jsonl"):
        name = f"{directory.name}/{path.name}"
        try:
            dated.append((path.stat().st_mtime_ns, path.name, path))
        except OSError:
            manifest[name] = "skipped: could not read file metadata"
    paths = [path for _, _, path in sorted(dated, reverse=True)]
    for path in paths[_RECENT_COUNT:]:
        manifest[f"{directory.name}/{path.name}"] = "skipped: outside the 10 most recent"
    return paths[:_RECENT_COUNT]


@contextmanager
def _bundle_output(out_path: Path) -> Iterator[tuple[Path, IO[bytes]]]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in count():
        final = out_path if not suffix else out_path.with_stem(f"{out_path.stem}-{suffix}")
        partial = final.with_suffix(".zip.partial")
        try:
            output = partial.open("xb")
        except FileExistsError:
            continue
        try:
            if final.exists():
                output.close()
                partial.unlink()
                continue
            with output:
                yield final, output
            partial.rename(final)
            return
        except BaseException:
            with suppress(OSError):
                partial.unlink(missing_ok=True)
            raise


def build_bundle(
    data_dir: Path,
    log_dir: Path,
    settings: LauncherSettings,
    out_path: Path,
    *,
    extra_info: dict[str, Any],
    now: datetime,
    env_token: str | None = None,
    plan_token: str | None = None,
) -> Path:
    """Write a snapshot from the supplied directories and metadata without probing the system."""
    snapshot = _settings_snapshot(data_dir, settings)
    saved_token = snapshot.get("network_token")
    redact = _redactor(
        settings.network_token,
        saved_token if isinstance(saved_token, str) else None,
        env_token,
        plan_token,
    )
    snapshot["network_token"] = REDACTED
    manifest = {"launcher.json": "included: network_token redacted"}
    with (
        _bundle_output(out_path) as (final, output),
        ZipFile(output, "w", compression=ZIP_DEFLATED) as archive,
    ):
        archive.writestr(
            "launcher.json",
            json.dumps(_redact_json(snapshot, redact), indent=2) + "\n",
        )
        for name in ("server.log", "audio.log"):
            _, manifest[name] = _copy_text(
                archive, log_dir / name, name, _LOG_LIMIT, redact, tail=True
            )
        budget = _JSONL_LIMIT
        for folder in ("services", "sessions"):
            for path in _recent_jsonl(log_dir / folder, manifest):
                name = f"{folder}/{path.name}"
                copied, manifest[name] = _copy_text(
                    archive, path, redact(name), budget, redact, tail=False
                )
                budget -= copied
        metadata = {
            **extra_info,
            "collected_at": now.isoformat(),
            "session_audio": "excluded",
            "files": manifest,
        }
        archive.writestr(
            "diagnostics.json",
            json.dumps(_redact_json(metadata, redact), indent=2) + "\n",
        )
    return final


def _run_gpu_query(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    kwargs["timeout"] = 2
    kwargs.update(current().child_process_options())
    return subprocess.run(*args, **kwargs)


def system_info(
    settings: LauncherSettings, monitors: list[Monitor], plan_engine: str | None = None
) -> dict[str, Any]:
    """Probe locally for support metadata. Model paths stay on the machine."""
    info: dict[str, Any] = {
        "biblelive_version": version_string(),
        "os": platform.platform(),
        "platform": sys.platform,
        "python_version": platform.python_version(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "app_bundle": sys.platform == "darwin"
        and any(p.endswith(".app") for p in Path(sys.executable).parts),
        "monitors": [asdict(monitor) for monitor in monitors],
    }
    try:
        gpus = query_gpus(_run_gpu_query)
        info["gpu"] = [describe_gpu(gpu) for gpu in gpus] if gpus else [describe_gpu(None)]
    except Exception:
        info["gpu"] = ["GPU probe unavailable"]
    info["speech"] = {
        "language": settings.language,
        "requested": settings.speech_engine,
        "resolved": plan_engine,
    }
    try:
        cache = models_dir()
        speech = resolve_speech(settings.speech_engine, cache)
        settings_engine = "whisper" if settings.language == "en" else speech.engine
        info["speech"].update(
            settings_resolved=settings_engine,
            resolved=plan_engine or settings_engine,
            warnings=speech.warnings if settings.language != "en" else [],
        )
        info["models"] = {
            "gguf": {
                model.filename: bool(cached_gguf_path(name, cache))
                for name, model in GGUF_MODELS.items()
            },
            "whisper_pinned": {
                name: bool(cached_whisper_dir(name, cache)) for name in WHISPER_MODELS
            },
        }
    except Exception:
        info["models"] = "Model probe unavailable"
    return info
