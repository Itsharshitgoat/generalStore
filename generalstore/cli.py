"""
CLI interface for generalStore — The Local Knowledge Engine.

Provides commands for indexing, searching, watching, and serving
the local knowledge base via a beautiful terminal interface.
"""

import asyncio
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.live import Live
from rich.markdown import Markdown

from generalstore.config import get_settings

console = Console()

# ASCII banner
BANNER = r"""
   ╔═════════════════════════════════════════════════════════╗
   ║         ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓          ║
   ║         ┃            generalStore            ┃          ║
   ║         ┃           Made by Harshit          ┃          ║
   ║         ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛          ║
   ╚═════════════════════════════════════════════════════════╝
"""


def setup_logging(verbose: bool = False):
    """Configure rich-formatted logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(
            console=console,
            rich_tracebacks=True,
            show_path=False,
        )],
    )
    # Suppress noisy third-party loggers
    logging.getLogger("chromadb").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
    logging.getLogger("watchdog").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def cli(verbose: bool):
    """generalStore — The Local Knowledge Engine 🧠

    Index your study materials, search with AI, and query via MCP.
    """
    setup_logging(verbose)


@cli.command()
@click.option(
    "--directory", "-d",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Specific directory to index (defaults to all watched dirs).",
)
def index(directory: Path | None):
    """Index all study materials into the knowledge base."""
    console.print(BANNER, style="bold cyan")
    console.print(
        Panel("🔍 [bold]Indexing study materials...[/bold]", style="blue")
    )

    settings = get_settings()

    if directory:
        dirs_to_index = [directory.resolve()]
    else:
        dirs_to_index = settings.watched_dir_paths

    # Show what we're indexing
    table = Table(title="📂 Directories to Index", show_header=True)
    table.add_column("Directory", style="cyan")
    table.add_column("Status", style="green")
    for d in dirs_to_index:
        status = "✅ Found" if d.exists() else "❌ Missing"
        table.add_row(str(d), status)
    console.print(table)
    console.print()

    async def _run_index():
        from generalstore.ingestion.engine import IngestionEngine

        engine = IngestionEngine()
        total_files = 0
        total_chunks = 0

        for dirpath in dirs_to_index:
            if not dirpath.exists():
                console.print(f"  ⚠️  Skipping missing dir: {dirpath}", style="yellow")
                continue

            files, chunks = await engine.ingest_directory(dirpath)
            total_files += files
            total_chunks += chunks

        # Final summary
        console.print()
        summary = Table(title="📊 Indexing Complete", show_header=True)
        summary.add_column("Metric", style="bold")
        summary.add_column("Value", style="green")
        summary.add_row("Files Processed", str(total_files))
        summary.add_row("Chunks Created", str(total_chunks))
        summary.add_row("Embedding Model", settings.embedding_model)
        summary.add_row("DB Location", str(settings.chroma_path))
        console.print(summary)

    asyncio.run(_run_index())


@cli.command()
def watch():
    """Start the file watcher daemon (foreground)."""
    console.print(BANNER, style="bold cyan")
    console.print(
        Panel(
            "👁️  [bold]Starting directory watcher...[/bold]\n"
            "Press Ctrl+C to stop.",
            style="blue",
        )
    )

    settings = get_settings()
    for d in settings.watched_dir_paths:
        console.print(f"  📂 Watching: {d}", style="cyan")

    async def _run_watch():
        from generalstore.ingestion.engine import IngestionEngine
        from generalstore.ingestion.watcher import DirectoryWatcher

        engine = IngestionEngine()

        # Start the worker
        worker_task = asyncio.create_task(engine.worker())

        # Start the watcher
        watcher = DirectoryWatcher(engine)
        await watcher.start()

        console.print()
        console.print(
            "  🟢 [bold green]Watcher active.[/bold green] "
            "Drop files into your watched directories to auto-index.",
        )
        console.print()

        try:
            # Keep running until Ctrl+C
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await watcher.stop()
            worker_task.cancel()
            console.print("\n  ⏹️  Watcher stopped.", style="yellow")

    try:
        asyncio.run(_run_watch())
    except KeyboardInterrupt:
        console.print("\n  👋 Goodbye!", style="bold cyan")


@cli.command()
@click.argument("query")
@click.option(
    "--type", "-t", "file_type",
    type=click.Choice([".pdf", ".docx", ".pptx", ".xlsx"]),
    help="Filter by file type.",
)
@click.option(
    "--results", "-n", "n_results",
    type=int, default=5,
    help="Number of results (default: 5).",
)
def search(query: str, file_type: str | None, n_results: int):
    """Search the knowledge base from the command line."""
    console.print(
        Panel(f"🔎 [bold]Searching:[/bold] {query}", style="blue")
    )

    from generalstore.vectorstore.store import VectorStore
    from generalstore.security.guard import SecurityGuard

    store = VectorStore()
    guard = SecurityGuard()

    results = store.query(
        query_text=query,
        n_results=n_results,
        file_type_filter=file_type,
    )

    results = guard.sanitize_results(results)

    if not results:
        console.print("  ❌ No results found.", style="red")
        return

    for i, result in enumerate(results, 1):
        source = result.get("source_file", "Unknown")
        filename = Path(source).name
        score = result.get("score", 0)
        text = result.get("text", "")
        relevance = f"{(1 - score) * 100:.1f}%" if score is not None else "N/A"

        # Build location info
        location_parts = []
        if result.get("page_number"):
            location_parts.append(f"📄 Page {result['page_number']}")
        if result.get("slide_number"):
            location_parts.append(f"🖼️  Slide {result['slide_number']}")
        if result.get("heading"):
            location_parts.append(f"📑 {result['heading']}")
        if result.get("sheet_name"):
            location_parts.append(f"📊 {result['sheet_name']}")
        location = " | ".join(location_parts) if location_parts else ""

        panel_title = f"Result {i} — {relevance} match"
        panel_content = (
            f"[bold cyan]{filename}[/bold cyan]\n"
            f"[dim]{source}[/dim]\n"
        )
        if location:
            panel_content += f"[yellow]{location}[/yellow]\n"
        panel_content += f"\n{text[:500]}{'...' if len(text) > 500 else ''}"

        console.print(
            Panel(panel_content, title=panel_title, border_style="green")
        )


@cli.command()
def status():
    """Show knowledge base statistics."""
    console.print(
        Panel("📊 [bold]Knowledge Base Status[/bold]", style="blue")
    )

    from generalstore.vectorstore.store import VectorStore

    store = VectorStore()
    stats = store.get_stats()

    table = Table(show_header=True, title="Index Statistics")
    table.add_column("Metric", style="bold")
    table.add_column("Value", style="cyan")

    table.add_row("Total Chunks", f"{stats.get('total_chunks', 0):,}")
    table.add_row("Total Files", f"{stats.get('total_files', 0):,}")

    breakdown = stats.get("file_type_breakdown", {})
    for ft, count in sorted(breakdown.items()):
        label = {
            ".pdf": "📕 PDF Chunks",
            ".docx": "📘 Word Chunks",
            ".pptx": "📙 PPTX Chunks",
            ".xlsx": "📗 Excel Chunks",
        }.get(ft, f"{ft} chunks")
        table.add_row(label, f"{count:,}")

    console.print(table)

    settings = get_settings()
    config_table = Table(title="Configuration", show_header=True)
    config_table.add_column("Setting", style="bold")
    config_table.add_column("Value", style="green")
    config_table.add_row("Embedding Model", settings.embedding_model)
    config_table.add_row("DB Path", str(settings.chroma_path))
    config_table.add_row(
        "Watched Dirs",
        "\n".join(str(d) for d in settings.watched_dir_paths),
    )
    console.print(config_table)


@cli.command()
def serve():
    """Start the MCP server (STDIO transport)."""
    # MCP server communicates over STDIO, so we can't print banners
    # Log to stderr instead
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        stream=sys.stderr,
        force=True,
    )
    logging.getLogger("chromadb").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)

    from generalstore.server.mcp_server import run_server
    run_server()


@cli.command()
@click.confirmation_option(prompt="⚠️  This will delete all indexed data. Are you sure?")
def purge():
    """Clear the entire vector store."""
    console.print(
        Panel("🗑️  [bold]Purging knowledge base...[/bold]", style="red")
    )

    from generalstore.vectorstore.store import VectorStore
    from generalstore.ingestion.hasher import FileHasher

    store = VectorStore()
    hasher = FileHasher()

    # Delete the collection and recreate it
    try:
        store._client.delete_collection(get_settings().collection_name)
        console.print("  ✅ Vector store cleared.", style="green")
    except Exception as e:
        console.print(f"  ⚠️  Collection not found or already empty: {e}", style="yellow")

    # Clear hash cache
    try:
        cache_path = get_settings().hash_cache_path
        if cache_path.exists():
            cache_path.unlink()
            console.print("  ✅ Hash cache cleared.", style="green")
    except Exception as e:
        console.print(f"  ⚠️  Failed to clear hash cache: {e}", style="yellow")

    console.print(
        Panel("✨ Knowledge base purged successfully.", style="green")
    )


if __name__ == "__main__":
    cli()
