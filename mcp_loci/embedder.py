import sqlite3
import numpy as np
from typing import Optional
import uuid
from datetime import datetime, timezone

_model = None
_model_name = 'all-MiniLM-L6-v2'
_embedding_dim = 384

def _load_model():
    global _model
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_model_name)
        return _model
    except Exception:
        return None

def embed_text(text: str) -> Optional[np.ndarray]:
    if not text:
        return None
    try:
        model = _model or _load_model()
        if model is None:
            return None
        embedding = model.encode(text)
        normalized = embedding / (np.linalg.norm(embedding) + 1e-8)
        return normalized.astype(np.float32)
    except Exception:
        return None

def embed_and_store(memory_id: str, text: str, conn: sqlite3.Connection) -> None:
    try:
        vector = embed_text(text)
        if vector is None:
            return
        vector_blob = vector.tobytes()
        embedding_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            'INSERT OR REPLACE INTO embeddings (id, memory_id, vector, model_name, dim, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (embedding_id, memory_id, vector_blob, _model_name, _embedding_dim, now)
        )
        conn.commit()
    except Exception as e:
        import logging
        logging.warning(f'Failed to embed memory {memory_id}: {e}')
