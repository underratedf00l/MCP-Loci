"""Integration tests — spawn the real MCP server subprocess and exercise the
JSON-RPC wire protocol end-to-end.

FastMCP 3.x uses newline-delimited JSON (NDJSON) transport, NOT the
Content-Length framing from the original MCP spec. Each message is a single
JSON object terminated by \n.
"""
import json
import os
import select
import subprocess
import sys
import time

import pytest

SERVER_CMD = [sys.executable, "-u", "-m", "mcp_loci.server"]
SERVER_CWD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _send(proc, payload: dict) -> None:
    """Write a JSON-RPC message to the server stdin (newline-delimited)."""
    line = json.dumps(payload) + "\n"
    proc.stdin.write(line.encode())
    proc.stdin.flush()


def _recv(proc, timeout: float = 15.0) -> dict:
    """Read one JSON-RPC message from the server stdout.

    FastMCP 3.x sends newline-terminated JSON lines — no Content-Length
    framing. We use select() + os.read() so we never block forever.
    """
    deadline = time.monotonic() + timeout
    buf = b""

    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 0.5))
        if not ready:
            continue
        ch = os.read(proc.stdout.fileno(), 1)
        if not ch:
            break
        buf += ch
        if buf.endswith(b"\n"):
            stripped = buf.strip()
            if not stripped:
                # blank line — keep reading
                buf = b""
                continue
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                # partial or corrupt line — keep reading
                buf = b""
                continue

    return {}


def _initialize(proc) -> dict:
    """Send MCP initialize + initialized notification, return server info."""
    _send(proc, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "test-client", "version": "0.0.1"},
            "capabilities": {},
        },
    })
    response = _recv(proc)
    _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    return response


def _call_tool(proc, call_id: int, tool: str, args: dict) -> dict:
    """Send a tools/call request and return the result message."""
    _send(proc, {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    })
    return _recv(proc)


@pytest.fixture()
def server(tmp_path):
    """Spawn the MCP server with an isolated DB and yield the process."""
    db_path = str(tmp_path / "integration.db")
    env = {**os.environ, "MCP_MEMORY_DB_PATH": db_path, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        SERVER_CMD,
        cwd=SERVER_CWD,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    # Give the server time to start up and run DB migrations
    time.sleep(1.5)
    yield proc
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_server_starts_and_initializes(server):
    """Server must respond to MCP initialize with a valid result."""
    response = _initialize(server)
    assert response.get("id") == 1
    result = response.get("result", {})
    assert "serverInfo" in result or "capabilities" in result


def test_health_tool_returns_healthy(server):
    """health tool must return healthy=True with correct fields."""
    _initialize(server)
    response = _call_tool(server, 2, "health", {})
    result = response.get("result", {})
    content = result.get("content", [{}])
    data = json.loads(content[0].get("text", "{}"))
    assert data.get("healthy") is True
    assert "memories_active" in data
    assert "embeddings_stored" in data


def test_remember_then_recall_roundtrip(server):
    """remember a fact, then recall it — must surface in results."""
    _initialize(server)
    _call_tool(server, 2, "remember", {
        "name": "integration_test_memory",
        "type": "project",
        "description": "integration test fixture",
        "content": "The integration test stored this fact about xylophone protocol",
    })
    response = _call_tool(server, 3, "recall", {
        "query": "xylophone protocol",
        "semantic": False,
    })
    result = response.get("result", {})
    content = result.get("content", [{}])
    data = json.loads(content[0].get("text", "[]"))
    names = [r.get("name") for r in data]
    assert "integration_test_memory" in names


def test_forget_removes_memory(server):
    """forget must prevent a memory from appearing in recall."""
    _initialize(server)
    _call_tool(server, 2, "remember", {
        "name": "to_be_forgotten",
        "type": "reference",
        "description": "temporary",
        "content": "This memory about oblique references should be deleted",
    })
    _call_tool(server, 3, "forget", {"memory_id_or_name": "to_be_forgotten"})
    response = _call_tool(server, 4, "recall", {
        "query": "oblique references",
        "semantic": False,
    })
    result = response.get("result", {})
    content = result.get("content", [{}])
    data = json.loads(content[0].get("text", "[]") if content else "[]")
    names = [r.get("name") for r in data]
    assert "to_be_forgotten" not in names
