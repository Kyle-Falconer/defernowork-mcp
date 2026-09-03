"""Transparent ref resolution for the kind-neutral ``move_item`` tool.

Exercises the tool through the public interface: a real MCPServer server built by
``create_server`` (client pointed at a respx-mocked backend), the tool looked up
from the registry and invoked.

``move_item`` resolves BOTH ids (item + non-None new_parent_id) before the move
and passes a None new_parent_id through as a root-detach. The retired
``move_task`` is confirmed absent. (Per-kind update/delete ref resolution moved
to test_ref_resolution_item_mutations.py with the kind-neutral ``update_item`` /
``delete_item``.)
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from defernowork_mcp import server as srv
from defernowork_mcp.client import DefernoClient

BASE = "http://test:3000/api"
TASK_UUID = "11111111-2222-3333-4444-555555555555"
PARENT_UUID = "99999999-8888-7777-6666-555555555555"


def _env(data):
    return {"version": "0.2", "data": data, "error": None}


def _task(status="open", **extra):
    body = {
        "kind": "task",
        "type": "task",
        "id": TASK_UUID,
        "title": "A task",
        "status": status,
    }
    body.update(extra)
    return body


@pytest.fixture
def server(monkeypatch):
    async def _stub_get_client_async(ctx=None):
        return DefernoClient(base_url=BASE, token="test-token")

    monkeypatch.setattr(srv, "_get_client_async", _stub_get_client_async)
    monkeypatch.setattr(srv, "_http_transport_mode", False)
    return srv.create_server()


def _tool(mcp, name):
    tools = getattr(mcp, "_tool_manager", None) or getattr(mcp, "tool_manager", None)
    for attr in ("_tools", "tools"):
        tool_map = getattr(tools, attr, None)
        if isinstance(tool_map, dict) and name in tool_map:
            return tool_map[name]
    raise LookupError(f"tool {name!r} not registered")


async def _call(mcp, name, **kwargs):
    tool = _tool(mcp, name)
    return await tool.fn(**kwargs)


# ── move_item: kind-neutral; resolves BOTH ids; None new_parent_id passes through ─


@respx.mock
async def test_move_item_resolves_both_ids(server):
    """move_item resolves item_id AND a non-None new_parent_id before the move.

    Distinct seqs/refs for item vs parent keep the two resolves unambiguous; the
    POST path must use the resolved ITEM uuid and the body the resolved PARENT uuid.
    """
    by_seq_item = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env({"id": TASK_UUID, "kind": "chore"}))
    )
    by_ref_parent = respx.get(f"{BASE}/items/by-ref/u-1y0e2v-456").mock(
        return_value=httpx.Response(200, json=_env({"id": PARENT_UUID, "kind": "task"}))
    )
    move = respx.post(f"{BASE}/items/{TASK_UUID}/move").mock(
        return_value=httpx.Response(200, json=_env(_task()))
    )

    result = await _call(
        server,
        "move_item",
        item_id="#123",
        new_parent_id="u-1y0e2v-456",
        position=2,
    )
    json.loads(result)

    assert by_seq_item.called and by_ref_parent.called and move.called
    # POST path used the resolved ITEM uuid.
    assert move.calls.last.request.url.path == f"/api/items/{TASK_UUID}/move"
    # Body carried the resolved PARENT uuid + the position.
    body = json.loads(move.calls.last.request.content)
    assert body["new_parent_id"] == PARENT_UUID
    assert body["position"] == 2


@respx.mock
async def test_move_item_none_parent_is_root_detach_no_resolve(server):
    """new_parent_id=None means detach-to-root: kept None, never resolved.

    item_id is a non-UUID so exactly ONE resolve (by-seq) fires; the move body
    must carry ``new_parent_id: null``.
    """
    by_seq_item = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env({"id": TASK_UUID, "kind": "event"}))
    )
    move = respx.post(f"{BASE}/items/{TASK_UUID}/move").mock(
        return_value=httpx.Response(200, json=_env(_task()))
    )

    result = await _call(server, "move_item", item_id="#123", new_parent_id=None)
    json.loads(result)

    assert by_seq_item.called and move.called
    body = json.loads(move.calls.last.request.content)
    assert body["new_parent_id"] is None


def test_move_task_retired_in_favor_of_move_item(server):
    with pytest.raises(LookupError):
        _tool(server, "move_task")
