# mcp-loci

**Persistent, searchable memory for Claude.** Built on SQLite with FTS5 full-text search and local semantic vector embeddings — no API costs, no external dependencies.

## What it does

mcp-loci gives Claude three tools:

| Tool | What it does |
|------|-------------|
| `remember` | Store or update a named memory with type, description, content, and optional evidence |
| `recall` | Search memories by keyword + semantic similarity, ranked by relevance and recency |
| `forget` | Soft-delete a memory by name or ID (pinned memories require `force=True`) |

## Quick start

```bash
cd ~/mcp-loci
pip install -e ".[dev]"
```

Then add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memory": {
      "command": "/usr/local/bin/python3",
      "args": ["-m", "mcp_loci.server"],
      "cwd": "/Users/your-username/mcp-loci"
    }
  }
}
```

Restart Claude. The `remember`, `recall`, and `forget` tools will appear automatically.

## Architecture

```
memories (SQLite)
    ├── memories_fts (FTS5 virtual table — keyword search)
    ├── embeddings (384-dim vectors — semantic search)
    ├── relationships (links between memories)
    ├── conflicts (flagged contradictions)
    └── syntheses (cross-memory synthesis cache)
```

Search is hybrid by default: FTS5 keyword match + cosine similarity on local embeddings, merged and ranked (60% semantic weight, 40% confidence/recency weight).

## Status

Phase 2 active — semantic embeddings live, 20 unit tests passing, Claude integration confirmed.
