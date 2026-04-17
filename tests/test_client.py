"""Tests for the DefernoClient HTTP layer."""

from __future__ import annotations

import pytest
import respx
import httpx

from defernowork_mcp.client import DefernoClient, DefernoError

BASE = "http://test:3000"


@pytest.fixture
def client():
    return DefernoClient(base_url=BASE, token="test-token")


# ── Basic request handling ────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_get_returns_json(client: DefernoClient):
    respx.get(f"{BASE}/tasks").respond(json=[{"id": "abc", "title": "Hello"}])
    result = await client.list_tasks()
    assert len(result) == 1
    assert result[0]["title"] == "Hello"


@respx.mock
@pytest.mark.asyncio
async def test_204_returns_none(client: DefernoClient):
    respx.post(f"{BASE}/tasks/plan/add").respond(204)
    result = await client.add_to_plan("some-id")
    assert result is None


@respx.mock
@pytest.mark.asyncio
async def test_error_raises_deferno_error(client: DefernoClient):
    respx.get(f"{BASE}/tasks/nonexistent").respond(
        404, json={"message": "task not found"}
    )
    with pytest.raises(DefernoError) as exc_info:
        await client.get_task("nonexistent")
    assert exc_info.value.status_code == 404
    assert "task not found" in exc_info.value.message


# ── Auth requirement ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_token_raises_401():
    client = DefernoClient(base_url=BASE, token=None)
    with pytest.raises(DefernoError) as exc_info:
        await client.list_tasks()
    assert exc_info.value.status_code == 401


# ── Network errors ────────────────────────────────────────────────────────


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
    respx.get(f"{BASE}/tasks").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(DefernoError) as exc_info:
        await client.list_tasks()
    assert exc_info.value.status_code == 502
    assert "network error" in exc_info.value.message


# ── Malformed response ────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_non_json_error_uses_text(client: DefernoClient):
    respx.get(f"{BASE}/tasks").respond(500, text="Internal Server Error")
    with pytest.raises(DefernoError) as exc_info:
        await client.list_tasks()
    assert exc_info.value.status_code == 500


# ── Daily plan methods ────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_get_daily_plan_passes_date(client: DefernoClient):
    route = respx.get(f"{BASE}/tasks/plan", params={"date": "2026-04-14"}).respond(json=[])
    result = await client.get_daily_plan("2026-04-14")
    assert result == []
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_add_to_plan_sends_task_id(client: DefernoClient):
    route = respx.post(f"{BASE}/tasks/plan/add").respond(204)
    await client.add_to_plan("task-uuid-123")
    assert route.called
    body = route.calls[0].request.content
    import json
    assert json.loads(body)["task_id"] == "task-uuid-123"


@respx.mock
@pytest.mark.asyncio
async def test_remove_from_plan(client: DefernoClient):
    route = respx.post(f"{BASE}/tasks/plan/remove").respond(204)
    await client.remove_from_plan("task-uuid-456")
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_reorder_plan(client: DefernoClient):
    route = respx.post(f"{BASE}/tasks/plan/reorder").respond(204)
    await client.reorder_plan(["id-1", "id-2"])
    assert route.called
    import json
    body = json.loads(route.calls[0].request.content)
    assert body["task_ids"] == ["id-1", "id-2"]


# ── Move task ─────────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_move_task_sends_payload(client: DefernoClient):
    route = respx.post(f"{BASE}/tasks/abc/move").respond(json={"id": "abc"})
    result = await client.move_task("abc", new_parent_id="parent-1", position=2)
    assert result["id"] == "abc"
    import json
    body = json.loads(route.calls[0].request.content)
    assert body["new_parent_id"] == "parent-1"
    assert body["position"] == 2


# ── Batch operations ─────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_batch_sends_payload(client: DefernoClient):
    response_data = {
        "tasks": [
            {"id": "id-1", "title": "Updated"},
            {"id": "id-2", "title": "Moved"},
        ]
    }
    route = respx.post(f"{BASE}/tasks/batch").respond(json=response_data)
    result = await client.batch([
        {"op": "update", "task_id": "id-1", "title": "Updated"},
        {"op": "move", "task_id": "id-2", "new_parent_id": "id-1"},
    ])
    assert len(result["tasks"]) == 2
    import json
    body = json.loads(route.calls[0].request.content)
    assert len(body["operations"]) == 2
    assert body["operations"][0]["op"] == "update"
    assert body["operations"][1]["op"] == "move"


@respx.mock
@pytest.mark.asyncio
async def test_batch_error_raises(client: DefernoClient):
    respx.post(f"{BASE}/tasks/batch").respond(
        400, json={"message": "operation 1: cannot complete task while children remain active"}
    )
    with pytest.raises(DefernoError) as exc_info:
        await client.batch([{"op": "update", "task_id": "id-1", "status": "done"}])
    assert exc_info.value.status_code == 400
    assert "operation 1" in exc_info.value.message
