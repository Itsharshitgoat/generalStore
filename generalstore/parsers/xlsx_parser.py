"""
XLSX parser using pandas.

Reads every sheet in the workbook and batches rows into chunks of ~10.
Each row is serialized as ``Column: Value | Column: Value | …`` so
that the embedding model can understand the tabular structure.
"""

import logging
from pathlib import Path
from typing import List

import pandas as pd

from generalstore.config import get_settings
from generalstore.parsers.base import BaseParser, DocumentChunk

logger = logging.getLogger(__name__)

_ROWS_PER_CHUNK = 10


class XLSXParser(BaseParser):
    """Parser for .xlsx spreadsheet files."""

    supported_extensions: List[str] = [".xlsx"]

    def parse(self, filepath: Path) -> List[DocumentChunk]:
        """
        Parse an XLSX file and return a list of DocumentChunks.

        Each sheet is read via pandas.  Rows are batched in groups of
        10 and serialized as ``Column: Value | Column: Value | …``.
        Column headers are included at the top of each chunk for
        context.

        Args:
            filepath: Absolute path to the .xlsx file.

        Returns:
            List of DocumentChunk objects with sheet-level metadata.

        Raises:
            FileNotFoundError: If the file does not exist.
            RuntimeError: If XLSX parsing fails.
        """
        filepath = Path(filepath).resolve()
        if not filepath.exists():
            raise FileNotFoundError(f"XLSX file not found: {filepath}")

        settings = get_settings()
        subject = self._derive_subject(filepath)
        chunks: List[DocumentChunk] = []
        chunk_index = 0

        try:
            xlsx = pd.ExcelFile(str(filepath))
        except Exception as exc:
            raise RuntimeError(
                f"Failed to open XLSX '{filepath.name}': {exc}"
            ) from exc

        for sheet_name in xlsx.sheet_names:
            try:
                df = xlsx.parse(sheet_name, dtype=str)
            except Exception as exc:
                logger.warning(
                    "Failed to read sheet '%s' in '%s': %s",
                    sheet_name,
                    filepath.name,
                    exc,
                )
                continue

            if df.empty:
                logger.info(
                    "Sheet '%s' in '%s' is empty, skipping",
                    sheet_name,
                    filepath.name,
                )
                continue

            # Clean column names
            columns = [str(c).strip() for c in df.columns]
            df.columns = columns

            # Build a header context line
            header_line = "Columns: " + " | ".join(columns)

            # Process rows in batches
            total_rows = len(df)
            for start_row in range(0, total_rows, _ROWS_PER_CHUNK):
                end_row = min(start_row + _ROWS_PER_CHUNK, total_rows)
                batch = df.iloc[start_row:end_row]

                row_lines: List[str] = []
                for _, row in batch.iterrows():
                    cells = []
                    for col in columns:
                        value = row.get(col, "")
                        # Normalise NaN / None to empty string
                        if pd.isna(value):
                            value = ""
                        else:
                            value = str(value).strip()
                        cells.append(f"{col}: {value}")
                    row_lines.append(" | ".join(cells))

                # Combine header context + serialized rows
                chunk_text = header_line + "\n" + "\n".join(row_lines)
                cleaned = self._clean_text(chunk_text)

                if len(cleaned) < settings.min_chunk_chars:
                    continue

                # Respect max_chunk_chars
                segments = self._split_text(cleaned, settings.max_chunk_chars)
                for seg in segments:
                    if len(seg) < settings.min_chunk_chars:
                        continue
                    chunks.append(
                        DocumentChunk(
                            text=seg,
                            source_file=str(filepath),
                            file_type=".xlsx",
                            chunk_index=chunk_index,
                            sheet_name=sheet_name,
                            subject=subject,
                        )
                    )
                    chunk_index += 1

        xlsx.close()

        logger.info(
            "Parsed XLSX '%s': %d chunks from %d sheets",
            filepath.name,
            len(chunks),
            len(xlsx.sheet_names),
        )
        return chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_text(text: str, max_chars: int) -> List[str]:
        """Split text into segments of at most *max_chars*, breaking at boundaries."""
        if len(text) <= max_chars:
            return [text]

        parts: List[str] = []
        while text:
            if len(text) <= max_chars:
                parts.append(text)
                break

            split_pos = text.rfind(". ", 0, max_chars)
            if split_pos == -1 or split_pos < max_chars // 2:
                split_pos = text.rfind(" ", 0, max_chars)
            if split_pos == -1:
                split_pos = max_chars

            parts.append(text[: split_pos + 1].strip())
            text = text[split_pos + 1 :].strip()

        return parts
