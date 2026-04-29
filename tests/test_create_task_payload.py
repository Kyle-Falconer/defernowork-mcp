"""Regression tests for the JSON body that ``create_task`` sends.

The Deferno backend rejects POST /tasks bodies that contain a ``recurrence``
or ``recurring_type`` key — even when their values are JSON null — with::

    422 Failed to deserialize the JSON body into the target type:
    'recurrence' is not allowed on a Task payload — Tasks are non-recurring.

The MCP ``create_task`` tool used to default ``recurrence`` and
``recurring_type`` to ``None`` and feed them through ``_compact``, which only
strips the ``_UNSET`` sentinel.  That meant minimal calls
(``create_task(title=..., description=...)``) shipped ``"recurrence": null``
and ``"recurring_type": null`` and got 422'd by the backend.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from defernowork_mcp import server as srv
from defernowork_mcp.client import DefernoClient


BASE = "http://test:3000/api"
TASK_ID = "00000000-0000-0000-0000-000000000001"

CREATED_TASK_ENVELOPE = {
    "version": "0.1",
    "data": {
        "id": TASK_ID,
        "title": "Demo",
        "status": "open",
        "actions": [{"kind": "Created"}],
        "date_created": "2026-04-29T00:00:00Z",
    },
    "error": None,
}


@pytest.fixture
def fastmcp(monkeypatch):
    """Build a FastMCP whose tools talk to the respx-mocked BASE."""

    async def _stub_get_client_async(ctx=None):
        return DefernoClient(base_url=BASE, token="test-token")

    monkeypatch.setattr(srv, "_get_client_async", _stub_get_client_async)
    monkeypatch.setattr(srv, "_http_transport_mode", False)
    return srv.create_server()


def _registered_tool(mcp, name):
    tools = getattr(mcp, "_tool_manager", None) or getattr(mcp, "tool_manager", None)
    for attr in ("_tools", "tools"):
        tool_map = getattr(tools, attr, None)
        if isinstance(tool_map, dict) and name in tool_map:
            return tool_map[name]
    raise LookupError(f"tool {name!r} not registered")


async def _invoke(tool, **kwargs):
    fn = getattr(tool, "fn", None) or tool
    return await fn(**kwargs)


@respx.mock
@pytest.mark.asyncio
async def test_create_task_minimal_omits_recurrence_keys(fastmcp):
    """A bare ``create_task(title, description)`` must not ship recurrence keys.

    The backend 422s if either ``recurrence`` or ``recurring_type`` appears
    on the JSON body of a POST /tasks request — null values included.
    """
    route = respx.post(BASE + "/tasks").mock(
        return_value=httpx.Response(201, json=CREATED_TASK_ENVELOPE)
    )

    tool = _registered_tool(fastmcp, "create_task")
    result = await _invoke(tool, title="Demo", description="Test")

    assert route.called, "create_task should POST /tasks"
    body = json.loads(route.calls.last.request.content)

    assert "recurrence" not in body, (
        f"recurrence must be omitted on minimal create — "
        f"backend rejects null recurrence on Task payloads. body={body!r}"
    )
    assert "recurring_type" not in body, (
        f"recurring_type must be omitted on minimal create. body={body!r}"
    )

    parsed = json.loads(result)
    assert parsed["id"] == TASK_ID


@respx.mock
@pytest.mark.asyncio
async def test_create_task_minimal_omits_other_unset_optional_keys(fastmcp):
    """Optional fields the caller didn't pass must be omitted, not sent as null.

    ``labels``, ``parent_id``, ``assignee``, ``complete_by``, ``productive``,
    ``desire`` should all be absent when not provided. Sending them as null
    pollutes the wire and risks future backend strictness.
    """
    route = respx.post(BASE + "/tasks").mock(
        return_value=httpx.Response(201, json=CREATED_TASK_ENVELOPE)
    )

    tool = _registered_tool(fastmcp, "create_task")
    await _invoke(tool, title="Demo", description="Test")

    body = json.loads(route.calls.last.request.content)
    for key in (
        "labels",
        "parent_id",
        "assignee",
        "complete_by",
        "productive",
        "desire",
    ):
        assert key not in body, f"{key!r} must be omitted on minimal create. body={body!r}"


@respx.mock
@pytest.mark.asyncio
async def test_create_task_passes_recurrence_when_explicitly_provided(fastmcp):
    """When the caller does pass recurrence, it must reach the backend."""
    route = respx.post(BASE + "/tasks").mock(
        return_value=httpx.Response(201, json=CREATED_TASK_ENVELOPE)
    )

    tool = _registered_tool(fastmcp, "create_task")
    recurrence = {"type": "daily"}
    await _invoke(
        tool,
        title="Daily review",
        description="Look at the inbox",
        recurrence=recurrence,
        recurring_type="habit",
    )

    body = json.loads(route.calls.last.request.content)
    assert body["recurrence"] == recurrence
    assert body["recurring_type"] == "habit"
    assert body["title"] == "Daily review"


@respx.mock
@pytest.mark.asyncio
async def test_create_task_passes_provided_optional_scalars(fastmcp):
    """Optional scalars the caller does pass must reach the backend verbatim."""
    route = respx.post(BASE + "/tasks").mock(
        return_value=httpx.Response(201, json=CREATED_TASK_ENVELOPE)
    )

    tool = _registered_tool(fastmcp, "create_task")
    await _invoke(
        tool,
        title="Demo",
        description="Test",
        labels=["Docs"],
        complete_by="2026-04-30T23:59:59Z",
        productive=0.5,
        desire=0.5,
    )

    body = json.loads(route.calls.last.request.content)
    assert body["labels"] == ["Docs"]
    assert body["complete_by"] == "2026-04-30T23:59:59Z"
    assert body["productive"] == 0.5
    assert body["desire"] == 0.5
    assert "recurrence" not in body
    assert "recurring_type" not in body
