"""Behavioural tests for the ``get_item`` MCP tool.

Exercises the tool through the public interface: a real MCPServer server built
by ``create_server`` (with the client pointed at a respx-mocked backend), the
tool looked up from the registry and invoked. Covers every Ref input form
resolving to the same item, Compact-by-default vs ``full=true``, and not-found.
"""

from __future__ import annotations

import json

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


def _tool(mcp, name):
    tools = getattr(mcp, "_tool_manager", None) or getattr(mcp, "tool_manager", None)
    for attr in ("_tools", "tools"):
        tool_map = getattr(tools, attr, None)
        if isinstance(tool_map, dict) and name in tool_map:
            return tool_map[name]
    raise LookupError(f"tool {name!r} not registered")


async def _call(mcp, **kwargs):
    tool = _tool(mcp, "get_item")
    result = await tool.fn(**kwargs)
    return json.loads(result)


# ── Compact default + full opt-out ───────────────────────────────────────────


@respx.mock
async def test_get_item_compact_by_default(server):
    respx.get(f"{BASE}/items/{ITEM_UUID}").mock(
        return_value=httpx.Response(200, json=_env(FULL_ITEM))
    )
    out = await _call(server, item=ITEM_UUID)

    # Compact whitelist: both discriminators + description present.
    assert out["kind"] == "task"
    assert out["type"] == "task"
    assert out["id"] == ITEM_UUID
    assert out["ref"] == "u-1y0e2v-123"
    assert out["description"] == "the body"
    # heavy fields dropped
    for heavy in ("actions", "comments", "children", "mood_start", "attachments"):
        assert heavy not in out


@respx.mock
async def test_get_item_full_returns_everything(server):
    respx.get(f"{BASE}/items/{ITEM_UUID}").mock(
        return_value=httpx.Response(200, json=_env(FULL_ITEM))
    )
    out = await _call(server, item=ITEM_UUID, full=True)
    assert out == FULL_ITEM


# ── every Ref input form resolves to the same item ───────────────────────────


@respx.mock
async def test_get_item_uuid_form(server):
    by_id = respx.get(f"{BASE}/items/{ITEM_UUID}").mock(
        return_value=httpx.Response(200, json=_env(FULL_ITEM))
    )
    out = await _call(server, item=ITEM_UUID)
    assert by_id.called
    assert out["id"] == ITEM_UUID


@respx.mock
async def test_get_item_sequence_form(server):
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env({"id": ITEM_UUID, "kind": "task"}))
    )
    by_id = respx.get(f"{BASE}/items/{ITEM_UUID}").mock(
        return_value=httpx.Response(200, json=_env(FULL_ITEM))
    )
    out = await _call(server, item="#123")
    assert by_seq.called and by_id.called
    assert out["id"] == ITEM_UUID


@respx.mock
async def test_get_item_canonical_form(server):
    by_ref = respx.get(f"{BASE}/items/by-ref/u-1y0e2v-123").mock(
        return_value=httpx.Response(200, json=_env({"id": ITEM_UUID, "kind": "task"}))
    )
    by_id = respx.get(f"{BASE}/items/{ITEM_UUID}").mock(
        return_value=httpx.Response(200, json=_env(FULL_ITEM))
    )
    out = await _call(server, item="u-1y0e2v-123")
    assert by_ref.called and by_id.called
    assert out["id"] == ITEM_UUID


@respx.mock
async def test_get_item_app_url_form(server):
    by_ref = respx.get(f"{BASE}/items/by-ref/u-1y0e2v-123").mock(
        return_value=httpx.Response(200, json=_env({"id": ITEM_UUID, "kind": "task"}))
    )
    by_id = respx.get(f"{BASE}/items/{ITEM_UUID}").mock(
        return_value=httpx.Response(200, json=_env(FULL_ITEM))
    )
    out = await _call(
        server, item="https://app.defernowork.com/o/u-1y0e2v/items/123"
    )
    assert by_ref.called and by_id.called
    assert out["id"] == ITEM_UUID


@respx.mock
async def test_all_forms_resolve_to_the_same_item(server):
    """The acceptance criterion: #123, canonical, UUID, and app URL agree."""
    respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env({"id": ITEM_UUID, "kind": "task"}))
    )
    respx.get(f"{BASE}/items/by-ref/u-1y0e2v-123").mock(
        return_value=httpx.Response(200, json=_env({"id": ITEM_UUID, "kind": "task"}))
    )
    respx.get(f"{BASE}/items/{ITEM_UUID}").mock(
        return_value=httpx.Response(200, json=_env(FULL_ITEM))
    )
    forms = [
        "#123",
        "u-1y0e2v-123",
        ITEM_UUID,
        "https://app.defernowork.com/o/u-1y0e2v/items/123",
    ]
    ids = [(await _call(server, item=f))["id"] for f in forms]
    assert ids == [ITEM_UUID] * 4


# ── explicit alias escape-hatch (issue #9) ───────────────────────────────────


@respx.mock
async def test_get_item_as_alias_bypasses_classifier(server):
    # ``ABC-223`` collides with a Canonical ref, so the bare classifier would
    # NOT auto-route it (NOT_AUTO_ROUTED -> 400). ``as_alias=True`` forces a
    # direct by-alias lookup, bypassing classify_ref entirely — the escape-hatch
    # for the Deferno-`#` vs GitHub-`#` ambiguity until a context-adaptive
    # classifier exists. A single by-alias call returns the item (no by-id leg).
    by_alias = respx.get(f"{BASE}/items/by-alias/ABC-223").mock(
        return_value=httpx.Response(200, json=_env(FULL_ITEM))
    )
    out = await _call(server, item="ABC-223", as_alias=True)
    assert by_alias.called
    assert out["id"] == ITEM_UUID
    # Compact-by-default projection still applies on the alias path.
    assert out["kind"] == "task"
    assert "actions" not in out


@respx.mock
async def test_get_item_as_alias_full_returns_everything(server):
    respx.get(f"{BASE}/items/by-alias/ABC-223").mock(
        return_value=httpx.Response(200, json=_env(FULL_ITEM))
    )
    out = await _call(server, item="ABC-223", as_alias=True, full=True)
    assert out == FULL_ITEM


# ── not-found ────────────────────────────────────────────────────────────────


@respx.mock
async def test_get_item_not_found_returns_error_string(server):
    respx.get(f"{BASE}/items/by-seq/999").mock(
        return_value=httpx.Response(
            404,
            json={
                "version": "0.2",
                "data": None,
                "error": {"code": "not_found", "message": "item not found"},
            },
        )
    )
    tool = _tool(server, "get_item")
    result = await tool.fn(item="#999")
    # Tools return a human-readable error STRING (not raising) on DefernoError.
    assert isinstance(result, str)
    assert "404" in result
    assert "not_found" in result


# ── per-day occurrence state on a single-item read ───────────────────────────


HABIT_UUID = "99999999-8888-7777-6666-555555555555"

FULL_HABIT = {
    "kind": "habit",
    "type": "habit",
    "id": HABIT_UUID,
    "ref": "u-1y0e2v-2",
    "sequence": 2,
    "org_slug": "u-1y0e2v",
    "title": "Standup",
    # A recurring item's status is active/archived and can NEVER be "done" —
    # resolving one date resolves THAT DATE only, leaving the series open.
    "status": "active",
    "labels": [],
    "pinned": False,
    "parent_id": None,
    # complete_by ADVANCES to the next occurrence on mark-done, so it names
    # tomorrow's deadline, not today's state.
    "complete_by": "2026-08-04T00:00:00Z",
    "date_created": "2026-06-01T00:00:00Z",
    "description": "daily team sync",
    "today_occurrence": {
        "id": "33333333-3333-3333-3333-333333333333",
        "parent_id": HABIT_UUID,
        "scheduled_date": "2026-08-03",
        "complete_by": "2026-08-03T23:59:59Z",
        "status": "done_on_time",
        "done_at": "2026-08-03T09:15:00Z",
    },
    # heavy fields that compact must still drop:
    "actions": [{"kind": "created"}],
    "comments": [{"body": "hi"}],
}


@respx.mock
async def test_get_item_compact_keeps_today_occurrence(server):
    """``get_item`` must be able to answer "is this done today?".

    Nothing else in the compact projection can: the item ``status`` reads
    ``active`` whether or not today is checked in, and ``complete_by`` has
    already advanced past today.
    """
    respx.get(f"{BASE}/items/{HABIT_UUID}").mock(
        return_value=httpx.Response(200, json=_env(FULL_HABIT))
    )
    out = await _call(server, item=HABIT_UUID)

    assert out["status"] == "active"
    assert out["today_occurrence"]["status"] == "done_on_time"
    assert out["today_occurrence"]["scheduled_date"] == "2026-08-03"
    # still a compact projection — heavy arrays dropped
    assert "actions" not in out
    assert "comments" not in out
