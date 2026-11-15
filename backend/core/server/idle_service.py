"""Persisted idle-screen picture and fit settings."""

import asyncio
import logging
from typing import Protocol

from fastapi import WebSocket

from .idle_screen import (
    IdleImage,
    IdleScreenError,
    IdleScreenFit,
    IdleScreenImageInfo,
    IdleScreenState,
    ImageKind,
    commit_image_file,
    discard_staged_file,
    new_image_version,
    remove_image_files,
    stage_image_file,
)
from .models import DisplaySettings, IdleScreenPicture
from .settings_store import SettingsStore

# kept on the name this code logged under before it left state.py
logger = logging.getLogger("core.server.state")


class IdleScreenOwner(Protocol):
    settings_store: SettingsStore
    display_settings: DisplaySettings

    async def send_display_settings(self, websocket: WebSocket | None = None) -> None: ...


class IdleScreenService:
    """Own idle state; shared dependencies are looked up on the owner at call time."""

    def __init__(self, owner: IdleScreenOwner) -> None:
        self._owner = owner
        # The idle screen: its fit mode and picture, persisted beside the settings file.
        self.idle_screen_fit: IdleScreenFit = owner.settings_store.load_idle_screen_fit()
        self.idle_image: IdleImage | None = self._load_idle_image()
        self._sync_display_idle_image()
        # Serializes idle-screen uploads and clears, which touch the picture file and its record.
        self._idle_lock = asyncio.Lock()

    def _load_idle_image(self) -> IdleImage | None:
        """The persisted idle-screen picture, or None when none is saved or its file is gone.

        A saved record without its file is ignored rather than served as a broken image.
        """
        image = self._owner.settings_store.load_idle_screen_image()
        if image is not None and not image.path_in(self._owner.settings_store.directory).is_file():
            logger.warning(f"Ignoring the saved idle-screen picture: {image.filename} is missing")
            return None
        return image

    def _sync_display_idle_image(self) -> None:
        """Mirror the idle picture and its fit into ``display_settings`` for the displays."""
        picture = (
            None
            if self.idle_image is None
            else IdleScreenPicture(version=self.idle_image.version, fit=self.idle_screen_fit)
        )
        self._owner.display_settings = self._owner.display_settings.model_copy(
            update={"idle_image": picture}
        )

    def idle_screen_state(self) -> IdleScreenState:
        """The idle screen as the console reads it: the fit mode and a version tag, not a path."""
        image = self.idle_image
        return IdleScreenState(
            fit=self.idle_screen_fit,
            image=None
            if image is None
            else IdleScreenImageInfo(version=image.version, content_type=image.content_type),
        )

    async def set_idle_image(self, kind: ImageKind, data: bytes) -> None:
        """Store ``data`` as the idle-screen picture.

        The bytes are staged in a temporary file, the record is saved, and only then is
        the file moved into place, so a failure at any step raises ``IdleScreenError``
        and leaves the picture on show (file, record and memory) as it was. Writes run
        off the event loop, since a large picture takes a moment to flush to disk. The
        lock keeps two uploads (two consoles) from interleaving: one must not delete the
        file the other has just recorded.
        """
        directory = self._owner.settings_store.directory
        async with self._idle_lock:
            try:
                staged = await asyncio.to_thread(stage_image_file, directory, data)
            except OSError as e:
                logger.error(f"Could not store the idle-screen picture in {directory}: {e}")
                raise IdleScreenError(500, "The idle-screen picture could not be stored.") from e
            previous = self.idle_image
            image = IdleImage(kind=kind, version=new_image_version())
            if not self._owner.settings_store.save_idle_screen_image(image):
                discard_staged_file(staged)
                raise IdleScreenError(500, "The idle-screen picture could not be saved.")
            try:
                commit_image_file(staged, directory, kind)
            except OSError as e:
                logger.error(f"Could not put the idle-screen picture in place in {directory}: {e}")
                self._restore_idle_record(previous)
                raise IdleScreenError(500, "The idle-screen picture could not be stored.") from e
            self.idle_image = image
            # Files of the other formats are stale now. Best effort: the record points at this one.
            remove_image_files(directory, keep=kind)
            self._sync_display_idle_image()
        logger.info(f"Idle-screen picture set ({kind}, {len(data)} bytes)")
        await self._owner.send_display_settings()

    def _restore_idle_record(self, previous: IdleImage | None) -> None:
        """Put back the record of the picture on show after a new one could not be placed."""
        restored = (
            self._owner.settings_store.clear_idle_screen_image()
            if previous is None
            else self._owner.settings_store.save_idle_screen_image(previous)
        )
        if not restored:
            logger.error("Could not restore the previous idle-screen picture record")

    async def clear_idle_image(self) -> None:
        """Remove the idle-screen picture, so the display is blank while idle again.

        The record is removed first; if that cannot be saved, the picture stays as it was.
        """
        async with self._idle_lock:
            if not self._owner.settings_store.clear_idle_screen_image():
                raise IdleScreenError(500, "The idle screen could not be cleared.")
            self.idle_image = None
            remove_image_files(self._owner.settings_store.directory)
            self._sync_display_idle_image()
        logger.info("Idle-screen picture removed")
        await self._owner.send_display_settings()

    async def set_idle_fit(self, fit: IdleScreenFit, broadcast: bool = True) -> None:
        """Choose how the idle-screen picture fills the screen (``logo`` or ``fill``).

        Raises ``IdleScreenError`` when the choice cannot be saved; the mode in memory is
        then unchanged. Displays are told right away unless ``broadcast`` is False, for a
        caller that is about to send the settings itself (an upload that names its fit).
        """
        if not self._owner.settings_store.save_idle_screen_fit(fit):
            raise IdleScreenError(500, "The idle-screen fit could not be saved.")
        self.idle_screen_fit = fit
        self._sync_display_idle_image()
        logger.info(f"Idle-screen fit set to '{fit}'")
        if broadcast:
            await self._owner.send_display_settings()
