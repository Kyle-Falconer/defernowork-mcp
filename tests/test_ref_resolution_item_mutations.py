"""Kind-neutral ``update_item`` / ``delete_item`` — ref resolution + per-kind
dispatch.

These two tools replace the per-kind ``update_*`` / ``delete_*`` (and the
``set_task_status`` convenience). Both resolve any Ref input form to ``(uuid,
kind)`` via ``resolve_ref_with_kind`` and dispatch to the matching backend
entity path — the same primitive the occurrence tools use.

Coverage:
- ``update_item`` resolves EACH Ref input form (UUID pays one kind GET; #seq /
  canonical / app-url learn the kind from the resolve) and PATCHes the resolved
  entity path.
- ``update_item`` dispatches to the right per-kind PATCH (task/chore/habit/event).
- The recurring-Task scope guard is preserved (a deferno-field change on a
  series-backed task with no scope returns the ask-for-scope message, no PATCH).
- Task-only fields are rejected on a non-Task before any write; ``end_time`` is
  rejected off an Event.
- A NOT_AUTO_ROUTED ref surfaces a clear 400 and issues NO write.
- ``delete_item`` dispatches to the right per-kind DELETE and returns
  ``{deleted, id, kind}``.

Tools are exercised through the public interface: a real MCPServer server built by
``create_server`` (client pointed at a respx-mocked backend), the tool looked up
from the registry and invoked.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from defernowork_mcp import server as srv
from defernowork_mcp.client import DefernoClient

BASE = "http://test:3000/api"
UUID = "11111111-2222-3333-4444-555555555555"


def _env(data):
    return {"version": "0.2", "data": data, "error": None}


def _entity(kind, **extra):
    body = {"id": UUID, "kind": kind, "type": kind, "title": "x"}
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
    return await _tool(mcp, name).fn(**kwargs)


# ── per-form resolution matrix (task kind) ────────────────────────────────────


@respx.mock
async def test_update_item_uuid_form_does_one_kind_get_then_patches(server):
    """A raw UUID has no kind locally -> one GET /items/{id} to learn it, then
    PATCH the resolved entity path. No by-seq / by-ref resolve fires."""
    by_seq = respx.get(f"{BASE}/items/by-seq/123")
    by_ref = respx.get(f"{BASE}/items/by-ref/u-1y0e2v-123")
    kind_get = respx.get(f"{BASE}/items/{UUID}").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )
    patch = respx.patch(f"{BASE}/tasks/{UUID}").mock(
        return_value=httpx.Response(200, json=_env(_entity("task", status="done")))
    )

    result = await _call(server, "update_item", ref=UUID, status="done")
    out = json.loads(result)

    assert kind_get.called and patch.called
    assert not by_seq.called and not by_ref.called
    assert out["id"] == UUID and out["status"] == "done"


@respx.mock
async def test_update_item_sequence_form_resolves_then_patches(server):
    """``#123`` -> GET /items/by-seq/123 (kind comes free), then PATCH /tasks/{uuid}."""
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )
    items = respx.get(f"{BASE}/items/{UUID}")  # must NOT be hit (kind came free)
    patch = respx.patch(f"{BASE}/tasks/{UUID}").mock(
        return_value=httpx.Response(200, json=_env(_entity("task", title="renamed")))
    )

    result = await _call(server, "update_item", ref="#123", status="done")
    out = json.loads(result)

    assert by_seq.called and patch.called and not items.called
    assert patch.calls.last.request.url.path == f"/api/tasks/{UUID}"
    assert out["id"] == UUID


@respx.mock
async def test_update_item_canonical_form_resolves_then_patches(server):
    by_ref = respx.get(f"{BASE}/items/by-ref/u-1y0e2v-123").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )
    patch = respx.patch(f"{BASE}/tasks/{UUID}").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )

    result = await _call(server, "update_item", ref="u-1y0e2v-123", status="done")
    json.loads(result)

    assert by_ref.called and patch.called


@respx.mock
async def test_update_item_app_url_form_resolves_by_ref_then_patches(server):
    by_ref = respx.get(f"{BASE}/items/by-ref/u-1y0e2v-123").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )
    patch = respx.patch(f"{BASE}/tasks/{UUID}").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )

    result = await _call(
        server,
        "update_item",
        ref="https://app.defernowork.com/o/u-1y0e2v/items/123",
        status="done",
    )
    json.loads(result)

    assert by_ref.called and patch.called


@respx.mock
async def test_update_item_not_auto_routed_surfaces_400_and_skips_write(server):
    patch = respx.patch(f"{BASE}/tasks/{UUID}")
    result = await _call(server, "update_item", ref="ABC-223", description="d")

    assert isinstance(result, str)
    assert "400" in result and "not an auto-routable" in result
    assert not patch.called


# ── per-kind dispatch (update) ────────────────────────────────────────────────


@respx.mock
@pytest.mark.parametrize(
    "kind,path",
    [("chore", "chores"), ("habit", "habits"), ("event", "events")],
)
async def test_update_item_dispatches_per_kind(server, kind, path):
    """A resolved {kind} routes the PATCH to /{path}/{uuid}."""
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env(_entity(kind)))
    )
    patch = respx.patch(f"{BASE}/{path}/{UUID}").mock(
        return_value=httpx.Response(200, json=_env(_entity(kind, title="y")))
    )

    result = await _call(server, "update_item", ref="#123", title="y")
    out = json.loads(result)

    assert by_seq.called and patch.called
    assert patch.calls.last.request.url.path == f"/api/{path}/{UUID}"
    assert out["id"] == UUID


# ── recurring-Task scope guard (preserved from update_task) ───────────────────


@respx.mock
async def test_update_item_recurring_task_without_scope_asks_first(server):
    """A deferno-field change on a series-backed task with no scope asks for the
    scope and issues NO patch — the series check uses the resolved uuid."""
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )
    get_task = respx.get(f"{BASE}/tasks/{UUID}").mock(
        return_value=httpx.Response(200, json=_env(_entity("task", series_id="s-1")))
    )
    patch = respx.patch(f"{BASE}/tasks/{UUID}")

    result = await _call(server, "update_item", ref="#123", title="renamed")

    assert by_seq.called and get_task.called and not patch.called
    assert "recurring_scope" in result


@respx.mock
async def test_update_item_recurring_task_with_scope_patches(server):
    """Providing recurring_scope skips the series guard and patches directly."""
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )
    get_task = respx.get(f"{BASE}/tasks/{UUID}")  # must NOT be hit
    patch = respx.patch(f"{BASE}/tasks/{UUID}").mock(
        return_value=httpx.Response(200, json=_env(_entity("task", title="renamed")))
    )

    result = await _call(
        server, "update_item", ref="#123", title="renamed", recurring_scope="all"
    )
    out = json.loads(result)

    assert by_seq.called and patch.called and not get_task.called
    body = json.loads(patch.calls.last.request.content)
    assert body["recurring_scope"] == "all"
    assert out["title"] == "renamed"


# ── wrong-kind field rejection (trust boundary, before any write) ─────────────


@respx.mock
async def test_update_item_task_only_field_on_chore_rejected_no_write(server):
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env(_entity("chore")))
    )
    patch = respx.patch(f"{BASE}/chores/{UUID}")

    result = await _call(server, "update_item", ref="#123", status="done")

    assert by_seq.called and not patch.called
    assert "status" in result and "Task" in result


@respx.mock
async def test_update_item_end_time_off_event_rejected_no_write(server):
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env(_entity("chore")))
    )
    patch = respx.patch(f"{BASE}/chores/{UUID}")

    result = await _call(
        server, "update_item", ref="#123", end_time="2026-06-20T10:00:00Z"
    )

    assert by_seq.called and not patch.called
    assert "end_time" in result and "Event" in result


# ── blocked_by: Task-only dependency edge (three-state + ref resolution) ──────

BLOCKER_UUID = "99999999-8888-7777-6666-555555555555"


@respx.mock
async def test_update_item_blocked_by_omitted_absent_from_body(server):
    """Omitting blocked_by keeps it out of the PATCH body (no-op three-state)."""
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )
    patch = respx.patch(f"{BASE}/tasks/{UUID}").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )

    await _call(server, "update_item", ref="#123", status="done")

    assert by_seq.called and patch.called
    body = json.loads(patch.calls.last.request.content)
    assert "blocked_by" not in body


@respx.mock
@pytest.mark.parametrize("value", [None, []])
async def test_update_item_blocked_by_clear_forwarded(server, value):
    """``None`` and ``[]`` both survive compaction and clear the edge on the wire."""
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )
    patch = respx.patch(f"{BASE}/tasks/{UUID}").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )

    await _call(server, "update_item", ref="#123", blocked_by=value)

    body = json.loads(patch.calls.last.request.content)
    assert "blocked_by" in body and body["blocked_by"] == value


@respx.mock
async def test_update_item_blocked_by_resolves_blocker_ref_preserves_occurrence(server):
    """Each blocker's ``item`` resolves to a UUID; ``occurrence`` rides through."""
    kind_get = respx.get(f"{BASE}/items/{UUID}").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )
    # blocker handed in as #123 -> by-seq resolve to a different UUID
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env(_entity("task", id=BLOCKER_UUID)))
    )
    patch = respx.patch(f"{BASE}/tasks/{UUID}").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )

    await _call(
        server,
        "update_item",
        ref=UUID,
        blocked_by=[{"item": "#123", "occurrence": "2026-06-20"}],
    )

    assert kind_get.called and by_seq.called and patch.called
    body = json.loads(patch.calls.last.request.content)
    assert body["blocked_by"] == [{"item": BLOCKER_UUID, "occurrence": "2026-06-20"}]


@respx.mock
async def test_update_item_blocked_by_on_chore_rejected_no_write(server):
    """``blocked_by`` is Task-only -> wrong-kind rejection before any write."""
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env(_entity("chore")))
    )
    patch = respx.patch(f"{BASE}/chores/{UUID}")

    result = await _call(
        server, "update_item", ref="#123", blocked_by=[{"item": UUID}]
    )

    assert by_seq.called and not patch.called
    assert "blocked_by" in result and "Task" in result


@respx.mock
@pytest.mark.parametrize("value", [[{"occurrence": "2026-06-20"}], ["#123"]])
async def test_update_item_blocked_by_malformed_entry_rejected_no_write(server, value):
    """A blocker entry missing ``item`` (or not a dict) is rejected before any write."""
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env(_entity("task")))
    )
    patch = respx.patch(f"{BASE}/tasks/{UUID}")

    result = await _call(server, "update_item", ref="#123", blocked_by=value)

    assert not patch.called
    assert "blocked_by" in result and "item" in result


# ── delete_item: per-kind dispatch + return shape ─────────────────────────────


@respx.mock
@pytest.mark.parametrize(
    "kind,path",
    [
        ("task", "tasks"),
        ("chore", "chores"),
        ("habit", "habits"),
        ("event", "events"),
    ],
)
async def test_delete_item_dispatches_per_kind(server, kind, path):
    by_seq = respx.get(f"{BASE}/items/by-seq/123").mock(
        return_value=httpx.Response(200, json=_env(_entity(kind)))
    )
    delete = respx.delete(f"{BASE}/{path}/{UUID}").mock(
        return_value=httpx.Response(204)
    )

    result = await _call(server, "delete_item", ref="#123")
    out = json.loads(result)

    assert by_seq.called and delete.called
    assert delete.calls.last.request.url.path == f"/api/{path}/{UUID}"
    assert out == {"deleted": True, "id": UUID, "kind": kind}


@respx.mock
async def test_delete_item_not_auto_routed_surfaces_400_and_skips_delete(server):
    delete = respx.delete(f"{BASE}/tasks/{UUID}")
    result = await _call(server, "delete_item", ref="ABC-223")

    assert isinstance(result, str)
    assert "400" in result and "not an auto-routable" in result
    assert not delete.called
