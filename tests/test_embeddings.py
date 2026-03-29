import sqlite3
import numpy as np
from unittest.mock import patch
import pytest
from mcp_loci import embedder
from mcp_loci.similarity import cosine_similarity


def test_cosine_similarity_identical():
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-6)


def test_cosine_similarity_orthogonal():
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-6)


def test_cosine_similarity_normalized():
    a = np.array([0.6, 0.8, 0.0], dtype=np.float32)
    b = np.array([0.8, 0.6, 0.0], dtype=np.float32)
    result = cosine_similarity(a, b)
    assert 0.0 <= result <= 1.0


def test_embed_and_store_roundtrip():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE embeddings "
        "(id TEXT PRIMARY KEY, memory_id TEXT NOT NULL, vector BLOB NOT NULL, "
        "model_name TEXT NOT NULL, dim INTEGER NOT NULL, created_at TEXT NOT NULL)"
    )
    memory_id = "test-mem-123"
    test_vector = np.array([0.1, 0.2, 0.3, 0.4, 0.5], dtype=np.float32)
    with patch("mcp_loci.embedder.embed_text") as mock_embed:
        mock_embed.return_value = test_vector
        embedder.embed_and_store(memory_id, "test text", conn)
    stored = conn.execute(
        "SELECT vector FROM embeddings WHERE memory_id = ?", (memory_id,)
    ).fetchone()
    assert stored is not None
    stored_vector = np.frombuffer(stored["vector"], dtype=np.float32)
    assert np.allclose(stored_vector, test_vector)
