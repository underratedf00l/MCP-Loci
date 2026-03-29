# MCP-Loci

**Your AI forgets everything between sessions. Loci fixes that.**

Loci is a persistent memory server for [Model Context Protocol](https://modelcontextprotocol.io) — giving Claude and other MCP-compatible assistants a cross-session memory that actually works. Store facts, recall them semantically, and synthesize a full portrait of everything your AI knows.

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![FastMCP](https://img.shields.io/badge/built%20with-FastMCP%203.x-purple)](https://gofastmcp.com)

---

## What it does

| Tool | Description |
|---|---|
| `remember` | Store a named memory with type, description, and content |
| `recall` | Search memories — keyword, semantic, or hybrid |
| `forget` | Remove a memory by name or ID |
| `synthesize` | Cross-memory portrait: changes, uncertainties, and recommendations |
| `health` | Server status: memory count, embedding count, model state |

Hybrid search combines BM25 keyword matching (SQLite FTS5) with local semantic embeddings (`all-MiniLM-L6-v2`) — so recall works whether you remember the exact word or just the general idea.

---

## Quickstart

### Install

```bash
pip install mcp-loci
```

Or with semantic search support (recommended):

```bash
pip install "mcp-loci[embeddings]"
```

### Add to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memory": {
      "command": "python",
      "args": ["-m", "mcp_loci.server"]
    }
  }
}
```

Restart Claude Desktop. That's it — Claude now has persistent memory across sessions.

### Try it

In Claude:

> "Remember that my preferred code style is no trailing commas and 4-space indentation."

Close Claude. Reopen it.

> "What's my preferred code style?"

Claude remembers.

---

## Memory types

Use types to organize and filter your memories:

| Type | Use for |
|---|---|
| `user` | Personal preferences, identity, background |
| `feedback` | Things Claude should always/never do |
| `project` | Active work context, goals, deadlines |
| `reference` | Where to find things — links, locations, sources |

---

## Semantic search

Install with `[embeddings]` to enable semantic recall. The first call loads the model (~90MB, local — no API key needed). Subsequent calls use a background-warmed model for instant response.

```
recall("what does adam prefer for formatting")
# → surfaces "preferred code style" memory even without exact keyword match
```

Set `semantic: false` for pure keyword search, or leave it on for hybrid (default).

---

## Configuration

| Env var | Default | Description |
|---|---|---|
| `MCP_MEMORY_DB_PATH` | `~/.mcp-loci/memory.db` | SQLite database location |

---

## Development

```bash
git clone https://github.com/underratedf00l/MCP-Loci
cd MCP-Loci
pip install -e ".[dev,embeddings]"
pytest
```

28 tests (24 unit + 4 integration). All green.

---

## Why "Loci"?

The [method of loci](https://en.wikipedia.org/wiki/Method_of_loci) is a 2,500-year-old memorization technique — you place memories in specific locations in an imagined space so you can walk back and find them. That's exactly what this does, at the data layer.

---

## License

MIT — see [LICENSE](LICENSE).
