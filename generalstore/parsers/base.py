"""
Base parser and DocumentChunk dataclass.

All file-type parsers inherit from BaseParser and return
lists of DocumentChunk objects for embedding.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class DocumentChunk:
    """A single chunk of text extracted from a document, with full metadata."""

    # The extracted text content
    text: str

    # Absolute path to the source file
    source_file: str

    # File type extension (e.g., ".pdf", ".docx")
    file_type: str

    # Zero-indexed chunk number within the document
    chunk_index: int

    # Page number (1-indexed, for PDFs)
    page_number: Optional[int] = None

    # Slide number (1-indexed, for PPTX)
    slide_number: Optional[int] = None

    # Section heading this chunk falls under (for DOCX)
    heading: Optional[str] = None

    # Sheet name (for XLSX)
    sheet_name: Optional[str] = None

    # Subject folder name (derived from parent directory)
    subject: Optional[str] = None

    @property
    def metadata(self) -> dict:
        """Return a flat metadata dict suitable for ChromaDB storage."""
        meta = {
            "source_file": self.source_file,
            "file_type": self.file_type,
            "chunk_index": self.chunk_index,
        }
        if self.page_number is not None:
            meta["page_number"] = self.page_number
        if self.slide_number is not None:
            meta["slide_number"] = self.slide_number
        if self.heading is not None:
            meta["heading"] = self.heading
        if self.sheet_name is not None:
            meta["sheet_name"] = self.sheet_name
        if self.subject is not None:
            meta["subject"] = self.subject
        return meta

    @property
    def doc_id(self) -> str:
        """Generate a deterministic ID for this chunk based on file path and index."""
        import hashlib
        raw = f"{self.source_file}::chunk_{self.chunk_index}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class BaseParser(ABC):
    """Abstract base class for all file type parsers."""

    # Subclasses must define supported extensions
    supported_extensions: List[str] = []

    def can_parse(self, filepath: Path) -> bool:
        """Check if this parser handles the given file type."""
        return filepath.suffix.lower() in self.supported_extensions

    @abstractmethod
    def parse(self, filepath: Path) -> List[DocumentChunk]:
        """
        Parse a file and return a list of semantically chunked DocumentChunks.

        Args:
            filepath: Absolute path to the file to parse.

        Returns:
            List of DocumentChunk objects with text and metadata.

        Raises:
            FileNotFoundError: If the file doesn't exist.
            ValueError: If the file type is unsupported.
            RuntimeError: If parsing fails.
        """
        ...

    def _derive_subject(self, filepath: Path) -> Optional[str]:
        """
        Derive the subject/folder name from the file path.
        
        Looks for the directory name that's directly inside the Data folder,
        or uses the immediate parent directory name.
        """
        parts = filepath.parts
        # Look for a known pattern like "ds_Notes", "eom_Notes", etc.
        for i, part in enumerate(parts):
            if part == "Data" and i + 1 < len(parts):
                return parts[i + 1]
        # Fallback: use the immediate parent directory name
        return filepath.parent.name

    def _clean_text(self, text: str) -> str:
        """Clean and normalize extracted text."""
        if not text:
            return ""
        # Normalize whitespace
        import re
        text = re.sub(r'\s+', ' ', text).strip()
        # Remove null bytes and other control characters
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
        return text
