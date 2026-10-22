"""Persistent server-side operator settings.

The auto-approve mode is the first setting that must survive a restart, so it
lives in a small JSON file instead of in process memory. Writes are atomic (a
temporary file in the same directory, then ``os.replace``), so a crash mid-write
can never leave a truncated file for the next start to read; ``bible_data/``
stays untouched and read-only.

Reads are best-effort by design: a missing, unreadable, malformed, or unexpected
file logs a warning and falls back to the configured default. Auto-approval is a
convenience, so it must never keep the server from starting, and it must never
prevent an operator from approving references by hand.
"""

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, TypeGuard

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


class SettingsStore:
    """Read and write the server's persisted settings JSON file."""

    def __init__(self, path: Path | str) -> None:
        """Bind the store to ``path``; the file is created on the first write."""
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        """Return the settings object, or an empty dict when it is unusable."""
        try:
            data = json.loads(self.path.read_bytes().decode("utf-8"))
        except FileNotFoundError:
            # No persisted settings yet: the caller's default applies.
            return {}
        except (OSError, ValueError) as e:
            logger.warning(f"Ignoring settings file {self.path}: {e}")
            return {}
        if not isinstance(data, dict):
            logger.warning(f"Ignoring settings file {self.path}: expected a JSON object")
            return {}
        return data

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
        settings = self._read()
        settings["bible_version"] = version
        settings["bible_version_db_path"] = db_path
        if not self._write(settings):
            return False
        logger.info(f"Persisted Bible version '{version}' to {self.path}")
        return True

    def save_auto_mode(self, auto_mode: AutoMode) -> bool:
        """Persist ``auto_mode`` atomically; returns False when the write failed.

        Keys this build does not know about are preserved. The caller keeps the
        in-memory value either way: a read-only or missing settings directory
        must not block an operator action on a live server.
        """
        settings = self._read()
        settings["auto_mode"] = auto_mode
        if not self._write(settings):
            return False
        logger.info(f"Persisted auto-approve mode '{auto_mode}' to {self.path}")
        return True

    def save_follow_auto(self, enabled: bool) -> bool:
        """Persist the follow-along auto-show switch; returns False when the write failed.

        Saved like ``save_auto_mode``: other keys are kept, and a failed write leaves
        the in-memory value in force.
        """
        settings = self._read()
        settings["follow_auto"] = enabled
        if not self._write(settings):
            return False
        logger.info(f"Persisted follow-along auto-show {'on' if enabled else 'off'} to {self.path}")
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
