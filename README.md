# generalStore — The Local Knowledge Engine 🧠

A Python-native CLI tool and Model Context Protocol (MCP) server that indexes your study materials (PDFs, Word docs, PowerPoints, spreadsheets) into a local vector database. It allows you to search your notes from the command line, watch for file changes in real-time, and lets an LLM (like Claude) query your exact local files with source citations.

---

## 🌟 Core Features

- **Local Vector Database**: Powered by **ChromaDB** and `sentence-transformers` (`all-MiniLM-L6-v2`), keeping all your data local and secure.
- **Smart Parsers**: Dedicated, semantic parsers for `.pdf`, `.docx`, `.pptx`, and `.xlsx` files that retain document structure (headings, slides, tables).
- **Asynchronous Ingestion**: Built with `asyncio`, utilizing thread pools and queues to process hundreds of files quickly without blocking.
- **File System Watchdog**: A background daemon that monitors your data directories, automatically indexing new files or surgically deleting removed files.
- **Intelligent Caching**: Uses SHA-256 content hashing to ensure files are only re-indexed if their content actually changes.
- **FastMCP Server**: Exposes your local knowledge base to LLMs using the official Model Context Protocol (v3).
- **Beautiful CLI**: A terminal interface built with Click and Rich for stunning progress bars and formatted outputs.
- **Security Guard**: Strict path-traversal protection ensures the engine can only read from explicitly allowed directories.

---

## 🛠️ Project Architecture & Components

The system is built on a modular, multi-phase architecture:

### 1. Foundation & Configuration (`config.py`)
Uses `pydantic-settings` to load configuration from a `.env` file. It manages:
- `WATCHED_DIRS`: The absolute path to your study materials (e.g., `Data/`).
- `CHROMA_DB_PATH`: Where the local vector database is stored.
- `EMBEDDING_MODEL`: The HuggingFace model used for vector embeddings.

### 2. Semantic Parsers (`parsers/`)
The heavy lifting of data extraction. All parsers inherit from an abstract `BaseParser` and output standardized `DocumentChunk` dataclasses containing the text and rich metadata (page numbers, slide numbers, headings).
- **PDF Parser (`pdf_parser.py`)**: Uses PyMuPDF (`fitz`) for *block-level* extraction rather than raw page text. Groups blocks into semantic paragraphs. Gracefully handles handwritten scans by skipping non-extractable pages.
- **DOCX Parser (`docx_parser.py`)**: Uses `python-docx`. Chunks text based on heading hierarchy (H1/H2). It also seamlessly serializes Word tables into Markdown format to preserve tabular context.
- **PPTX Parser (`pptx_parser.py`)**: Uses `python-pptx`. Treats one slide as one chunk, prepending the slide title to the text frames so context isn't lost.
- **XLSX Parser (`xlsx_parser.py`)**: Uses `pandas`. Iterates through sheets and serializes rows into a `Column: Value | Column: Value` Markdown string, preserving tabular data perfectly for LLM context.

### 3. Vector Store (`vectorstore/store.py`)
Interfaces with the persistent ChromaDB client.
- Automatically handles batching (upserting 100 chunks at a time).
- Uses stable document IDs derived from `hash(filepath + chunk_index)` to ensure idempotent updates.
- Supports filtering by file type during semantic search queries.

### 4. Ingestion Engine & Watchdog (`ingestion/`)
- **Hasher (`hasher.py`)**: Maintains a JSON cache of file SHA-256 hashes. It prevents redundant parsing of unchanged files.
- **Engine (`engine.py`)**: The central coordinator. Implements an `asyncio.Queue` and workers that route files to the correct parser, update hashes, and upsert to the vector store. Captures errors per-file so one corrupted PDF won't crash the whole pipeline.
- **Watcher (`watcher.py`)**: Uses `watchdog` to monitor the filesystem. It debounces rapid-fire events (like saving a document) with a 2-second cooldown and triggers surgical additions or deletions in the vector store.

### 5. FastMCP Server (`server/mcp_server.py`)
Exposes strictly read-only tools to LLMs via the Model Context Protocol:
- `query_knowledge`: Semantic search over indexed materials. Returns formatted text chunks, source file paths, and relevance scores.
- `list_indexed_files`: Lists all files in the knowledge base, grouped by subject folder.
- `get_index_stats`: Returns metrics like total chunks, total files, and a breakdown by file type.

### 6. Security Boundary (`security/guard.py`)
A critical component since this grants AI access to the local machine. The guard:
- Resolves paths and verifies they reside strictly within `WATCHED_DIRS`.
- Blocks path traversal attacks (`../`), absolute path escapes, and symlink exploits.
- Ensures all MCP tools remain strictly read-only.

---

## 🚀 Installation & Setup

### Prerequisites
- Python 3.10+
- macOS/Linux/Windows

### 1. Clone & Environment Setup
```bash
git clone https://github.com/Itsharshitgoat/generalStore.git
cd generalStore
python3 -m venv venv
source venv/bin/activate
```

### 2. Configuration
Create a `.env` file in the root directory:
```env
WATCHED_DIRS=/path/to/your/Data
CHROMA_DB_PATH=/path/to/your/chroma_db
EMBEDDING_MODEL=all-MiniLM-L6-v2
```

### 3. Install the Package
Install `generalStore` and all its dependencies (ChromaDB, PyTorch, PyMuPDF, Pandas, FastMCP, etc.):
```bash
pip install -e "."
```

### 4. (Optional) Set up an Alias
To use the command globally without activating the virtual environment every time, add this to your `~/.zshrc` or `~/.bashrc`:
```bash
alias generalstore="/path/to/generalStore/venv/bin/generalstore"
```

---

## 💻 Command Line Interface (CLI)

The beautiful CLI is built with `Click` and `Rich`.

### `generalstore index`
Performs a full bulk ingestion of your `Data/` directory. Reads the hash cache to skip unchanged files, parses new/modified files, and shows a beautiful progress bar.
```bash
generalstore index
```

### `generalstore search`
Perform a semantic search directly from your terminal. Returns the top matching chunks with citations.
```bash
generalstore search "Dijkstra algorithm"
```

### `generalstore watch`
Starts the Watchdog daemon in the foreground. It actively monitors your files and updates the index the moment you hit "Save".
```bash
generalstore watch
```

### `generalstore status`
Shows detailed statistics about your knowledge base, including the total number of chunks, files, and a breakdown by file type (PDFs, Word docs, etc.).
```bash
generalstore status
```

### `generalstore purge`
Clears the entire vector store and deletes the hash cache. Useful if you want to start completely fresh.
```bash
generalstore purge
```

### `generalstore serve`
Starts the MCP Server using STDIO transport. This command is primarily used by LLM clients (like Claude Desktop) to connect to the engine.
```bash
generalstore serve
```

---

## 🤖 Connecting to Claude Desktop (MCP)

You can give Claude Desktop direct access to your local knowledge base by adding `generalstore` to its MCP configuration.

1. Open your Claude Desktop config file (usually at `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS).
2. Add the following entry:

```json
{
  "mcpServers": {
    "generalStore": {
      "command": "/absolute/path/to/generalStore/venv/bin/generalstore",
      "args": ["serve"]
    }
  }
}
```
3. Restart Claude Desktop.
4. You can now prompt Claude: *"Use the generalStore search_knowledge tool to look up my notes on Machine Learning..."*

---

## 📂 Project Structure

```text
generalStore/
├── Data/                          # Your study materials (PDFs, PPTXs, etc.)
├── generalstore/                  # Main Python package
│   ├── cli.py                     # Click CLI entry point
│   ├── config.py                  # Pydantic settings & path config
│   ├── parsers/                   # File type parsers (base, pdf, docx, pptx, xlsx)
│   ├── ingestion/                 # engine.py, hasher.py, watcher.py
│   ├── vectorstore/               # ChromaDB interface (store.py)
│   ├── server/                    # FastMCP server (mcp_server.py)
│   └── security/                  # Path traversal protection (guard.py)
├── chroma_db/                     # Persistent vector storage
├── pyproject.toml                 # Dependencies & project metadata
└── .env                           # Local configuration
```

---

*Built for robust local context extraction. Feed it your messy notes; it returns structured, searchable knowledge.*
