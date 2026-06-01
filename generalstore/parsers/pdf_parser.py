"""
PDF parser using PyMuPDF (fitz).

Extracts text block-by-block from each page, groups consecutive
text blocks into chunks, and splits oversized pages into multiple
chunks when the combined text exceeds max_chunk_chars.
"""

import logging
from pathlib import Path
from typing import List

import fitz  # PyMuPDF

from generalstore.config import get_settings
from generalstore.parsers.base import BaseParser, DocumentChunk

logger = logging.getLogger(__name__)


class PDFParser(BaseParser):
    """Parser for PDF files using PyMuPDF block-level text extraction."""

    supported_extensions: List[str] = [".pdf"]

    def parse(self, filepath: Path) -> List[DocumentChunk]:
        """
        Parse a PDF file and return a list of DocumentChunks.

        Each page's text blocks are grouped and, if the combined text
        exceeds max_chunk_chars, split into multiple chunks.  Pages
        with no extractable text (e.g. scanned/handwritten) are
        logged and skipped.

        Args:
            filepath: Absolute path to the PDF file.

        Returns:
            List of DocumentChunk objects with page-level metadata.

        Raises:
            FileNotFoundError: If the file does not exist.
            RuntimeError: If PDF parsing fails.
        """
        filepath = Path(filepath).resolve()
        if not filepath.exists():
            raise FileNotFoundError(f"PDF file not found: {filepath}")

        settings = get_settings()
        subject = self._derive_subject(filepath)
        chunks: List[DocumentChunk] = []
        chunk_index = 0

        try:
            doc = fitz.open(str(filepath))
        except Exception as exc:
            raise RuntimeError(
                f"Failed to open PDF '{filepath.name}': {exc}"
            ) from exc

        try:
            for page_num in range(len(doc)):
                page = doc[page_num]
                page_number = page_num + 1  # 1-indexed

                try:
                    blocks = page.get_text("blocks")
                except Exception as exc:
                    logger.warning(
                        "Failed to extract blocks from page %d of '%s': %s",
                        page_number,
                        filepath.name,
                        exc,
                    )
                    continue

                # Filter to text blocks only (block_type == 0)
                text_blocks = [
                    blk[4] for blk in blocks if blk[6] == 0
                ]

                if not text_blocks:
                    logger.warning(
                        "No extractable text on page %d of '%s' "
                        "(possibly a scanned/handwritten page)",
                        page_number,
                        filepath.name,
                    )
                    continue

                # Combine all text blocks on this page
                combined = "\n".join(text_blocks)
                page_text = self._clean_text(combined)

                if len(page_text) < settings.min_chunk_chars:
                    continue

                # Split into sub-chunks if text exceeds max_chunk_chars
                sub_texts = self._split_text(page_text, settings.max_chunk_chars)

                for sub_text in sub_texts:
                    cleaned = self._clean_text(sub_text)
                    if len(cleaned) < settings.min_chunk_chars:
                        continue
                    chunks.append(
                        DocumentChunk(
                            text=cleaned,
                            source_file=str(filepath),
                            file_type=".pdf",
                            chunk_index=chunk_index,
                            page_number=page_number,
                            subject=subject,
                        )
                    )
                    chunk_index += 1
            total_pages = len(doc)
        finally:
            doc.close()

        logger.info(
            "Parsed PDF '%s': %d chunks from %d pages",
            filepath.name,
            len(chunks),
            total_pages,
        )
        return chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_text(text: str, max_chars: int) -> List[str]:
        """
        Split *text* into segments of at most *max_chars* characters,
        preferring to break at sentence or word boundaries.
        """
        if len(text) <= max_chars:
            return [text]

        parts: List[str] = []
        while text:
            if len(text) <= max_chars:
                parts.append(text)
                break

            # Try to find a sentence-ending break
            split_pos = text.rfind(". ", 0, max_chars)
            if split_pos == -1 or split_pos < max_chars // 2:
                # Fall back to a word boundary
                split_pos = text.rfind(" ", 0, max_chars)
            if split_pos == -1:
                # No good boundary – hard split
                split_pos = max_chars

            parts.append(text[: split_pos + 1].strip())
            text = text[split_pos + 1 :].strip()

        return parts
