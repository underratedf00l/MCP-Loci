# mcp-loci — Technical Specification

**Version:** 0.2.0 (Phase 4 — Synthesis Tool)  
**Status:** Active development  
**License:** MIT  
**Python:** ≥3.11

---

## Overview

mcp-loci is a FastMCP server that provides Claude with persistent, cross-session memory backed by SQLite. It exposes four MCP tools and uses a hybrid search architecture combining BM25 full-text search with local sentence-transformer embeddings.

---

## Architecture

### Storage

| Layer | Technology | Purpose |
|-------|-----------|---------| 
| Primary store | SQLite (WAL mode) | Memory records, metadata, relationships |
| Full-text index | FTS5 virtual table | BM25 keyword search |
| Vector store | SQLite BLOB column | 384-dim float32 embeddings |
| Synthesis cache | SQLite table | Cross-memory synthesis output |
| Config | Environment variable | `MCP_MEMORY_DB_PATH` (default: `~/.claude/memory.db`) |

### Search Pipeline

```
query string
    │
    ├─► FTS5 BM25 search ──────────────────────────┐
    │                                               │
    └─► all-MiniLM-L6-v2 embed ──► cosine rank ────┤
                                                    │
                                        merge + dedupe by memory_id
                                                    │
                                        score = 0.6 × semantic + 0.4 × confidence
                                                    │
                                        sort descending, apply limit
```

### Confidence Score

```
confidence = 0.6 × exp(−days_since_update / 90) + 0.4 × min(use_count / 10, 1.0)
```

Pinned memories always return `confidence = 1.0`.

---

## Database Schema

### `memories`

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | UUID |
| `name` | TEXT UNIQUE | Human-readable identifier (upsert key) |
| `type` | TEXT | Category: `user`, `feedback`, `project`, `reference`, `insight` |
| `description` | TEXT | One-line summary |
| `content` | TEXT | Full memory body |
| `evidence` | TEXT | Optional supporting evidence |
| `session_hint` | TEXT | Originating session context |
| `status` | TEXT | `active` or `archived` (soft delete) |
| `pinned` | INTEGER | 0/1 — pinned memories require `force=True` to forget |
| `use_count` | INTEGER | Incremented on each `recall` hit |
| `created_at` | TEXT | ISO UTC timestamp |
| `updated_at` | TEXT | ISO UTC timestamp |

### `memories_fts`

FTS5 virtual table (content table pattern). Indexes `name`, `description`, `content`. Kept in sync via 3 triggers: `memories_ai`, `memories_ad`, `memories_au`.

### `embeddings`

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | UUID |
| `memory_id` | TEXT FK | References `memories(id)` ON DELETE CASCADE |
| `vector` | BLOB | `numpy.ndarray.tobytes()` — float32, 384-dim |
| `model_name` | TEXT | Embedding model used (e.g., `all-MiniLM-L6-v2`) |
| `dim` | INTEGER | Vector dimension |
| `created_at` | TEXT | ISO UTC timestamp |

### `relationships`

Links between memories. `source_id → target_id` with a `relation_type` label.

### `conflicts`

Flagged contradictions between memories, detected at write time by keyword overlap.

### `syntheses`

Cache table for cross-memory synthesis output.

| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | UUID |
| `scope` | TEXT | Search scope (e.g., "project", "user") |
| `portrait` | TEXT | Narrative summary of memory state |
| `changes` | TEXT | JSON array of recent updates |
| `uncertainties` | TEXT | JSON array of uncertain items |
| `recommendations` | TEXT | JSON array of suggested actions |
| `memories_included` | INTEGER | Count of memories included |
| `model_used` | TEXT | Synthesis engine version |
| `created_at` | TEXT | ISO UTC timestamp |

---

## MCP Tools

### `remember`

Store or update a named memory.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `name` | str | ✓ | Unique identifier. Upserts on match. |
| `type` | str | ✓ | `user`, `feedback`, `project`, `reference`, or `insight` |
| `description` | str | ✓ | One-line summary |
| `content` | str | ✓ | Full body text |
| `evidence` | str | — | Supporting evidence |
| `session_hint` | str | — | Context about originating session |
| `related_to` | list[str] | — | Names of related memories |
| `pinned` | bool | — | Pin to prevent accidental deletion (default: False) |

**Returns:**

```json
{
  "stored": true,
  "id": "uuid-string",
  "action": "created",
  "conflicts": []
}
```

**Side effects:**
- Inserts or updates FTS5 index (via trigger)
- Embeds `{name} {description} {content}` and stores in `embeddings` table (gracefully skipped on failure)

---

### `recall`

Search memories by relevance.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `query` | str | ✓ | Search string |
| `type_filter` | str | — | Filter by memory type |
| `limit` | int | — | Max results (default: 10) |
| `include_stale` | bool | — | Include archived memories (default: False) |
| `min_confidence` | float | — | Minimum confidence threshold (default: 0.0) |
| `semantic` | bool | — | Enable hybrid search (default: True) |

**Returns:** Array of memory objects:

```json
[
  {
    "id": "uuid",
    "name": "example_memory",
    "type": "project",
    "description": "A test memory",
    "content": "Full body text...",
    "confidence_score": 0.87,
    "match_reason": "keyword match + high use count",
    "use_count": 5,
    "last_accessed": "2026-03-29T12:00:00+00:00",
    "created_at": "2026-03-28T10:00:00+00:00",
    "updated_at": "2026-03-29T11:00:00+00:00",
    "pinned": false,
    "search_mode": "hybrid"
  }
]
```

**`search_mode` values:** `"fts"`, `"semantic"`, `"hybrid"`

---

### `forget`

Soft-delete a memory.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `memory_id_or_name` | str | ✓ | ID or name |
| `force` | bool | — | Required to forget pinned memories (default: False) |

**Returns:**

```json
{
  "archived": true,
  "was_pinned": false,
  "reason": "archived by request"
}
```

---

### `synthesize`

Cross-memory synthesis: portrait, changes, uncertainties, and recommendations.

**Parameters:**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `scope` | str | — | Search scope (e.g., "project", "user") |
| `type_filter` | str | — | Filter by memory type |
| `min_confidence` | float | — | Minimum confidence threshold (default: 0.3) |
| `max_memories` | int | — | Max memories to analyze (default: 20) |
| `force_refresh` | bool | — | Bypass cache and regenerate (default: False) |

**Returns:**

```json
{
  "scope": "project",
  "portrait": "Current state: 2 project memories. Key memories: project_alpha, project_beta.",
  "changes": [
    "project_alpha: updated 1 days ago",
    "project_beta: updated 3 days ago"
  ],
  "uncertainties": [
    {
      "name": "project_alpha",
      "excerpt": "moving to phase 2 is pending approval"
    }
  ],
  "recommendations": [
    "Reconnect or reference: stale_project — this memory has never been recalled",
    "Update or verify: old_project — confidence is declining",
    "Document relationships: Connect project_alpha to other project memories"
  ],
  "memories_included": ["project_alpha", "project_beta"],
  "memory_count": 2,
  "cached": false,
  "created_at": "2026-03-29T12:00:00+00:00"
}
```

**Caching:** If a synthesis for the same `scope` exists within 24 hours, it is returned with `cached: true`. Pass `force_refresh=True` to bypass cache.

---

### `health`

Return server status — database connectivity, memory counts, and embedding model state.

**Parameters:** None

**Returns:**

```json
{
  "healthy": true,
  "db_path": "~/.claude/memory.db",
  "memories_active": 12,
  "memories_pinned": 3,
  "embeddings_stored": 12,
  "syntheses_cached": 2,
  "embedding_model_loaded": true,
  "embedding_model_name": "all-MiniLM-L6-v2",
  "has_embeddings": true
}
```

Use `health` to verify the server is running correctly after installation or restart.


---

## Embedding Model

| Property | Value |
|----------|-------|
| Model | `sentence-transformers/all-MiniLM-L6-v2` |
| Dimensions | 384 |
| Download size | ~90 MB |
| Inference | Local CPU (no API, no cost) |
| Normalization | L2 (unit vectors) |
| Loaded | Lazy on first `remember` — pre-warmed in background thread on server startup |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_MEMORY_DB_PATH` | `~/.claude/memory.db` | SQLite database path |

---

## Project Structure

```
mcp-loci/
├── mcp_loci/
│   ├── __init__.py       # Package docstring
│   ├── server.py         # FastMCP app, 4 MCP tools
│   ├── db.py             # Schema, connection management, FTS triggers
│   ├── confidence.py     # Confidence score formula, match_reason
│   ├── embedder.py       # sentence-transformers integration, embed_and_store
│   └── similarity.py     # cosine_similarity, semantic_search
├── tests/
│   ├── test_remember.py  # 4 tests
│   ├── test_recall.py    # 6 tests
│   ├── test_forget.py    # 6 tests
│   ├── test_embeddings.py # 4 tests
│   ├── test_synthesize.py # 4 tests
│   └── test_integration.py # 4 integration tests (wire protocol)
├── docs/
│   ├── index.md
│   ├── spec.md           # This document
│   ├── roadmap.md
│   └── api/
│       ├── tools.md
│       ├── db.md
│       ├── embeddings.md
│       └── confidence.md
├── .github/
│   └── workflows/
│       └── ci.yml        # GitHub Actions CI
├── mkdocs.yml
├── pyproject.toml
├── LICENSE
├── README.md
└── .gitignore
```

---

## Test Coverage

| File | Tests | Coverage |
|------|-------|----------|
| `test_remember.py` | 4 | Create, upsert, pin, conflict detection |
| `test_recall.py` | 6 | Keyword search, type filter, stale filter, use_count increment, match_reason |
| `test_forget.py` | 6 | Soft delete, pin guard, force flag, by-name, post-delete recall, not-found |
| `test_embeddings.py` | 4 | Cosine similarity (identical/orthogonal/range), embed-and-store roundtrip |
| `test_synthesize.py` | 4 | Portrait generation, caching, force refresh, uncertainty detection |
| `test_integration.py` | 4 | Server startup, health check, remember→recall roundtrip, forget end-to-end |
| **Total** | **28** | **All passing** |

---

## Roadmap

### Phase 3 ✅ — MCP Registration

Claude desktop integration via `claude_desktop_config.json`. Live as of March 2026.

### Phase 4 ✅ — Synthesis Tool

Implement `synthesize` MCP tool using the `syntheses` table. Cross-memory portrait generation, change tracking, uncertainty surfacing, recommendations. Complete as of March 2026.

### Phase 5 — Open Source

README, MIT license, GitHub repo, GitHub Actions CI, GitHub Pages docs. In progress.
