import math
from datetime import datetime, timezone


def confidence_score(pinned: bool, updated_at: str, use_count: int) -> float:
    """
    Computed confidence score. Never stored — always derived on read.
    Pinned memories always return 1.0.
    Formula: 0.6 * recency + 0.4 * access_saturation
    """
    if pinned:
        return 1.0

    now = datetime.now(timezone.utc)
    updated = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
    days_since_update = max((now - updated).days, 0)

    recency = math.exp(-days_since_update / 90)          # ~0.37 at 90 days
    access = min(use_count / 10.0, 1.0)                  # saturates at 10 uses

    return round(0.6 * recency + 0.4 * access, 3)


def match_reason(similarity: float | None, confidence: float,
                 pinned: bool, keyword_match: bool, use_count: int) -> str:
    """Human-readable explanation of why a memory surfaced."""
    if pinned:
        return f"pinned + confidence {confidence:.2f}"
    if similarity is not None and similarity >= 0.85:
        return f"semantic similarity {similarity:.2f}"
    if keyword_match and similarity is not None:
        return f"keyword match + similarity {similarity:.2f}"
    if keyword_match:
        return "keyword match"
    if use_count >= 5:
        return f"frequently reinforced (used {use_count}x)"
    return f"confidence {confidence:.2f}"
