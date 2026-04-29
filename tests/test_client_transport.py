"""Transport-level error handling for DefernoClient.

These tests exercise generic transport behavior (timeouts, connection
errors, malformed bodies, missing tokens) — orthogonal to per-endpoint
spec assertions. Migrated from test_client.py.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from defernowork_mcp.client import DefernoClient, DefernoError

BASE = "http://test:3000/api"


@pytest.fixture
def client() -> DefernoClient:
    return DefernoClient(base_url=BASE, token="test-token")


# ── Auth requirement ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_token_raises_401():
    c = DefernoClient(base_url=BASE, token=None)
    with pytest.raises(DefernoError) as exc_info:
        await c.list_tasks()
    assert exc_info.value.status_code == 401


# ── Network errors ──────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_timeout_raises_504(client: DefernoClient):
    respx.get(f"{BASE}/tasks").mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(DefernoError) as exc_info:
        await client.list_tasks()
    assert exc_info.value.status_code == 504
    assert "timed out" in exc_info.value.message


@respx.mock
@pytest.mark.asyncio
async def test_connect_error_raises_502(client: DefernoClient):
    respx.get(f"{BASE}/tasks").mock(side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(DefernoError) as exc_info:
        await client.list_tasks()
    assert exc_info.value.status_code == 502
    assert "network error" in exc_info.value.message


# ── Malformed response body ────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_non_json_error_uses_text(client: DefernoClient):
    respx.get(f"{BASE}/tasks").respond(500, text="Internal Server Error")
    with pytest.raises(DefernoError) as exc_info:
        await client.list_tasks()
    assert exc_info.value.status_code == 500
    assert "Internal Server Error" in exc_info.value.message
