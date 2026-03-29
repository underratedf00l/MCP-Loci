import pytest
from mcp_loci import db as db_module
from mcp_loci import server


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    db_module.configure(db_path)
    db_module.reset()
    yield
    db_module.reset()


def _store(name, content, type_="project", days_old=0):
    from datetime import datetime, timezone, timedelta
    ts = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    conn = db_module.get_conn()
    import uuid
    mid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO memories (id, name, type, description, content, pinned, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
        (mid, name, type_, f"desc of {name}", content, ts, ts),
    )
    conn.commit()
    return mid


def test_synthesize_returns_portrait():
    _store("mem_a", "We are building a CX platform for enterprise customers")
    _store("mem_b", "The Glue is an integration middleware with FedRAMP readiness")
    _store("mem_c", "NUVØ is the music and creative identity project")
    result = server.synthesize()
    assert result["portrait"]
    assert len(result["portrait"]) > 20
    assert result["memories_included"] == 3
    assert result["cached"] is False


def test_synthesize_caches_result():
    _store("consulting_positioning", "FoundryCX is a CX consulting firm for enterprise clients")
    _store("consulting_pipeline", "Pipeline includes three enterprise deals in Q2")
    # Use type_filter as the scope key — no FTS lookup, deterministic
    first = server.synthesize(type_filter="project")
    assert first["cached"] is False
    second = server.synthesize(type_filter="project")
    assert second["cached"] is True
    assert second["portrait"] == first["portrait"]


def test_synthesize_force_refresh_bypasses_cache():
    _store("mem_a", "conference strategy for channel partners annual event")
    first = server.synthesize(force_refresh=True)
    assert first["cached"] is False
    second = server.synthesize(force_refresh=True)
    assert second["cached"] is False


def test_synthesize_identifies_uncertainties():
    _store("mem_a", "The healthcare channel play is still pending evaluation")
    _store("mem_b", "We might pursue FedRAMP certification next quarter")
    _store("mem_c", "FoundryCX positioning is locked and clear")
    result = server.synthesize()
    uncertain_names = [u["name"] for u in result["uncertainties"]]
    assert "mem_a" in uncertain_names or "mem_b" in uncertain_names
