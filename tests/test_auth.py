from contextlib import asynccontextmanager

import httpx
import pytest

from .conftest import TOKEN

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream"}


@asynccontextmanager
async def serve(server):
    # Entered inside the test body: the MCP session manager must start and stop in one task.
    app = server.http_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_health_is_public(server):
    async with serve(server) as http:
        resp = await http.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.parametrize("auth", [None, "Bearer wrong", f"Basic {TOKEN}"])
async def test_mcp_rejects_missing_or_bad_token(server, auth):
    headers = dict(HEADERS)
    if auth:
        headers["Authorization"] = auth
    async with serve(server) as http:
        resp = await http.post("/mcp", json=INIT, headers=headers)
    assert resp.status_code == 401


async def test_mcp_accepts_valid_token(server):
    async with serve(server) as http:
        resp = await http.post(
            "/mcp", json=INIT, headers={**HEADERS, "Authorization": f"Bearer {TOKEN}"}
        )
    assert resp.status_code == 200
