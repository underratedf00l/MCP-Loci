import os
import sqlite3
from typing import Optional

DEFAULT_DB_PATH = os.path.expanduser(
    os.environ.get("MCP_MEMORY_DB_PATH", "~/.claude/memory.db")
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id            TEXT PRIMARY KEY,
    name          TEXT UNIQUE NOT NULL,
    type          TEXT NOT NULL CHECK(type IN ('user','feedback','project','reference','insight')),
    description   TEXT NOT NULL,
    content       TEXT NOT NULL,
    evidence      TEXT,
    session_hint  TEXT,
    status        TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','pending','archived')),
    pinned        INTEGER NOT NULL DEFAULT 0,
    supersedes_id TEXT REFERENCES memories(id),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_accessed TEXT,
    use_count     INTEGER NOT NULL DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
    USING fts5(name, description, content, content='memories', content_rowid='rowid');

CREATE TABLE IF NOT EXISTS embeddings (
    id          TEXT PRIMARY KEY,
    memory_id   TEXT NOT NULL REFERENCES memories(id),
    vector      BLOB NOT NULL,
    model_name  TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationships (
    id            TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL REFERENCES memories(id),
    target_id     TEXT NOT NULL REFERENCES memories(id),
    relation_type TEXT NOT NULL CHECK(relation_type IN (
        'supports','contradicts','updates','derived_from','related_to'
    )),
    weight        REAL NOT NULL DEFAULT 0.5,
    created_at    TEXT NOT NULL,
    UNIQUE(source_id, target_id, relation_type)
);

CREATE TABLE IF NOT EXISTS conflicts (
    id               TEXT PRIMARY KEY,
    memory_id_a      TEXT NOT NULL REFERENCES memories(id),
    memory_id_b      TEXT NOT NULL REFERENCES memories(id),
    similarity_score REAL NOT NULL,
    resolved         INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS syntheses (
    id                TEXT PRIMARY KEY,
    scope             TEXT NOT NULL,
    portrait          TEXT NOT NULL,
    changes           TEXT NOT NULL,
    uncertainties     TEXT NOT NULL,
    recommendations   TEXT NOT NULL,
    memories_included INTEGER NOT NULL,
    model_used        TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS memories_fts_insert
    AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, name, description, content)
    VALUES (new.rowid, new.name, new.description, new.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_fts_delete
    AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, name, description, content)
    VALUES ('delete', old.rowid, old.name, old.description, old.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_fts_update
    AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, name, description, content)
    VALUES ('delete', old.rowid, old.name, old.description, old.content);
    INSERT INTO memories_fts(rowid, name, description, content)
    VALUES (new.rowid, new.name, new.description, new.content);
END;
"""

_connection: Optional[sqlite3.Connection] = None
_db_path: str = DEFAULT_DB_PATH


def configure(db_path: str) -> None:
    """Override the database path. Call before first get_conn()."""
    global _db_path, _connection
    _db_path = db_path
    _connection = None


def reset() -> None:
    """Close and reset the connection (used in tests)."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None


def init_db(db_path: str) -> sqlite3.Connection:
    """Initialize database, create tables, return connection."""
    if db_path != ":memory:":
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def get_conn() -> sqlite3.Connection:
    """Return the singleton connection, initializing if needed."""
    global _connection
    if _connection is None:
        _connection = init_db(_db_path)
    return _connection
