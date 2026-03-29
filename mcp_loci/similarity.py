import sqlite3
import numpy as np
from typing import Optional, List
from mcp_loci.embedder import embed_text

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        return 0.0
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))

def semantic_search(query: str, conn: sqlite3.Connection, limit: int = 10, min_similarity: float = 0.2) -> List[dict]:
    try:
        query_vector = embed_text(query)
        if query_vector is None:
            return []
        
        if conn.row_factory is None:
            conn.row_factory = sqlite3.Row
        
        cursor = conn.execute('SELECT memory_id, vector FROM embeddings')
        results = []
        for row in cursor.fetchall():
            memory_id = row[0] if isinstance(row, tuple) else row['memory_id']
            vector_blob = row[1] if isinstance(row, tuple) else row['vector']
            stored_vector = np.frombuffer(vector_blob, dtype=np.float32)
            similarity = cosine_similarity(query_vector, stored_vector)
            if similarity >= min_similarity:
                memory_row = conn.execute('SELECT * FROM memories WHERE id = ? AND status = ?', (memory_id, 'active')).fetchone()
                if memory_row:
                    results.append({
                        'memory_id': memory_id,
                        'name': memory_row['name'],
                        'type': memory_row['type'],
                        'description': memory_row['description'],
                        'content': memory_row['content'],
                        'evidence': memory_row['evidence'],
                        'similarity': similarity,
                        'use_count': memory_row['use_count'],
                        'updated_at': memory_row['updated_at'],
                        'created_at': memory_row['created_at'],
                    })
        results.sort(key=lambda x: x['similarity'], reverse=True)
        return results[:limit]
    except Exception as e:
        import sys
        print(f'semantic_search error: {e}', file=sys.stderr)
        return []
