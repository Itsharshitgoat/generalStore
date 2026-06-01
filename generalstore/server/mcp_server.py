"""
FastMCP server for generalStore.

Exposes the local knowledge base as structured MCP tools
that LLM clients (like Claude Desktop) can natively discover,
invoke, and query. All tools are strictly read-only.
"""

import logging
from datetime import datetime
from pathlib import Path

from fastmcp import FastMCP

from generalstore.config import get_settings
from generalstore.security.guard import SecurityGuard
from generalstore.vectorstore.store import VectorStore

logger = logging.getLogger(__name__)

# Initialize the MCP server
mcp = FastMCP(
    "generalStore",
    instructions=(
        "generalStore is a local knowledge engine that indexes study materials "
        "(PDFs, Word docs, PowerPoints, spreadsheets) into a searchable vector database. "
        "Use the query_knowledge tool to search for specific topics, concepts, or terms "
        "across all indexed materials. Results include source file paths and page/slide "
        "numbers for precise citation."
    ),
)

# Lazy-initialized singletons
_store: VectorStore | None = None
_guard: SecurityGuard | None = None


def _get_store() -> VectorStore:
    """Get or create the VectorStore singleton."""
    global _store
    if _store is None:
        _store = VectorStore()
    return _store


def _get_guard() -> SecurityGuard:
    """Get or create the SecurityGuard singleton."""
    global _guard
    if _guard is None:
        _guard = SecurityGuard()
    return _guard


@mcp.tool()
def query_knowledge(
    query: str,
    file_type_filter: str | None = None,
    n_results: int = 5,
) -> str:
    """
    Search the local knowledge base for relevant information.

    Performs semantic search across all indexed study materials
    (PDFs, Word documents, PowerPoints, spreadsheets) and returns
    matching text chunks with exact source citations.

    Args:
        query: The search query — a question, topic, or concept to find.
        file_type_filter: Optional filter by file type. Use one of:
                          ".pdf", ".docx", ".pptx", ".xlsx"
        n_results: Number of results to return (default: 5, max: 20).

    Returns:
        Formatted search results with text, source file, and location info.
    """
    logger.info(f"MCP query: '{query}' (filter={file_type_filter}, n={n_results})")

    store = _get_store()
    guard = _get_guard()

    # Clamp results
    n_results = max(1, min(n_results, 20))

    # Validate file type filter
    if file_type_filter:
        valid_types = {".pdf", ".docx", ".pptx", ".xlsx"}
        if file_type_filter not in valid_types:
            return (
                f"Invalid file_type_filter '{file_type_filter}'. "
                f"Valid options: {', '.join(sorted(valid_types))}"
            )

    try:
        results = store.query(
            query_text=query,
            n_results=n_results,
            file_type_filter=file_type_filter,
        )
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return f"Search error: {e}"

    if not results:
        return "No results found for your query. Try different keywords or remove the file type filter."

    # Security: filter results to only include allowed paths
    results = guard.sanitize_results(results)

    if not results:
        return "Results were found but filtered by security policy. The source files may have been moved or deleted."

    # Format results for the LLM
    output_parts = [f"## Search Results for: \"{query}\"\n"]

    for i, result in enumerate(results, 1):
        source = result.get("source_file", "Unknown")
        filename = Path(source).name
        score = result.get("score", 0)
        text = result.get("text", "")

        # Build location info
        location_parts = []
        if result.get("page_number"):
            location_parts.append(f"Page {result['page_number']}")
        if result.get("slide_number"):
            location_parts.append(f"Slide {result['slide_number']}")
        if result.get("heading"):
            location_parts.append(f"Section: {result['heading']}")
        if result.get("sheet_name"):
            location_parts.append(f"Sheet: {result['sheet_name']}")
        if result.get("subject"):
            location_parts.append(f"Subject: {result['subject']}")

        location_str = " | ".join(location_parts) if location_parts else "N/A"

        # Similarity score (ChromaDB returns distance; lower = better)
        relevance = f"{(1 - score) * 100:.1f}%" if score is not None else "N/A"

        output_parts.append(
            f"### Result {i}\n"
            f"- **File**: {filename}\n"
            f"- **Path**: {source}\n"
            f"- **Location**: {location_str}\n"
            f"- **Relevance**: {relevance}\n"
            f"\n> {text}\n"
        )

    return "\n".join(output_parts)


@mcp.tool()
def list_indexed_files() -> str:
    """
    List all files currently indexed in the knowledge base.

    Returns a list of all indexed files grouped by subject folder,
    useful for understanding what materials are available to search.
    """
    store = _get_store()
    guard = _get_guard()

    try:
        indexed = store.get_indexed_files()
    except Exception as e:
        logger.error(f"Failed to list indexed files: {e}")
        return f"Error listing files: {e}"

    if not indexed:
        return "No files are currently indexed. Run `generalstore index` to index your study materials."

    # Group by subject folder
    grouped: dict[str, list[str]] = {}
    for filepath in sorted(indexed):
        if not guard.validate_source_path(filepath):
            continue
        path = Path(filepath)
        # Try to find subject folder
        parts = path.parts
        subject = "Other"
        for i, part in enumerate(parts):
            if part == "Data" and i + 1 < len(parts):
                subject = parts[i + 1]
                break

        if subject not in grouped:
            grouped[subject] = []
        grouped[subject].append(path.name)

    if not grouped:
        return "No accessible files found in the knowledge base."

    output_parts = ["## Indexed Files\n"]
    total = 0
    for subject in sorted(grouped.keys()):
        files = grouped[subject]
        total += len(files)
        output_parts.append(f"### 📁 {subject} ({len(files)} files)")
        for f in sorted(files):
            output_parts.append(f"  - {f}")
        output_parts.append("")

    output_parts.insert(1, f"**Total: {total} files indexed**\n")

    return "\n".join(output_parts)


@mcp.tool()
def get_index_stats() -> str:
    """
    Get statistics about the current knowledge base index.

    Returns total chunks, total files, file type breakdown,
    and storage information.
    """
    store = _get_store()

    try:
        stats = store.get_stats()
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        return f"Error getting stats: {e}"

    output_parts = [
        "## Knowledge Base Statistics\n",
        f"- **Total Chunks**: {stats.get('total_chunks', 0):,}",
        f"- **Total Files**: {stats.get('total_files', 0):,}",
        "",
        "### File Type Breakdown",
    ]

    breakdown = stats.get("file_type_breakdown", {})
    if breakdown:
        for ft, count in sorted(breakdown.items()):
            ext_label = {
                ".pdf": "📕 PDF",
                ".docx": "📘 Word",
                ".pptx": "📙 PowerPoint",
                ".xlsx": "📗 Excel",
            }.get(ft, ft)
            output_parts.append(f"  - {ext_label}: {count:,} chunks")
    else:
        output_parts.append("  - No data yet")

    settings = get_settings()
    output_parts.extend([
        "",
        "### Configuration",
        f"- **Embedding Model**: {settings.embedding_model}",
        f"- **DB Path**: {settings.chroma_db_path}",
        f"- **Watched Dirs**: {', '.join(str(d) for d in settings.watched_dir_paths)}",
    ])

    return "\n".join(output_parts)


def run_server():
    """Run the MCP server via STDIO transport."""
    logger.info("Starting generalStore MCP server...")
    mcp.run()
