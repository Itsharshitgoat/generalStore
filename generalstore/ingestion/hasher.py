"""
File content hashing for deduplication in generalStore.

Maintains a persistent JSON cache mapping file paths to their SHA-256
hashes so the ingestion engine can skip files that have not changed.
"""

import hashlib
import json
import logging
from pathlib import Path

from generalstore.config import get_settings

logger = logging.getLogger(__name__)

# Size of read buffer when hashing file contents
_HASH_CHUNK_SIZE = 8192  # 8 KB


class FileHasher:
    """Track file content hashes to detect changes between ingestion runs."""

    def __init__(self) -> None:
        settings = get_settings()
        self._cache_path: Path = settings.hash_cache_path
        self._cache: dict[str, str] = self._load_cache()
        logger.info(
            "FileHasher initialised – %d entries loaded from %s",
            len(self._cache),
            self._cache_path,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def has_changed(self, filepath: Path) -> bool:
        """Return ``True`` if *filepath* is new or its content hash differs."""
        key = str(filepath.resolve())
        current_hash = self._compute_hash(filepath)
        cached_hash = self._cache.get(key)

        if cached_hash is None:
            logger.debug("No cached hash for %s – treating as changed", filepath.name)
            return True

        changed = current_hash != cached_hash
        if changed:
            logger.debug("Hash mismatch for %s", filepath.name)
        return changed

    def update_hash(self, filepath: Path) -> None:
        """Compute and store the current hash for *filepath*, then persist."""
        key = str(filepath.resolve())
        self._cache[key] = self._compute_hash(filepath)
        self._save_cache()
        logger.debug("Updated hash for %s", filepath.name)

    def remove_hash(self, filepath: Path) -> None:
        """Remove the hash entry for *filepath* and persist."""
        key = str(filepath.resolve())
        if key in self._cache:
            del self._cache[key]
            self._save_cache()
            logger.debug("Removed hash entry for %s", filepath.name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_hash(self, filepath: Path) -> str:
        """Return the SHA-256 hex digest of *filepath*'s contents."""
        sha = hashlib.sha256()
        with open(filepath, "rb") as fh:
            while True:
                block = fh.read(_HASH_CHUNK_SIZE)
                if not block:
                    break
                sha.update(block)
        return sha.hexdigest()

    def _load_cache(self) -> dict[str, str]:
        """Load the hash cache from disk, returning an empty dict on failure."""
        if not self._cache_path.exists():
            logger.debug("Hash cache file not found at %s – starting fresh", self._cache_path)
            return {}
        try:
            text = self._cache_path.read_text(encoding="utf-8")
            data = json.loads(text)
            if isinstance(data, dict):
                return data
            logger.warning("Hash cache is not a dict – starting fresh")
            return {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load hash cache (%s) – starting fresh", exc)
            return {}

    def _save_cache(self) -> None:
        """Persist the hash cache to disk, creating parent directories."""
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps(self._cache, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("Failed to save hash cache: %s", exc)
