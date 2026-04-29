"""Live smoke test against a real Deferno backend.

Gated behind ``@pytest.mark.live`` so it does not run in the default suite.
Verifies the v0.1 envelope unwrap actually works end-to-end — the MCP
returning ``{"version": "0.1", "data": [...]}`` shaped responses to its
callers (instead of unwrapping to the inner ``data``) was the original
"the MCP stopped working" symptom this whole branch was built to detect.

Setup:
  DEFERNO_BASE_URL  defaults to http://127.0.0.1:3000/api (local dev backend)
  DEFERNO_TOKEN     required; obtain via `defernowork-mcp auth` for the
                    stdio path, or via the backend's /internal/mcp-session
                    for HTTP transport. The fixture self-skips if absent.
"""

from __future__ import annotations

import os

import pytest

from defernowork_mcp.client import DefernoClient

pytestmark = pytest.mark.live

ENVELOPE_KEYS = {"version", "data", "error"}


def _envelope_leak(value: object) -> bool:
    """True when `value` still looks like a raw v0.1 envelope.

    The whole point of `client._request` is to unwrap; if a caller sees a
    dict with all three envelope keys, the unwrap regressed.
    """
    return isinstance(value, dict) and ENVELOPE_KEYS.issubset(value.keys())


@pytest.fixture
def base_url() -> str:
    return os.environ.get("DEFERNO_BASE_URL", "http://127.0.0.1:3000/api")


@pytest.fixture
def token() -> str:
    tok = os.environ.get("DEFERNO_TOKEN")
    if not tok:
        pytest.skip(
            "DEFERNO_TOKEN not set — provide a bearer token (e.g. via the "
            "backend's /internal/mcp-session) to run the live smoke."
        )
    return tok


@pytest.fixture
async def client(base_url: str, token: str):
    async with DefernoClient(base_url=base_url, token=token) as c:
        yield c


@pytest.mark.asyncio
async def test_whoami_returns_unwrapped_user(client):
    user = await client.whoami()
    assert isinstance(user, dict), (
        f"whoami returned {type(user).__name__}, expected dict"
    )
    assert not _envelope_leak(user), (
        f"v0.1 envelope leaked into whoami response — client._request did "
        f"not unwrap. Got: {user!r}"
    )
    assert "username" in user or "id" in user, (
        f"whoami response is missing user identity fields: {user!r}"
    )


@pytest.mark.asyncio
async def test_list_tasks_returns_unwrapped_list(client):
    tasks = await client.list_tasks()
    assert isinstance(tasks, list), (
        f"list_tasks returned {type(tasks).__name__}, expected list. "
        f"If the value is a dict with version/data/error keys, the "
        f"envelope unwrap regressed."
    )
    assert not _envelope_leak(tasks), (
        f"v0.1 envelope leaked into list_tasks response: {tasks!r}"
    )
    if tasks:
        first = tasks[0]
        assert isinstance(first, dict), (
            f"task list element is {type(first).__name__}, expected dict"
        )
        assert "id" in first and "title" in first, (
            f"task is missing required fields (id, title): {first!r}"
        )


@pytest.mark.asyncio
async def test_get_daily_plan_returns_unwrapped_list(client):
    plan = await client.get_daily_plan()
    assert isinstance(plan, list), (
        f"get_daily_plan returned {type(plan).__name__}, expected list"
    )
    assert not _envelope_leak(plan), (
        f"v0.1 envelope leaked into get_daily_plan response: {plan!r}"
    )
