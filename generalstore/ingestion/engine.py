"""
Async ingestion engine for generalStore.

Coordinates file parsing, content hashing, and vector store updates.
Supports both single-file processing (via an asyncio queue) and bulk
directory ingestion with a rich progress bar.
"""

import asyncio
import logging
from pathlib import Path

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from generalstore.config import get_settings
from generalstore.ingestion.hasher import FileHasher
from generalstore.parsers.base import BaseParser
from generalstore.parsers.pdf_parser import PDFParser
from generalstore.parsers.docx_parser import DOCXParser
from generalstore.parsers.pptx_parser import PPTXParser
from generalstore.parsers.xlsx_parser import XLSXParser
from generalstore.vectorstore.store import VectorStore

logger = logging.getLogger(__name__)


class IngestionEngine:
    """Orchestrates file ingestion into the vector store."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Path] = asyncio.Queue()
        self._store = VectorStore()
        self._hasher = FileHasher()

        # Instantiate all parsers
        self._parsers: list[BaseParser] = [
            PDFParser(),
            DOCXParser(),
            PPTXParser(),
            XLSXParser(),
        ]

        # Build extension → parser lookup
        self._parser_registry: dict[str, BaseParser] = {}
        for parser in self._parsers:
            for ext in parser.supported_extensions:
                self._parser_registry[ext] = parser

        settings = get_settings()
        self._supported_extensions: set[str] = set(settings.supported_extensions)

        logger.info(
            "IngestionEngine ready – supported extensions: %s",
            ", ".join(sorted(self._supported_extensions)),
        )

    # ------------------------------------------------------------------
    # Queue-based single-file workflow
    # ------------------------------------------------------------------

    async def enqueue(self, filepath: Path) -> None:
        """Add a file to the processing queue."""
        await self._queue.put(filepath)
        logger.debug("Enqueued %s", filepath.name)

    async def worker(self) -> None:
        """Continuously process files from the queue.

        Intended to run as a long-lived ``asyncio.Task``.  Uses
        ``asyncio.to_thread`` to offload CPU-bound parsing and embedding
        work to a thread-pool thread.
        """
        logger.info("Ingestion worker started")
        while True:
            filepath = await self._queue.get()
            try:
                await asyncio.to_thread(self._process_file_sync, filepath)
            except Exception:
                logger.exception("Worker failed to process %s", filepath)
            finally:
                self._queue.task_done()

    # ------------------------------------------------------------------
    # Core processing
    # ------------------------------------------------------------------

    async def process_file(self, filepath: Path) -> None:
        """Process a single file (async wrapper).

        Validates the file, checks for changes, parses, replaces old
        chunks, and updates the hash cache.  All heavy work is offloaded
        to a thread via ``asyncio.to_thread``.
        """
        await asyncio.to_thread(self._process_file_sync, filepath)

    def _process_file_sync(self, filepath: Path) -> None:
        """Synchronous implementation of single-file processing."""
        try:
            # --- Validation ------------------------------------------------
            if not filepath.exists():
                logger.warning("File not found, skipping: %s", filepath)
                return

            ext = filepath.suffix.lower()
            if ext not in self._supported_extensions:
                logger.warning("Unsupported extension '%s', skipping: %s", ext, filepath)
                return

            if not self._hasher.has_changed(filepath):
                logger.debug("File unchanged, skipping: %s", filepath.name)
                return

            # --- Parsing ---------------------------------------------------
            parser = self._parser_registry.get(ext)
            if parser is None:
                logger.error("No parser registered for '%s'", ext)
                return

            logger.info("Parsing %s", filepath.name)
            chunks = parser.parse(filepath)
            logger.info("Parsed %d chunks from %s", len(chunks), filepath.name)

            # --- Store update ----------------------------------------------
            source_key = str(filepath.resolve())

            # Remove stale chunks for this file first
            self._store.delete_by_source(source_key)

            # Insert fresh chunks
            if chunks:
                self._store.add_chunks(chunks)

            # --- Hash update -----------------------------------------------
            self._hasher.update_hash(filepath)
            logger.info("Successfully ingested %s (%d chunks)", filepath.name, len(chunks))

        except Exception:
            logger.exception("Failed to process file %s", filepath)

    # ------------------------------------------------------------------
    # Bulk directory ingestion
    # ------------------------------------------------------------------

    async def ingest_directory(self, dirpath: Path) -> tuple[int, int]:
        """Walk *dirpath* recursively and ingest all supported files.

        Uses synchronous processing (not the queue) so that a rich
        progress bar can accurately track completion.

        Returns:
            Tuple of (files_processed, total_chunks).
        """
        if not dirpath.is_dir():
            logger.error("Directory not found: %s", dirpath)
            return 0, 0

        # Collect all candidate files first
        files_to_process: list[Path] = sorted(
            f
            for f in dirpath.rglob("*")
            if f.is_file() and f.suffix.lower() in self._supported_extensions
        )

        if not files_to_process:
            logger.info("No supported files found in %s", dirpath)
            return 0, 0

        logger.info(
            "Found %d supported files in %s – starting bulk ingestion",
            len(files_to_process),
            dirpath,
        )

        files_processed = 0
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
        )

        with progress:
            task_id = progress.add_task("Ingesting files", total=len(files_to_process))

            for filepath in files_to_process:
                progress.update(task_id, description=f"[cyan]{filepath.name}")
                # Offload to thread so progress bar stays responsive
                await asyncio.to_thread(self._process_file_sync, filepath)
                files_processed += 1
                progress.advance(task_id)

        stats = self._store.get_stats()
        total_chunks = stats["total_chunks"]
        logger.info(
            "Bulk ingestion complete – %d chunks across %d files",
            total_chunks,
            stats["total_files"],
        )
        return files_processed, total_chunks

    # ------------------------------------------------------------------
    # Deletion handling
    # ------------------------------------------------------------------

    async def handle_deletion(self, filepath: Path) -> None:
        """Remove all stored data for a deleted file."""
        source_key = str(filepath.resolve())
        logger.info("Handling deletion of %s", filepath.name)

        await asyncio.to_thread(self._store.delete_by_source, source_key)
        self._hasher.remove_hash(filepath)
        logger.info("Deletion cleanup complete for %s", filepath.name)
