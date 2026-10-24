"""Persistent server-side operator settings.

The auto-approve mode is the first setting that must survive a restart, so it
lives in a small JSON file instead of in process memory. Writes are atomic (a
temporary file in the same directory, then ``os.replace``), so a crash mid-write
can never leave a truncated file for the next start to read; ``bible_data/``
stays untouched and read-only.

Reads are best-effort by design: a missing, unreadable, malformed, or unexpected
file logs a warning and falls back to the configured default. Auto-approval is a
convenience, so it must never keep the server from starting, and it must never
prevent an operator from approving references by hand. A damaged file (unreadable,
not JSON, or not an object) is first copied beside it as a backup, so the next save,
which replaces the file, cannot silently drop what it held; if the copy cannot be
made, saves refuse to overwrite the file.
"""

import contextlib
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, TypeGuard

from .idle_screen import (
    DEFAULT_IDLE_SCREEN_FIT,
    IdleImage,
    IdleScreenFit,
    is_idle_screen_fit,
    is_image_kind,
    is_image_version,
)
from .models import AutoMode

logger = logging.getLogger(__name__)

# Modes the server accepts, most conservative first. Kept in step with the
# operator console's selector; anything else is rejected so that a typo cannot
# make the display show unreviewed verses.
ALLOWED_AUTO_MODES: tuple[AutoMode, ...] = ("off", "high", "medium_and_up", "all")

# Mode used when nothing valid is configured or persisted.
DEFAULT_AUTO_MODE: AutoMode = "off"

# Follow-along auto-show is off until an operator turns it on (``set_follow_auto``).
DEFAULT_FOLLOW_AUTO = False


def is_auto_mode(value: object) -> TypeGuard[AutoMode]:
    """Whether ``value`` is one of the modes the server accepts."""
    return isinstance(value, str) and value in ALLOWED_AUTO_MODES


def coerce_follow_auto(value: object, default: bool = DEFAULT_FOLLOW_AUTO) -> bool:
    """Return ``value`` when it is a boolean, otherwise ``default``.

    Persisted JSON is outside the type system: a hand-edited ``"yes"`` keeps
    follow-along auto-show off (with a warning) rather than being read as true.
    """
    if isinstance(value, bool):
        return value
    if value is not None:
        logger.warning(f"Ignoring invalid follow-along auto-show value {value!r}; using {default}")
    return default


def coerce_auto_mode(value: object, default: AutoMode = DEFAULT_AUTO_MODE) -> AutoMode:
    """Return ``value`` when it is a known mode, otherwise ``default``.

    Used for values that come from outside the type system (the
    ``BIBLELIVE_AUTO_MODE`` environment variable, persisted JSON): a typo turns
    auto-approval off (with a warning) instead of failing at startup.
    """
    if is_auto_mode(value):
        return value
    if isinstance(value, str) and value.strip():
        logger.warning(f"Ignoring invalid auto-approve mode {value!r}; using '{default}'")
    return default


def coerce_idle_screen_fit(
    value: object, default: IdleScreenFit = DEFAULT_IDLE_SCREEN_FIT
) -> IdleScreenFit:
    """Return ``value`` when it is a known fit mode, otherwise ``default`` (with a warning)."""
    if is_idle_screen_fit(value):
        return value
    if value is not None:
        logger.warning(f"Ignoring invalid idle-screen fit {value!r}; using '{default}'")
    return default


def _holds(path: Path, data: bytes) -> bool:
    """Whether ``path`` is a readable file holding exactly ``data``."""
    try:
        return path.read_bytes() == data
    except OSError:
        return False


def _write_backup(backup: Path, data: bytes) -> None:
    """Create ``backup`` holding ``data`` on disk; an existing file is never replaced."""
    try:
        with backup.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise
    except OSError:
        # A truncated copy must not pass for a backup.
        with contextlib.suppress(OSError):
            backup.unlink()
        raise


class SettingsStore:
    """Read and write the server's persisted settings JSON file."""

    def __init__(self, path: Path | str) -> None:
        """Bind the store to ``path``; the file is created on the first write."""
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        """Return the settings object, or an empty dict when it is unusable."""
        return self._load()[0]

    def _load(self) -> tuple[dict[str, Any], str | None]:
        """Return the settings object (empty when unusable) and why a save must not overwrite it.

        The second value is None unless the file exists and cannot be kept: a damaged file
        is copied aside first (see ``_keep_copy``), and a file that cannot be read at all
        cannot be copied either.
        """
        try:
            raw = self.path.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            # No persisted settings yet (or a parent that is a file): the caller's default applies.
            return {}, None
        except OSError as e:
            logger.warning(f"Ignoring settings file {self.path}: {e}")
            return {}, str(e)
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as e:
            problem = str(e)
        else:
            if isinstance(data, dict):
                return data, None
            problem = "expected a JSON object"
        logger.warning(f"Ignoring settings file {self.path}: {problem}")
        blocked = self._keep_copy(raw)
        if blocked is not None:
            logger.warning(
                f"Could not keep a copy of the damaged settings file {self.path}: {blocked}"
            )
        return {}, blocked

    def _keep_copy(self, raw: bytes) -> str | None:
        """Keep the damaged bytes ``raw`` in a backup beside the file, unless one already has them.

        Each distinct damaged content is copied once, so repeated reads and saves of the same
        file leave one backup. Returns None once the bytes are kept, otherwise why they are not.
        """
        prefix = f"{self.path.stem}.damaged-"
        try:
            for candidate in self.path.parent.iterdir():
                if candidate.name.startswith(prefix) and _holds(candidate, raw):
                    return None
        except OSError as e:
            return str(e)
        # Local time, like the log lines; a repeated second takes a counter suffix.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        attempt = 1
        while True:
            suffix = f"-{attempt}" if attempt > 1 else ""
            backup = self.path.with_name(f"{prefix}{stamp}{suffix}.json")
            try:
                _write_backup(backup, raw)
            except FileExistsError:
                attempt += 1
                continue
            except OSError as e:
                return str(e)
            logger.warning(f"Kept a copy of the damaged settings file at {backup}")
            return None

    def load_auto_mode(self, default: AutoMode = DEFAULT_AUTO_MODE) -> AutoMode:
        """Return the persisted auto-approve mode, or ``default``."""
        return coerce_auto_mode(self._read().get("auto_mode"), default=default)

    def load_follow_auto(self, default: bool = DEFAULT_FOLLOW_AUTO) -> bool:
        """Return the persisted follow-along auto-show switch, or ``default``."""
        return coerce_follow_auto(self._read().get("follow_auto"), default=default)

    def load_bible_version(self, db_path: str) -> str | None:
        """Return the persisted Bible translation id, or None if none applies.

        The choice is saved with the ``DB_PATH`` it was made under. If that path
        has since been changed, the new configuration wins and the saved choice
        is ignored, so editing ``BIBLELIVE_DB_PATH`` for a deployment takes
        effect instead of being silently overridden.
        """
        settings = self._read()
        value = settings.get("bible_version")
        if not isinstance(value, str) or not value:
            return None
        if settings.get("bible_version_db_path") != db_path:
            logger.warning(
                f"Ignoring saved Bible version {value!r}: BIBLELIVE_DB_PATH is no longer "
                "the one it was chosen under"
            )
            return None
        return value

    def save_bible_version(self, version: str, db_path: str) -> bool:
        """Persist the chosen Bible translation id; returns False when the write failed."""
        return self._update(
            {"bible_version": version, "bible_version_db_path": db_path},
            log=f"Persisted Bible version '{version}' to {self.path}",
        )

    def save_auto_mode(self, auto_mode: AutoMode) -> bool:
        """Persist ``auto_mode`` atomically; returns False when the write failed.

        Keys this build does not know about are preserved. The caller keeps the
        in-memory value either way: a read-only or missing settings directory
        must not block an operator action on a live server.
        """
        return self._update(
            {"auto_mode": auto_mode},
            log=f"Persisted auto-approve mode '{auto_mode}' to {self.path}",
        )

    def save_follow_auto(self, enabled: bool) -> bool:
        """Persist the follow-along auto-show switch; returns False when the write failed.

        Saved like ``save_auto_mode``: other keys are kept, and a failed write leaves
        the in-memory value in force.
        """
        return self._update(
            {"follow_auto": enabled},
            log=f"Persisted follow-along auto-show {'on' if enabled else 'off'} to {self.path}",
        )

    @property
    def directory(self) -> Path:
        """The folder the settings file lives in; the idle-screen picture is stored beside it."""
        return self.path.parent

    def load_idle_screen_fit(
        self, default: IdleScreenFit = DEFAULT_IDLE_SCREEN_FIT
    ) -> IdleScreenFit:
        """Return the persisted idle-screen fit mode, or ``default``."""
        return coerce_idle_screen_fit(self._read().get("idle_screen_fit"), default=default)

    def load_idle_screen_image(self) -> IdleImage | None:
        """Return the persisted idle-screen picture's format and version, or None.

        Only the record is checked here; whether the file is still there is the caller's
        business, because the file lives beside this file, not inside it.
        """
        value = self._read().get("idle_screen_image")
        if value is None:
            return None
        if (
            isinstance(value, dict)
            and is_image_kind(value.get("kind"))
            and is_image_version(value.get("version"))
        ):
            return IdleImage(kind=value["kind"], version=value["version"])
        logger.warning(f"Ignoring invalid idle-screen picture record in {self.path}")
        return None

    def save_idle_screen_fit(self, fit: IdleScreenFit) -> bool:
        """Persist the idle-screen fit mode; returns False when the write failed."""
        return self._update(
            {"idle_screen_fit": fit},
            log=f"Persisted idle-screen fit '{fit}' to {self.path}",
        )

    def save_idle_screen_image(self, image: IdleImage) -> bool:
        """Persist the idle-screen picture's record; returns False when the write failed.

        The picture file itself is written by the caller before this, so a failed save
        leaves the previous record in force.
        """
        return self._update(
            {"idle_screen_image": {"kind": image.kind, "version": image.version}},
            log=f"Persisted idle-screen picture ({image.kind}) to {self.path}",
        )

    def clear_idle_screen_image(self) -> bool:
        """Remove the idle-screen picture's record; returns False when the write failed."""
        return self._update(
            remove=("idle_screen_image",),
            log=f"Removed the idle-screen picture record from {self.path}",
        )

    def _update(
        self,
        changes: dict[str, Any] | None = None,
        *,
        remove: tuple[str, ...] = (),
        log: str,
    ) -> bool:
        """Set the keys in ``changes``, drop the keys in ``remove``, and write the file.

        Every other key is kept. ``log`` is logged at INFO after a successful write;
        returns False when the write failed, or when the file is damaged and could not be
        kept as a backup (then it is left as it is).
        """
        settings, blocked = self._load()
        if blocked is not None:
            logger.error(
                f"Settings not saved to {self.path}: the file could not be kept as a backup "
                f"({blocked}), so it was left unchanged"
            )
            return False
        settings.update(changes or {})
        for key in remove:
            settings.pop(key, None)
        if not self._write(settings):
            return False
        logger.info(log)
        return True

    def _write(self, settings: dict[str, Any]) -> bool:
        """Write ``settings`` atomically; returns False when the write failed."""
        tmp_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(settings, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            # Same directory, so os.replace is atomic: readers see either the old
            # file or the complete new one, never a partial write.
            os.replace(tmp_path, self.path)
        except OSError as e:
            logger.warning(f"Could not persist settings to {self.path}: {e}")
            if tmp_path is not None:
                # Best effort: a leftover temp file is harmless, a raised
                # exception here would turn a settings write into a failed
                # operator action.
                with contextlib.suppress(OSError):
                    tmp_path.unlink()
            return False
        return True
