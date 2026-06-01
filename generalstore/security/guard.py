"""
Security boundary for generalStore.

Provides path traversal protection, access control,
and ensures all operations stay within allowed directories.
"""

import os
from pathlib import Path
from typing import List, Optional

from generalstore.config import get_settings


class SecurityGuard:
    """
    Enforces security boundaries for file access.
    
    All file paths returned by the knowledge engine must pass
    through this guard to prevent path traversal attacks and
    ensure the LLM can only access data within allowed directories.
    """

    def __init__(self, allowed_directories: Optional[List[Path]] = None):
        """
        Initialize the security guard.
        
        Args:
            allowed_directories: List of directories the system is allowed to access.
                                 Defaults to WATCHED_DIRS from settings.
        """
        if allowed_directories is not None:
            self._allowed_dirs = [d.resolve() for d in allowed_directories]
        else:
            self._allowed_dirs = get_settings().watched_dir_paths

    @property
    def allowed_directories(self) -> List[Path]:
        """Return the list of allowed directories."""
        return self._allowed_dirs

    def validate_path(self, path: str | Path) -> Path:
        """
        Validate that a path is within allowed directories.
        
        Resolves the path (following symlinks) and checks it falls
        within at least one allowed directory. Rejects path traversal
        attempts, symlink escapes, and paths outside boundaries.
        
        Args:
            path: The path to validate.
            
        Returns:
            The resolved, validated Path object.
            
        Raises:
            PermissionError: If the path is outside allowed directories.
            FileNotFoundError: If the path doesn't exist.
        """
        path = Path(path)

        # Resolve to absolute path (follows symlinks)
        try:
            resolved = path.resolve(strict=False)
        except (OSError, ValueError) as e:
            raise PermissionError(
                f"Invalid path '{path}': {e}"
            )

        # Check for path traversal patterns in the raw string
        raw = str(path)
        if ".." in raw:
            raise PermissionError(
                f"Path traversal detected in '{raw}'. "
                "Paths containing '..' are not allowed."
            )

        # Check the resolved path is within at least one allowed directory
        is_within = False
        for allowed_dir in self._allowed_dirs:
            try:
                resolved.relative_to(allowed_dir)
                is_within = True
                break
            except ValueError:
                continue

        if not is_within:
            allowed_str = ", ".join(str(d) for d in self._allowed_dirs)
            raise PermissionError(
                f"Access denied: '{resolved}' is outside allowed directories. "
                f"Allowed: [{allowed_str}]"
            )

        return resolved

    def validate_source_path(self, source_path: str) -> bool:
        """
        Validate a source file path from search results.
        
        Used to filter search results before returning them to the LLM.
        Returns True if the path is safe, False otherwise (does not raise).
        
        Args:
            source_path: The source file path from ChromaDB metadata.
            
        Returns:
            True if the path is within allowed boundaries and exists.
        """
        try:
            resolved = self.validate_path(source_path)
            return resolved.exists()
        except (PermissionError, FileNotFoundError):
            return False

    def is_supported_file(self, filepath: Path) -> bool:
        """Check if a file has a supported extension."""
        settings = get_settings()
        return filepath.suffix.lower() in settings.supported_extensions

    def sanitize_results(self, results: list[dict]) -> list[dict]:
        """
        Filter search results to only include paths within boundaries.
        
        Removes any results whose source_file path fails validation.
        This is the last line of defense before data reaches the LLM.
        
        Args:
            results: List of result dicts with 'source_file' keys.
            
        Returns:
            Filtered list with only safe results.
        """
        safe_results = []
        for result in results:
            source = result.get("source_file", "")
            if self.validate_source_path(source):
                safe_results.append(result)
        return safe_results
