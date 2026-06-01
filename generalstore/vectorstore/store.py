"""
ChromaDB vector store interface for generalStore.

Provides embedding, storage, retrieval, and management of
document chunks using a persistent ChromaDB collection with
SentenceTransformer embeddings.
"""

import logging
from collections import Counter

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from generalstore.config import get_settings
from generalstore.parsers.base import DocumentChunk

logger = logging.getLogger(__name__)

# Maximum number of chunks to upsert in a single ChromaDB call
_UPSERT_BATCH_SIZE = 100


class VectorStore:
    """Persistent ChromaDB vector store for document chunks."""

    def __init__(self) -> None:
        settings = get_settings()
        logger.info("Initializing ChromaDB client at %s", settings.chroma_path)

        self._client = chromadb.PersistentClient(
            path=str(settings.chroma_path),
        )

        self._embedding_fn = SentenceTransformerEmbeddingFunction(
            model_name=settings.embedding_model,
        )

        self._collection = self._client.get_or_create_collection(
            name=settings.collection_name,
            embedding_function=self._embedding_fn,
        )

        logger.info(
            "Collection '%s' ready (%d chunks)",
            settings.collection_name,
            self._collection.count(),
        )

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_chunks(self, chunks: list[DocumentChunk]) -> None:
        """Upsert document chunks into the collection in batches.

        Uses ``chunk.doc_id`` as the document ID so re-indexing the same
        file silently replaces stale embeddings.
        """
        if not chunks:
            logger.debug("add_chunks called with empty list – nothing to do")
            return

        total = len(chunks)
        logger.info("Upserting %d chunks (batch size %d)", total, _UPSERT_BATCH_SIZE)

        for start in range(0, total, _UPSERT_BATCH_SIZE):
            batch = chunks[start : start + _UPSERT_BATCH_SIZE]

            ids = [c.doc_id for c in batch]
            documents = [c.text for c in batch]
            metadatas = [c.metadata for c in batch]

            self._collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )
            logger.debug(
                "Upserted batch %d–%d of %d",
                start + 1,
                min(start + _UPSERT_BATCH_SIZE, total),
                total,
            )

        logger.info("Upsert complete – collection now has %d chunks", self._collection.count())

    def delete_by_source(self, filepath: str) -> None:
        """Delete all chunks whose ``source_file`` metadata matches *filepath*."""
        if self._collection.count() == 0:
            logger.debug("Collection is empty – nothing to delete for %s", filepath)
            return

        results = self._collection.get(
            where={"source_file": filepath},
        )

        ids_to_delete: list[str] = results.get("ids", [])
        if not ids_to_delete:
            logger.debug("No chunks found for source file %s", filepath)
            return

        self._collection.delete(ids=ids_to_delete)
        logger.info("Deleted %d chunks for %s", len(ids_to_delete), filepath)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        query_text: str,
        n_results: int = 5,
        file_type_filter: str | None = None,
    ) -> list[dict]:
        """Semantic search over the collection.

        Parameters
        ----------
        query_text:
            Natural-language query string.
        n_results:
            Maximum number of results to return.
        file_type_filter:
            If provided, restrict results to chunks with this ``file_type``
            metadata value (e.g. ``".pdf"``).

        Returns
        -------
        list[dict]
            Each dict contains ``text``, ``source_file``, ``score``,
            ``page_number``, ``slide_number``, ``heading``, ``sheet_name``,
            ``subject``, and ``chunk_index``.
        """
        if self._collection.count() == 0:
            logger.debug("Collection is empty – returning no results")
            return []

        query_kwargs: dict = {
            "query_texts": [query_text],
            "n_results": min(n_results, self._collection.count()),
        }
        if file_type_filter is not None:
            query_kwargs["where"] = {"file_type": file_type_filter}

        raw = self._collection.query(**query_kwargs)

        results: list[dict] = []
        # ChromaDB returns parallel lists wrapped in an outer list (one per query).
        ids_list = raw.get("ids", [[]])[0]
        docs_list = raw.get("documents", [[]])[0]
        dists_list = raw.get("distances", [[]])[0]
        metas_list = raw.get("metadatas", [[]])[0]

        for doc, dist, meta in zip(docs_list, dists_list, metas_list):
            results.append(
                {
                    "text": doc,
                    "source_file": meta.get("source_file"),
                    "score": dist,
                    "page_number": meta.get("page_number"),
                    "slide_number": meta.get("slide_number"),
                    "heading": meta.get("heading"),
                    "sheet_name": meta.get("sheet_name"),
                    "subject": meta.get("subject"),
                    "chunk_index": meta.get("chunk_index"),
                }
            )

        logger.debug("Query returned %d results", len(results))
        return results

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_indexed_files(self) -> set[str]:
        """Return the set of unique ``source_file`` values in the collection."""
        if self._collection.count() == 0:
            return set()

        all_meta = self._collection.get(include=["metadatas"])
        metadatas: list[dict] = all_meta.get("metadatas", [])

        return {m["source_file"] for m in metadatas if "source_file" in m}

    def get_stats(self) -> dict:
        """Return high-level statistics about the collection.

        Returns
        -------
        dict
            Keys: ``total_chunks``, ``total_files``, ``file_type_breakdown``.
        """
        total_chunks = self._collection.count()

        if total_chunks == 0:
            return {
                "total_chunks": 0,
                "total_files": 0,
                "file_type_breakdown": {},
            }

        all_meta = self._collection.get(include=["metadatas"])
        metadatas: list[dict] = all_meta.get("metadatas", [])

        unique_files = {m["source_file"] for m in metadatas if "source_file" in m}
        type_counts = Counter(m.get("file_type", "unknown") for m in metadatas)

        return {
            "total_chunks": total_chunks,
            "total_files": len(unique_files),
            "file_type_breakdown": dict(type_counts),
        }
