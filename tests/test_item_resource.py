"""Behavioural tests for the ``defernowork://item/{ref}`` MCP resource (#8).

Exercises the resource through the real FastMCP surface: a server built by
``create_server`` (client pointed at a respx-mocked backend), the registered
resource template looked up and invoked via the resource manager. Covers each
Ref input form (UUID, sequence shorthand, canonical ref) resolving to the same
item and returning a Compact projection.

We also assert the bounded-surface reshape itself: the unbounded
``defernowork://tasks`` resource and the UUID-only ``defernowork://task/{id}``
template are gone, while ``plan`` + ``mood-history`` are retained.

The App URL and Alias forms, the routing that keeps a Sequence shorthand on the
personal org, and the errors for malformed and empty refs are pinned separately
in ``test_item_resource_ref_forms.py``.
"""

from __future__ import annotations

import json
from urllib.parse import quote

import httpx
import pytest
import respx

from defernowork_mcp import server as srv
from defernowork_mcp.client import DefernoClient

BASE = "http://test:3000/api"
ITEM_UUID = "11111111-2222-3333-4444-555555555555"

FULL_ITEM = {
    "kind": "task",
    "type": "task",
    "id": ITEM_UUID,
    "ref": "u-1y0e2v-123",
    "sequence": 123,
    "org_slug": "u-1y0e2v",
    "title": "Write the classifier",
    "status": "active",
    "labels": ["mcp"],
    "pinned": False,
    "parent_id": None,
    "complete_by": "2026-06-10T00:00:00Z",
    "date_created": "2026-06-01T00:00:00Z",
    "description": "the body",
    # heavy fields that compact must drop:
    "actions": [{"kind": "created"}],
    "comments": [{"body": "hi"}],
    "children": [{"id": "child"}],
    "mood_start": [0.1, 0.2],
    "attachments": [{"url": "x"}],
}


def _env(data):
    return {"version": "0.2", "data": data, "error": None}


@pytest.fixture
def server(monkeypatch):
    async def _stub_get_client_async(ctx=None):
        return DefernoClient(base_url=BASE, token="test-token")

    monkeypatch.setattr(srv, "_get_client_async", _stub_get_client_async)
    monkeypatch.setattr(srv, "_http_transport_mode", False)
    return srv.create_server()


async def _read(mcp, uri: str):
    """Resolve a resource URI through the registered FastMCP surface and read it.

    Goes through ``ResourceManager.get_resource`` (URI -> template ``matches``
    regex routing -> ``create_resource`` which calls the real handler) so the
    test reflects real behaviour, then ``read()`` returns the handler's string.
    """
    resource = await mcp._resource_manager.get_resource(uri)
    body = await resource.read()
    return json.loads(body)


# ── the reshape itself: bounded surfaces only ────────────────────────────────


def test_item_template_registered_and_old_surfaces_removed(server):
    rm = server._resource_manager
    assert "defernowork://item/{ref}" in rm._templates
    # the UUID-only single-task template is gone
    assert "defernowork://task/{task_id}" not in rm._templates
    concrete = set(rm._resources)
    # the unbounded all-tasks resource is dropped
    assert "defernowork://tasks" not in concrete
    # plan + mood-history are retained
    assert "defernowork://tasks/plan" in concrete
    assert "defernowork://tasks/mood-history" in concrete


# ── compact projection ───────────────────────────────────────────────────────


@respx.mock
async def test_item_resource_returns_compact(server):
    respx.get(f"{BASE}/items/{ITEM_UUID}").mock(
        return_value=httpx.Response(200, json=_env(FULL_ITEM))
    )
    out = await _read(server, f"defernowork://item/{ITEM_UUID}")

    # Compact whitelist: discriminators, ref, title, description present.
    assert out["kind"] == "task"
    assert out["type"] == "task"
    assert out["ref"] == "u-1y0e2v-123"
    assert out["title"] == "Write the classifier"
    assert out["description"] == "the body"
    # heavy fields dropped
    for heavy in ("actions", "comments", "children", "mood_start", "attachments"):
        assert heavy not in out


# ── every Ref input form resolves to the same item ───────────────────────────


@respx.mock
async def test_item_resource_uuid_form(server):
    by_id = respx.get(f"{BASE}/items/{ITEM_UUID}").mock(
        return_value=httpx.Response(200, json=_env(FULL_ITEM))
    )
    out = await _read(server, f"defernowork://item/{ITEM_UUID}")
    assert by_id.called
    assert out["ref"] == "u-1y0e2v-123"
    assert "description" in out
    assert "actions" not in out


@respx.mock
async def test_item_resource_sequence_form(server):
    # A real client URL-encodes ``#123`` -> ``%23123``; unquote must turn it
    # back into ``#123`` so the classifier routes it as a Sequence shorthand.
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env({"id": ITEM_UUID, "kind": "task"}))
    )
    by_id = respx.get(f"{BASE}/items/{ITEM_UUID}").mock(
        return_value=httpx.Response(200, json=_env(FULL_ITEM))
    )
    out = await _read(server, f"defernowork://item/{quote('#123', safe='')}")
    assert by_seq.called and by_id.called
    assert out["ref"] == "u-1y0e2v-123"
    assert "description" in out
    assert "comments" not in out


@respx.mock
async def test_item_resource_canonical_form(server):
    by_ref = respx.get(f"{BASE}/items/by-ref/u-1y0e2v-123").mock(
        return_value=httpx.Response(200, json=_env({"id": ITEM_UUID, "kind": "task"}))
    )
    by_id = respx.get(f"{BASE}/items/{ITEM_UUID}").mock(
        return_value=httpx.Response(200, json=_env(FULL_ITEM))
    )
    out = await _read(server, "defernowork://item/u-1y0e2v-123")
    assert by_ref.called and by_id.called
    assert out["ref"] == "u-1y0e2v-123"
    assert "description" in out
    assert "children" not in out


@respx.mock
async def test_item_resource_all_forms_agree(server):
    """UUID, sequence shorthand, and canonical ref resolve to the same item."""
    respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env({"id": ITEM_UUID, "kind": "task"}))
    )
    respx.get(f"{BASE}/items/by-ref/u-1y0e2v-123").mock(
        return_value=httpx.Response(200, json=_env({"id": ITEM_UUID, "kind": "task"}))
    )
    respx.get(f"{BASE}/items/{ITEM_UUID}").mock(
        return_value=httpx.Response(200, json=_env(FULL_ITEM))
    )
    uris = [
        f"defernowork://item/{ITEM_UUID}",
        f"defernowork://item/{quote('#123', safe='')}",
        "defernowork://item/u-1y0e2v-123",
    ]
    refs = [(await _read(server, u))["ref"] for u in uris]
    assert refs == ["u-1y0e2v-123"] * 3
