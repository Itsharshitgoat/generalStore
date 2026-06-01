"""
Configuration management for generalStore.

Loads settings from .env file and provides typed access
to all configuration values used across the application.
"""

import os
from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings


# Resolve project root relative to this file
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    # Directories to watch for file changes
    watched_dirs: str = str(PROJECT_ROOT / "Data")

    # ChromaDB persistent storage path
    chroma_db_path: str = str(PROJECT_ROOT / "chroma_db")

    # SentenceTransformer model name
    embedding_model: str = "all-MiniLM-L6-v2"

    # ChromaDB collection name
    collection_name: str = "generalstore_knowledge"

    # Supported file extensions
    supported_extensions: List[str] = [".pdf", ".docx", ".pptx", ".xlsx"]

    # Maximum chunk size in characters (safety limit)
    max_chunk_chars: int = 2000

    # Minimum chunk size in characters (filter noise)
    min_chunk_chars: int = 20

    # Debounce interval for watchdog events (seconds)
    watcher_debounce_seconds: float = 2.0

    # Number of search results to return by default
    default_search_results: int = 5

    model_config = {
        "env_file": str(PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def watched_dir_paths(self) -> List[Path]:
        """Parse WATCHED_DIRS as a colon-separated list of paths."""
        raw = self.watched_dirs
        paths = []
        for p in raw.split(":"):
            p = p.strip()
            if p:
                resolved = Path(p).resolve()
                paths.append(resolved)
        return paths

    @property
    def chroma_path(self) -> Path:
        return Path(self.chroma_db_path).resolve()

    @property
    def hash_cache_path(self) -> Path:
        return self.chroma_path / ".file_hashes.json"


# Singleton settings instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get or create the singleton Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
