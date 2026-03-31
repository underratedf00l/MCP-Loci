import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
import json
from fastmcp import FastMCP
from mcp_loci import db as db_module
from mcp_loci.confidence import confidence_score, match_reason

try:
    from mcp_loci.embedder import embed_and_store
    from mcp_loci.similarity import semantic_search
    HAS_EMBEDDINGS = True
except ImportError:
    HAS_EMBEDDINGS = False
    embed_and_store = None
    semantic_search = None

mcp = FastMCP("memory")
DB_PATH = os.path.expanduser(os.environ.get("MCP_MEMORY_DB_PATH", "~/.claude/memory.db"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_fts_query(query: str) -> str:
    """Convert a natural-language query into a safe FTS5 query string.

    FTS5 interprets ':' as a column filter, '-' as NOT, etc.
    Quoting each token forces literal matching and prevents parse errors
    like 'no such column: pager'.
    """
    import re
    tokens = re.split(r'[\s\-_/:;,]+', query.strip())
    tokens = [t.strip('"\'.!?()[]{}') for t in tokens]
    tokens = [t for t in tokens if len(t) >= 2]
    if not tokens:
        return f'"{query}"'
    return " ".join(f'"{t}"' for t in tokens)


def _get_keywords(text: str, n: int = 3) -> list[str]:
    stop = {"this", "that", "with", "from", "have", "been", "they", "will", "your", "their", "what", "when", "where", "which", "would", "could", "should", "about", "there", "into", "than", "then", "some", "more", "also", "just", "like", "very", "over", "such"}
    words = [w.lower().strip(".,!?;:\"'") for w in text.split()]
    seen = set()
    result = []
    for w in words:
        if len(w) > 3 and w not in stop and w not in seen:
            seen.add(w)
            result.append(w)
            if len(result) >= n:
                break
    return result


@mcp.tool()
def remember(name: str, type: str, description: str, content: str, evidence: Optional[str] = None, session_hint: Optional[str] = None, related_to: Optional[list[str]] = None, pinned: bool = False) -> dict:
    """Store or update a named memory with type, description, and content."""
    conn = db_module.get_conn()
    now = _now_iso()
    
    existing = conn.execute("SELECT id, rowid FROM memories WHERE name = ?", (name,)).fetchone()
    
    if existing:
        memory_id = existing["id"]
        exclude_rowid = existing["rowid"]
        conn.execute("UPDATE memories SET type=?, description=?, content=?, evidence=?, session_hint=?, pinned=?, updated_at=? WHERE id=?", (type, description, content, evidence, session_hint, 1 if pinned else 0, now, memory_id))
        conn.commit()
        action = "updated"
    else:
        memory_id = str(uuid.uuid4())
        conn.execute("INSERT INTO memories (id, name, type, description, content, evidence, session_hint, pinned, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (memory_id, name, type, description, content, evidence, session_hint, 1 if pinned else 0, now, now))
        conn.commit()
        new_row = conn.execute("SELECT rowid FROM memories WHERE id=?", (memory_id,)).fetchone()
        exclude_rowid = new_row["rowid"] if new_row else -1
        action = "created"
    
    if HAS_EMBEDDINGS and embed_and_store:
        try:
            combined_text = f"{name} {description} {content}"
            embed_and_store(memory_id, combined_text, conn)
        except Exception as e:
            import logging
            logging.warning(f"Failed to embed memory {memory_id}: {e}")
    
    keywords = _get_keywords(f"{name} {description}")
    conflicts = []
    if keywords:
        fts_query = " OR ".join(keywords)
        try:
            rows = conn.execute("SELECT m.id, m.name, m.description FROM memories_fts fts JOIN memories m ON fts.rowid = m.rowid WHERE memories_fts MATCH ? AND fts.rowid != ? AND m.status = 'active' LIMIT 3", (fts_query, exclude_rowid)).fetchall()
            conflicts = [{"id": r["id"], "name": r["name"], "description": r["description"]} for r in rows]
        except Exception:
            pass
    
    return {"stored": True, "id": memory_id, "action": action, "conflicts": conflicts}


@mcp.tool()
def recall(query: str, type_filter: Optional[str] = None, limit: int = 10, include_stale: bool = False, min_confidence: float = 0.0, semantic: bool = True) -> list[dict]:
    """Hybrid keyword + semantic search across memories, ranked by relevance and confidence."""
    conn = db_module.get_conn()
    now = _now_iso()
    
    type_clause = "AND m.type = ?" if type_filter else ""
    safe_query = _sanitize_fts_query(query)
    params = [safe_query]
    if type_filter:
        params.append(type_filter)
    params.append(limit * 3)
    
    # FTS search
    fts_rows = conn.execute(f"""SELECT m.id, m.name, m.type, m.description, m.content, m.evidence, m.pinned, m.use_count, m.last_accessed, m.created_at, m.updated_at, fts.rank AS bm25_rank FROM memories_fts fts JOIN memories m ON fts.rowid = m.rowid WHERE memories_fts MATCH ? AND m.status = 'active' {type_clause} ORDER BY fts.rank LIMIT ?""", params).fetchall()
    
    fts_results = {}
    for row in fts_rows:
        conf = confidence_score(pinned=bool(row["pinned"]), updated_at=row["updated_at"], use_count=row["use_count"])
        if not include_stale and conf < 0.3 and not row["pinned"]:
            continue
        if conf < min_confidence:
            continue
        bm25 = -(row["bm25_rank"] or 0.0)
        score = bm25 * conf
        reason = match_reason(similarity=None, confidence=conf, pinned=bool(row["pinned"]), keyword_match=True, use_count=row["use_count"])
        fts_results[row["id"]] = {
            "id": row["id"],
            "name": row["name"],
            "type": row["type"],
            "description": row["description"],
            "content": row["content"],
            "evidence": row["evidence"],
            "confidence_score": conf,
            "match_reason": reason,
            "use_count": row["use_count"],
            "last_accessed": row["last_accessed"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "pinned": bool(row["pinned"]),
            "_score": score,
            "search_mode": "fts"
        }
    
    # Semantic search
    semantic_results = {}
    if semantic and HAS_EMBEDDINGS and semantic_search:
        try:
            sem_rows = semantic_search(query, conn, limit=limit * 3, min_similarity=0.2)
            for row in sem_rows:
                if type_filter and row["type"] != type_filter:
                    continue
                conf = confidence_score(pinned=False, updated_at=row.get("updated_at") or now, use_count=row.get("use_count", 0))
                if not include_stale and conf < 0.3:
                    continue
                if conf < min_confidence:
                    continue
                semantic_score = row["similarity"]
                combined_score = 0.6 * semantic_score + 0.4 * conf
                semantic_results[row["memory_id"]] = {
                    "id": row["memory_id"],
                    "name": row["name"],
                    "type": row["type"],
                    "description": row["description"],
                    "content": row["content"],
                    "evidence": row["evidence"],
                    "confidence_score": conf,
                    "match_reason": f"semantic (sim: {semantic_score:.2f})",
                    "use_count": 0,
                    "last_accessed": None,
                    "created_at": now,
                    "updated_at": now,
                    "pinned": False,
                    "_score": combined_score,
                    "search_mode": "semantic"
                }
        except Exception as e:
            import logging
            logging.warning(f"Semantic search failed: {e}")
    
    # Merge results
    merged = {}
    for mid, result in fts_results.items():
        merged[mid] = result
    for mid, result in semantic_results.items():
        if mid in merged:
            merged[mid]["search_mode"] = "hybrid"
            merged[mid]["_score"] = (merged[mid]["_score"] + result["_score"]) / 2
        else:
            merged[mid] = result
    
    results = sorted(merged.values(), key=lambda r: r["_score"], reverse=True)
    results = results[:limit]
    
    final = []
    for r in results:
        r.pop("_score")
        try:
            conn.execute("UPDATE memories SET use_count = use_count + 1, last_accessed = ? WHERE id = ?", (now, r["id"]))
        except Exception:
            pass
        final.append(r)
    
    conn.commit()
    return final


@mcp.tool()
def forget(memory_id_or_name: str, force: bool = False) -> dict:
    """Soft-delete a memory by ID or name. Pinned memories require force=True."""
    conn = db_module.get_conn()
    
    row = conn.execute("SELECT id, pinned FROM memories WHERE id = ?", (memory_id_or_name,)).fetchone()
    if row is None:
        row = conn.execute("SELECT id, pinned FROM memories WHERE name = ?", (memory_id_or_name,)).fetchone()
    
    if row is None:
        return {"archived": False, "was_pinned": False, "reason": "not found"}
    
    was_pinned = bool(row["pinned"])
    if was_pinned and not force:
        return {"archived": False, "was_pinned": True, "reason": "memory is pinned — use force=True to archive"}
    
    conn.execute("UPDATE memories SET status='archived', updated_at=? WHERE id=?", (_now_iso(), row["id"]))
    conn.commit()
    
    return {"archived": True, "was_pinned": was_pinned, "reason": None}


@mcp.tool()
def synthesize(
    scope: Optional[str] = None,
    type_filter: Optional[str] = None,
    min_confidence: float = 0.3,
    max_memories: int = 20,
    force_refresh: bool = False
) -> dict:
    """
    Cross-memory synthesis: portrait, changes, uncertainties, recommendations.
    
    Returns cached synthesis if one exists within 24 hours (unless force_refresh=True).
    """
    conn = db_module.get_conn()
    now = _now_iso()
    
    # Check for cached synthesis
    if not force_refresh and scope:
        cached = conn.execute(
            "SELECT * FROM syntheses WHERE scope = ? ORDER BY created_at DESC LIMIT 1",
            (scope,)
        ).fetchone()
        
        if cached:
            # Check if within 24 hours
            try:
                created = datetime.fromisoformat(cached["created_at"].replace('Z', '+00:00'))
                now_dt = datetime.now(timezone.utc)
                age_seconds = (now_dt - created).total_seconds()
                
                if age_seconds < 86400:  # 24 hours
                    return {
                        "scope": cached["scope"],
                        "portrait": cached["portrait"],
                        "changes": json.loads(cached["changes"]) if isinstance(cached["changes"], str) else cached["changes"],
                        "uncertainties": json.loads(cached["uncertainties"]) if isinstance(cached["uncertainties"], str) else cached["uncertainties"],
                        "recommendations": json.loads(cached["recommendations"]) if isinstance(cached["recommendations"], str) else cached["recommendations"],
                        "memories_included": [cached["scope"]],
                        "memory_count": cached["memories_included"],
                        "cached": True,
                        "created_at": cached["created_at"]
                    }
            except Exception:
                pass
    
    # Fetch memories matching scope
    if scope:
        fts_query = _sanitize_fts_query(scope)
        rows = conn.execute(
            """SELECT m.id, m.name, m.type, m.description, m.content, m.updated_at, m.use_count, m.pinned
               FROM memories_fts fts
               JOIN memories m ON fts.rowid = m.rowid
               WHERE memories_fts MATCH ? AND m.status = 'active'
               ORDER BY fts.rank LIMIT ?""",
            (fts_query, max_memories)
        ).fetchall()
    else:
        if type_filter:
            rows = conn.execute(
                """SELECT id, name, type, description, content, updated_at, use_count, pinned
                   FROM memories WHERE type = ? AND status = 'active'
                   ORDER BY updated_at DESC LIMIT ?""",
                (type_filter, max_memories)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, name, type, description, content, updated_at, use_count, pinned
                   FROM memories WHERE status = 'active'
                   ORDER BY updated_at DESC LIMIT ?""",
                (max_memories,)
            ).fetchall()
    
    # Filter by confidence
    memories = []
    for row in rows:
        conf = confidence_score(pinned=bool(row["pinned"]), updated_at=row["updated_at"], use_count=row["use_count"])
        if conf >= min_confidence:
            memories.append({
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "description": row["description"],
                "content": row["content"],
                "updated_at": row["updated_at"],
                "use_count": row["use_count"],
                "confidence": conf
            })
    
    if not memories:
        return {
            "scope": scope or "all",
            "portrait": "No memories found matching the scope and confidence threshold.",
            "changes": [],
            "uncertainties": [],
            "recommendations": [],
            "memories_included": [],
            "memory_count": 0,
            "cached": False,
            "created_at": now
        }
    
    # Build portrait
    portrait_lines = []
    by_type = {}
    for mem in memories:
        t = mem["type"]
        by_type.setdefault(t, []).append(mem)
    
    for t, mems in sorted(by_type.items()):
        portrait_lines.append(f"{len(mems)} {t} memor{'y' if len(mems) == 1 else 'ies'}")
    
    portrait = f"Current state: {', '.join(portrait_lines)}. Key memories: {', '.join(m['name'] for m in memories[:3])}."
    
    # Identify changes (updated in last 7 days)
    now_dt = datetime.fromisoformat(now.replace('Z', '+00:00'))
    seven_days_ago = (now_dt - timedelta(days=7)).isoformat()
    
    changes = []
    for mem in memories:
        if mem["updated_at"] and mem["updated_at"] > seven_days_ago:
            days_ago = (now_dt - datetime.fromisoformat(mem["updated_at"].replace('Z', '+00:00'))).days
            changes.append(f"{mem['name']}: updated {days_ago} days ago")
    
    # Identify uncertainties (content with uncertain language)
    uncertain_keywords = ["pending", "exploring", "unclear", "tbd", "might", "possibly", "considering", "uncertain", "unknown", "to be determined"]
    uncertainties = []
    for mem in memories:
        content_lower = (mem["content"] or "").lower()
        for kw in uncertain_keywords:
            if kw in content_lower:
                # Extract excerpt
                words = content_lower.split()
                try:
                    idx = words.index(kw)
                    start = max(0, idx - 5)
                    end = min(len(words), idx + 6)
                    excerpt = " ".join(mem["content"].split()[start:end])
                except:
                    excerpt = mem["content"][:100]
                
                uncertainties.append({
                    "name": mem["name"],
                    "excerpt": excerpt
                })
                break
    
    # Identify recommendations
    recommendations = []
    
    # 1. Low use count memories
    unused = [m["name"] for m in memories if m["use_count"] == 0]
    if unused:
        recommendations.append(f"Reconnect or reference: {', '.join(unused[:2])} — these memories have never been recalled")
    
    # 2. Low-confidence memories
    low_conf = [m["name"] for m in memories if m["confidence"] < 0.5]
    if low_conf:
        recommendations.append(f"Update or verify: {', '.join(low_conf[:2])} — confidence is declining")
    
    # 3. Generic recommendation
    if len(memories) > 1:
        recommendations.append(f"Document relationships: Connect {memories[0]['name']} to other {memories[0]['type']} memories for deeper synthesis")
    
    # Store synthesis
    synth_id = str(uuid.uuid4())
    memories_included = [m["name"] for m in memories]
    
    conn.execute(
        """INSERT INTO syntheses (id, scope, portrait, changes, uncertainties, recommendations, memories_included, model_used, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            synth_id,
            scope or "all",
            portrait,
            json.dumps(changes),
            json.dumps(uncertainties),
            json.dumps(recommendations),
            len(memories),
            "mcp-loci-synthesize-v1",
            now
        )
    )
    conn.commit()
    
    return {
        "scope": scope or "all",
        "portrait": portrait,
        "changes": changes,
        "uncertainties": uncertainties,
        "recommendations": recommendations,
        "memories_included": memories_included,
        "memory_count": len(memories),
        "cached": False,
        "created_at": now
    }


@mcp.tool()
def synthesize(
    scope: Optional[str] = None,
    type_filter: Optional[str] = None,
    min_confidence: float = 0.3,
    max_memories: int = 20,
    force_refresh: bool = False,
) -> dict:
    """Cross-memory synthesis — portrait, changes, uncertainties, recommendations."""
    conn = db_module.get_conn()
    now = _now_iso()
    scope_key = scope or (type_filter or "all")

    # Check cache (within 24h) unless force_refresh
    if not force_refresh:
        cached = conn.execute(
            "SELECT * FROM syntheses WHERE scope = ? ORDER BY created_at DESC LIMIT 1",
            (scope_key,),
        ).fetchone()
        if cached:
            from datetime import datetime, timezone
            created = datetime.fromisoformat(cached["created_at"])
            if (datetime.now(timezone.utc) - created).total_seconds() < 86400:
                return {
                    "scope": cached["scope"],
                    "portrait": cached["portrait"],
                    "changes": json.loads(cached["changes"]),
                    "uncertainties": json.loads(cached["uncertainties"]),
                    "recommendations": json.loads(cached["recommendations"]),
                    "memories_included": cached["memories_included"],
                    "model_used": cached["model_used"],
                    "cached": True,
                    "created_at": cached["created_at"],
                }

    # Fetch memories
    type_clause = "AND type = ?" if type_filter else ""
    params = ["active"]
    if type_filter:
        params.append(type_filter)

    if scope and scope != "all":
        # FTS search within scope
        fts_params = [_sanitize_fts_query(scope)] + params + [max_memories * 2]
        rows = conn.execute(
            f"""SELECT m.* FROM memories_fts fts
                JOIN memories m ON fts.rowid = m.rowid
                WHERE memories_fts MATCH ? AND m.status = ? {type_clause}
                ORDER BY fts.rank LIMIT ?""",
            fts_params,
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM memories WHERE status = ? {type_clause} ORDER BY updated_at DESC LIMIT ?",
            params + [max_memories],
        ).fetchall()

    if not rows:
        return {
            "scope": scope_key,
            "portrait": "No memories found matching this scope.",
            "changes": [],
            "uncertainties": [],
            "recommendations": ["Store some memories first using the remember tool."],
            "memories_included": 0,
            "model_used": "rule-based",
            "cached": False,
            "created_at": now,
        }

    # Filter by confidence
    from datetime import datetime, timezone, timedelta
    from mcp_loci.confidence import confidence_score
    qualified = []
    for row in rows:
        conf = confidence_score(
            pinned=bool(row["pinned"]),
            updated_at=row["updated_at"],
            use_count=row["use_count"],
        )
        if conf >= min_confidence or row["pinned"]:
            qualified.append((row, conf))

    qualified = qualified[:max_memories]
    if not qualified:
        qualified = list(zip(rows[:5], [0.5] * 5))

    memory_names = [r["name"] for r, _ in qualified]
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    # Changes — recently updated
    changes = []
    for row, _ in qualified:
        if row["updated_at"] >= cutoff_7d:
            days_ago = (datetime.now(timezone.utc) - datetime.fromisoformat(row["updated_at"])).days
            label = "today" if days_ago == 0 else f"{days_ago}d ago"
            changes.append(f"{row['name']}: updated {label} — {row['description']}")

    # Uncertainties — hedged language in content
    hedge_words = {"pending", "exploring", "unclear", "tbd", "might", "possibly",
                   "considering", "unsure", "maybe", "tentative", "unknown"}
    uncertainties = []
    for row, _ in qualified:
        content_lower = row["content"].lower()
        if any(w in content_lower for w in hedge_words):
            excerpt = row["content"][:120].replace("\n", " ")
            uncertainties.append({"name": row["name"], "excerpt": excerpt + "..."})

    # Recommendations — simple rule-based
    recommendations = []
    never_recalled = [r["name"] for r, _ in qualified if r["use_count"] == 0]
    if never_recalled:
        recommendations.append(
            f"These memories have never been recalled — consider whether they are still relevant: {', '.join(never_recalled[:3])}"
        )
    if uncertainties:
        names = [u["name"] for u in uncertainties[:2]]
        recommendations.append(
            f"Resolve open questions in: {', '.join(names)}"
        )
    if changes:
        recommendations.append(
            f"{len(changes)} memor{'y' if len(changes)==1 else 'ies'} updated recently — verify they reflect current state"
        )
    if not recommendations:
        recommendations.append("Memory set looks healthy — no immediate action required")

    # Portrait — narrative summary
    type_summary = f" ({type_filter} memories)" if type_filter else ""
    scope_phrase = f" matching '{scope}'" if scope else ""
    portrait = (
        f"Across {len(qualified)} active memor{'y' if len(qualified)==1 else 'ies'}"
        f"{type_summary}{scope_phrase}: "
    )
    if changes:
        portrait += f"{len(changes)} updated in the last 7 days, signaling active work. "
    if uncertainties:
        portrait += f"{len(uncertainties)} contain open questions or pending decisions. "
    if never_recalled:
        portrait += f"{len(never_recalled)} have never been recalled and may be stale. "
    portrait += f"Top memories by recency: {', '.join(memory_names[:4])}."

    # Save synthesis
    synth_id = str(__import__('uuid').uuid4())
    conn.execute(
        """INSERT OR REPLACE INTO syntheses
           (id, scope, portrait, changes, uncertainties, recommendations,
            memories_included, model_used, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            synth_id, scope_key, portrait,
            json.dumps(changes), json.dumps(uncertainties),
            json.dumps(recommendations), len(qualified),
            "rule-based-v1", now,
        ),
    )
    conn.commit()

    return {
        "scope": scope_key,
        "portrait": portrait,
        "changes": changes,
        "uncertainties": uncertainties,
        "recommendations": recommendations,
        "memories_included": len(qualified),
        "model_used": "rule-based-v1",
        "cached": False,
        "created_at": now,
    }


@mcp.tool()
def health() -> dict:
    """Return server health status — DB connectivity, embedding model state, memory counts."""
    conn = db_module.get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM memories WHERE status='active'").fetchone()[0]
        pinned = conn.execute("SELECT COUNT(*) FROM memories WHERE status='active' AND pinned=1").fetchone()[0]
        embeddings = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        syntheses = conn.execute("SELECT COUNT(*) FROM syntheses").fetchone()[0]
        db_ok = True
    except Exception as e:
        return {"healthy": False, "error": str(e)}

    model_loaded = False
    if HAS_EMBEDDINGS:
        try:
            from mcp_loci.embedder import _model, _load_model
            model_loaded = (_model is not None)
        except Exception:
            pass

    return {
        "healthy": True,
        "db_path": DB_PATH,
        "memories_active": total,
        "memories_pinned": pinned,
        "embeddings_stored": embeddings,
        "syntheses_cached": syntheses,
        "embedding_model_loaded": model_loaded,
        "embedding_model_name": "all-MiniLM-L6-v2" if HAS_EMBEDDINGS else None,
        "has_embeddings": HAS_EMBEDDINGS,
    }

def main() -> None:
    db_module.configure(DB_PATH)
    # Pre-warm the embedding model in a background thread so the first
    # remember/recall call doesn't block for 3-5s on model load.
    if HAS_EMBEDDINGS:
        import threading
        def _warm():
            try:
                from mcp_loci.embedder import _load_model
                _load_model()
            except Exception:
                pass
        threading.Thread(target=_warm, daemon=True).start()
    mcp.run()

if __name__ == "__main__":
    main()
