"""Live Bible lookup, translation discovery and switching."""

import logging
import sqlite3
from typing import Protocol

from ..bible_lookup import BibleLookup
from ..bible_versions import BibleVersion, discover_versions_in
from ..config import config
from .service_log import ServiceLog
from .settings_store import SettingsStore

# kept on the name this code logged under before it left state.py
logger = logging.getLogger("core.server.state")


class BibleVersionsOwner(Protocol):
    settings_store: SettingsStore
    service_log: ServiceLog

    def available_bible_versions(self) -> dict[str, BibleVersion]: ...

    def active_bible_version(self) -> str | None: ...

    def switch_bible_version(self, version: str) -> str: ...

    def _open_bible_version(self, version: str) -> BibleLookup: ...

    def _install_bible_version(self, version: str, new_lookup: BibleLookup) -> str | None: ...


class BibleVersions:
    """Own the lookup; shared dependencies are looked up on the owner at call time.

    Calls between these methods go through the owner too, as they did when they were
    AppState methods, so a patch on AppState still reaches them.
    """

    def __init__(self, owner: BibleVersionsOwner, bible_lookup: BibleLookup) -> None:
        self._owner = owner
        self.bible_lookup = bible_lookup

    def available_bible_versions(self) -> dict[str, BibleVersion]:
        """Translations that can be switched to: next to the database, then the user folder."""
        return discover_versions_in(config.bible_dirs())

    def active_bible_version(self) -> str | None:
        """The translation id the loaded Bible text reports, if it has metadata."""
        translation = self.bible_lookup.get_metadata().get("translation")
        return str(translation).strip().lower() if translation else None

    def switch_bible_version(self, version: str) -> str:
        """Load another translation and use it for all verse text from now on.

        This only swaps the text source. It does not touch the pending queue: a
        queued detection was validated against the old translation, so callers must
        drop it (``change_bible_version`` does, in the same step as the swap). The
        passage on screen is not re-rendered. The new database
        is opened and checked before the old one is released, so a bad file leaves
        the server on the translation it had.

        Raises:
            KeyError: ``version`` is not an available translation.
            ValueError: the translation's file loads but holds no verses.
        """
        new_lookup = self._owner._open_bible_version(version)
        self._owner._install_bible_version(version, new_lookup)
        return version

    def open_bible_version(self, version: str) -> BibleLookup:
        """Open and check a candidate without changing live state (worker-safe)."""
        target = self._owner.available_bible_versions().get(version)
        if target is None:
            raise KeyError(version)

        new_lookup = BibleLookup(str(target.path), readonly=config.DB_READONLY)
        try:
            if new_lookup.verse_count() == 0:
                raise ValueError(f"Bible translation '{version}' contains no verses.")
        except BaseException:
            new_lookup.close()
            raise
        return new_lookup

    def install_bible_version(self, version: str, new_lookup: BibleLookup) -> str | None:
        """Install on the owner thread; return the version active just before this swap."""
        previous = self._owner.active_bible_version()
        old_lookup, self.bible_lookup = self.bible_lookup, new_lookup
        old_lookup.close()
        if previous != version:
            self._owner.service_log.emit(
                "translation_switched", **{"from": previous, "to": version}
            )
        self._owner.settings_store.save_bible_version(version, str(config.resolve_db_path()))
        logger.info(f"Bible version switched to '{version}' ({new_lookup.db_path.name})")
        return previous

    def apply_start_language_version(self, language: str | None, saved: str | None) -> None:
        """At startup, open the translation of the language the pipeline starts in.

        Without this a fresh install, or one whose saved translation is the other
        language's default (English chosen at the end of the last service), opens
        on that text while the pipeline listens in Lithuanian. A saved translation
        that is no language's default (WEB) was picked by hand and is kept. Best
        effort, like the runtime switch: a missing translation is logged and skipped.
        """
        if not config.AUTO_BIBLE_VERSION or not language:
            return
        target = config.LANGUAGE_BIBLE_VERSIONS.get(language.strip().lower())
        if not target or target == self._owner.active_bible_version():
            return
        if saved and saved not in config.LANGUAGE_BIBLE_VERSIONS.values():
            return
        try:
            self._owner.switch_bible_version(target)
        except (KeyError, ValueError, OSError, sqlite3.Error) as e:
            logger.warning(f"Not opening Bible version {target!r} for language {language!r}: {e!r}")
