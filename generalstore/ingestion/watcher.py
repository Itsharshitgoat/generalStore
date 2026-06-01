"""
Watchdog filesystem observer for generalStore.

Monitors watched directories for file system events and
triggers the ingestion pipeline when files are created,
modified, or deleted.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from generalstore.config import get_settings

logger = logging.getLogger(__name__)


class IngestionEventHandler(FileSystemEventHandler):
    """
    Handles filesystem events and routes them to the ingestion engine.
    
    Implements debouncing to avoid processing rapid-fire events
    (e.g., save-then-rename patterns from editors).
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, engine):
        """
        Args:
            loop: The asyncio event loop to schedule coroutines on.
            engine: The IngestionEngine instance to send files to.
        """
        super().__init__()
        self._loop = loop
        self._engine = engine
        self._settings = get_settings()
        self._last_event_time: dict[str, float] = {}  # filepath -> timestamp

    def _should_process(self, filepath: str) -> bool:
        """Check if we should process this event (debouncing)."""
        now = time.time()
        last = self._last_event_time.get(filepath, 0)
        if now - last < self._settings.watcher_debounce_seconds:
            return False
        self._last_event_time[filepath] = now
        return True

    def _is_supported(self, filepath: str) -> bool:
        """Check if the file has a supported extension."""
        path = Path(filepath)
        return path.suffix.lower() in self._settings.supported_extensions

    def on_created(self, event):
        if event.is_directory:
            return
        if not self._is_supported(event.src_path):
            return
        if not self._should_process(event.src_path):
            return

        filepath = Path(event.src_path)
        logger.info(f"📄 File created: {filepath.name}")
        asyncio.run_coroutine_threadsafe(
            self._engine.enqueue(filepath), self._loop
        )

    def on_modified(self, event):
        if event.is_directory:
            return
        if not self._is_supported(event.src_path):
            return
        if not self._should_process(event.src_path):
            return

        filepath = Path(event.src_path)
        logger.info(f"✏️  File modified: {filepath.name}")
        asyncio.run_coroutine_threadsafe(
            self._engine.enqueue(filepath), self._loop
        )

    def on_deleted(self, event):
        if event.is_directory:
            return
        if not self._is_supported(event.src_path):
            return

        filepath = Path(event.src_path)
        logger.info(f"🗑️  File deleted: {filepath.name}")
        asyncio.run_coroutine_threadsafe(
            self._engine.handle_deletion(filepath), self._loop
        )

    def on_moved(self, event):
        if event.is_directory:
            return

        # Handle the source (old path) as a deletion
        if self._is_supported(event.src_path):
            src = Path(event.src_path)
            logger.info(f"🗑️  File moved away: {src.name}")
            asyncio.run_coroutine_threadsafe(
                self._engine.handle_deletion(src), self._loop
            )

        # Handle the destination (new path) as a creation
        if self._is_supported(event.dest_path):
            if self._should_process(event.dest_path):
                dest = Path(event.dest_path)
                logger.info(f"📄 File moved in: {dest.name}")
                asyncio.run_coroutine_threadsafe(
                    self._engine.enqueue(dest), self._loop
                )


class DirectoryWatcher:
    """
    Watches configured directories for file changes.
    
    Uses watchdog's Observer to monitor directories and routes
    events through the IngestionEventHandler to the ingestion engine.
    """

    def __init__(self, engine):
        """
        Args:
            engine: The IngestionEngine instance for processing files.
        """
        self._engine = engine
        self._settings = get_settings()
        self._observer: Optional[Observer] = None

    async def start(self):
        """Start watching all configured directories."""
        loop = asyncio.get_running_loop()
        handler = IngestionEventHandler(loop, self._engine)

        self._observer = Observer()

        for watch_dir in self._settings.watched_dir_paths:
            if not watch_dir.exists():
                logger.warning(f"Watch directory does not exist: {watch_dir}")
                continue

            self._observer.schedule(handler, str(watch_dir), recursive=True)
            logger.info(f"👁️  Watching: {watch_dir}")

        self._observer.daemon = True
        self._observer.start()
        logger.info("🔄 Directory watcher started")

    async def stop(self):
        """Stop the directory watcher."""
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
            logger.info("⏹️  Directory watcher stopped")

    @property
    def is_running(self) -> bool:
        """Check if the observer is currently running."""
        return self._observer is not None and self._observer.is_alive()
