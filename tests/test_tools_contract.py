"""MCP tool-layer contract — parametrized over fixtures with ``mcp_tool`` set.

Catches argument coercion / serialization bugs in tools/*.py that the
client-layer test would miss.
"""

from __future__ import annotations

import inspect
import json
import re
from typing import Any

import pytest
import respx

from defernowork_mcp import server as srv
from defernowork_mcp.client import DefernoClient
from tests.spec_runner import (
    Fixture,
    PLACEHOLDER_UUID,
    assert_response_matches_shape,
    discover_backend_fixtures,
    substitute_path,
    wrap_envelope_data,
)

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

BASE = "http://test:3000/api"


def _tool_fixtures() -> list[Fixture]:
    return [f for f in discover_backend_fixtures() if f.mcp_tool]


def _ids(fixtures: list[Fixture]) -> list[str]:
    return [f.operation for f in fixtures]


@pytest.fixture
def fastmcp_with_stub_client(monkeypatch):
    """Wire a fresh FastMCP server whose ``_get_client_async`` returns a
    DefernoClient pointed at our respx-mocked ``BASE``.

    The patch must happen *before* ``create_server()`` so that the
    ``get_client`` closure captured by each ``register_*`` call uses the stub.
    """

    async def _stub_get_client_async(ctx=None):
        return DefernoClient(base_url=BASE, token="test-token")

    monkeypatch.setattr(srv, "_get_client_async", _stub_get_client_async)
    monkeypatch.setattr(srv, "_http_transport_mode", False)
    return srv.create_server()


def _registered_tool(mcp, name: str):
    """Look up a registered tool by name from a FastMCP instance.

    FastMCP stores tools in ``mcp._tool_manager._tools`` (a dict keyed by
    tool name). Falls back to probing ``tool_manager`` and the ``tools``
    attribute name for forward-compatibility.
    """
    tools = getattr(mcp, "_tool_manager", None) or getattr(mcp, "tool_manager", None)
    if tools is not None:
        for attr in ("_tools", "tools"):
            tool_map = getattr(tools, attr, None)
            if isinstance(tool_map, dict) and name in tool_map:
                return tool_map[name]
    # Last-resort: direct attribute on the mcp object
    fn = getattr(mcp, name, None)
    if fn is not None:
        return fn
    raise LookupError(f"tool {name!r} not registered on this FastMCP instance")


def _tool_kwargs(fixture: Fixture, tool_fn) -> dict[str, Any]:
    """Build keyword-argument dict for invoking a tool.

    If ``fixture.mcp_tool_args_from_example`` is non-empty, use only those
    keys (explicit list takes precedence).  Otherwise fall back to all keys
    in the body/query example that the tool's signature actually accepts —
    this handles fixtures like ``tasks.fold`` and ``tasks.split`` whose
    ``mcp_tool_args_from_example`` is ``[]`` but whose tools have required
    positional args (``title``, ``first_title``, etc.).
    """
    body = fixture.request.get("body") or {}
    body_example = body.get("example") or {}
    query = fixture.request.get("query") or {}
    query_example = query.get("example") or {}

    explicit_keys = fixture.mcp_tool_args_from_example
    if explicit_keys:
        return {
            k: body_example.get(k, query_example.get(k))
            for k in explicit_keys
            if k in body_example or k in query_example
        }

    # Fall back: intersect example keys with the tool's accepted parameters
    accepted = set(inspect.signature(tool_fn).parameters)
    kwargs: dict[str, Any] = {}
    for k, v in body_example.items():
        if k in accepted:
            kwargs[k] = v
    for k, v in query_example.items():
        if k in accepted:
            kwargs[k] = v
    return kwargs


def _singularize(plural: str) -> str:
    """Drop hyphens, drop English plural suffix.

    Examples: ``tasks`` -> ``task``, ``saved-searches`` -> ``saved_search``,
    ``feedback`` -> ``feedback`` (no change).
    """
    plural = plural.replace("-", "_")
    if plural.endswith("ies"):
        return plural[:-3] + "y"
    for suffix in ("ches", "shes", "xes", "ses"):
        if plural.endswith(suffix):
            return plural[:-2]
    if plural.endswith("s") and not plural.endswith("ss"):
        return plural[:-1]
    return plural


def _tool_path_kwarg(fixture: Fixture) -> dict[str, Any]:
    """Build kwargs for path placeholders.

    Convention:
    - ``{id}`` becomes ``<resource>_id`` derived from the segment immediately
      preceding the placeholder (e.g. ``/chores/{id}`` -> ``chore_id``).
    - Any other placeholder name is passed verbatim
      (e.g. ``{task_id}`` -> ``task_id``, ``{date}`` -> ``date``).
    """
    segments = fixture.path_template.split("/")
    kwargs: dict[str, Any] = {}
    for i, seg in enumerate(segments):
        m = re.match(r"^\{(\w+)\}$", seg)
        if not m:
            continue
        name = m.group(1)
        if name == "id" and i > 0:
            resource = _singularize(segments[i - 1])
            kwargs[f"{resource}_id"] = PLACEHOLDER_UUID
        else:
            kwargs[name] = PLACEHOLDER_UUID
    return kwargs


async def _invoke_tool(tool, kwargs: dict[str, Any]) -> Any:
    """Call a registered FastMCP tool object."""
    if hasattr(tool, "fn"):
        return await tool.fn(**kwargs)
    if hasattr(tool, "handler"):
        return await tool.handler(**kwargs)
    if callable(tool):
        return await tool(**kwargs)
    raise TypeError(
        f"don't know how to invoke tool object of type {type(tool).__name__}"
    )


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", _tool_fixtures(), ids=_ids(_tool_fixtures()))
async def test_tool_invokes_endpoint_and_returns_payload(
    fixture: Fixture, fastmcp_with_stub_client
):
    success_idx = next(
        (i for i, r in enumerate(fixture.responses) if 200 <= r["status"] < 300),
        None,
    )
    if success_idx is None:
        pytest.skip(f"{fixture.operation}: no 2xx response")

    spec = fixture.responses[success_idx]
    url = BASE + substitute_path(fixture.path_template)
    if spec["status"] == 204:
        respx.route(method=fixture.method, url__startswith=url).respond(204)
    else:
        respx.route(method=fixture.method, url__startswith=url).respond(
            status_code=spec["status"],
            json=wrap_envelope_data(spec.get("example")),
        )

    tool = _registered_tool(fastmcp_with_stub_client, fixture.mcp_tool)

    kwargs = {**_tool_path_kwarg(fixture), **_tool_kwargs(fixture, tool.fn)}
    result = await _invoke_tool(tool, kwargs)

    # Tools serialize their output as JSON strings (see tools/tasks.py).
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except ValueError:
            parsed = result
    else:
        parsed = result

    assert respx.calls.called, f"{fixture.operation}: tool did not call backend"
    if spec["status"] != 204 and isinstance(parsed, (dict, list)):
        assert_response_matches_shape(fixture, success_idx, parsed)
