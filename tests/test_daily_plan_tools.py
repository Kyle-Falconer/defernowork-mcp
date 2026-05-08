"""Tests for daily plan MCP tools — tz parameter plumbing."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Load defernowork_mcp.tools.daily_plan without triggering the circular
# import chain (tools/__init__ -> auth.py -> server.py -> tools/__init__).
# We achieve this by loading the module file directly via importlib.util after
# ensuring its transitive dependencies are in sys.modules.
# ---------------------------------------------------------------------------

def _load_daily_plan_register():
    """Return the `register` function from tools/daily_plan.py directly."""
    src_root = Path(__file__).parent.parent / "src"

    # Ensure the package stub is present so relative imports in daily_plan work.
    pkg_name = "defernowork_mcp"
    if pkg_name not in sys.modules:
        pkg_spec = importlib.util.spec_from_file_location(
            pkg_name, src_root / pkg_name / "__init__.py",
            submodule_search_locations=[str(src_root / pkg_name)],
        )
        pkg = importlib.util.module_from_spec(pkg_spec)
        sys.modules[pkg_name] = pkg
        pkg_spec.loader.exec_module(pkg)

    # Load client module (needed by daily_plan).
    client_name = "defernowork_mcp.client"
    if client_name not in sys.modules:
        client_spec = importlib.util.spec_from_file_location(
            client_name, src_root / pkg_name / "client.py"
        )
        client_mod = importlib.util.module_from_spec(client_spec)
        sys.modules[client_name] = client_mod
        client_spec.loader.exec_module(client_mod)

    # Load daily_plan.py directly — avoids tools/__init__ entirely.
    dp_name = "defernowork_mcp.tools.daily_plan"
    if dp_name in sys.modules:
        return sys.modules[dp_name].register

    dp_spec = importlib.util.spec_from_file_location(
        dp_name, src_root / pkg_name / "tools" / "daily_plan.py"
    )
    dp_mod = importlib.util.module_from_spec(dp_spec)
    sys.modules[dp_name] = dp_mod
    dp_spec.loader.exec_module(dp_mod)
    return dp_mod.register


register = _load_daily_plan_register()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_daily_plan_passes_tz_to_client():
    captured = {}

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get_daily_plan(self, date=None, tz=None):
            captured["date"] = date
            captured["tz"] = tz
            return []

    handlers = {}

    def fake_tool():
        def decorator(fn):
            handlers[fn.__name__] = fn
            return fn
        return decorator

    mcp = MagicMock()
    mcp.tool = fake_tool

    register(mcp, get_client=AsyncMock(return_value=FakeClient()),
             format_error=lambda e: str(e))

    await handlers["get_daily_plan"](
        date="2026-05-07",
        tz="America/Los_Angeles",
        ctx=None,
    )
    assert captured == {"date": "2026-05-07", "tz": "America/Los_Angeles"}


@pytest.mark.asyncio
async def test_get_calendar_events_passes_tz_to_client():
    captured = {}

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get_calendar_events(self, start, end, tz=None):
            captured["start"] = start
            captured["end"] = end
            captured["tz"] = tz
            return []

    handlers = {}

    def fake_tool():
        def decorator(fn):
            handlers[fn.__name__] = fn
            return fn
        return decorator

    mcp = MagicMock()
    mcp.tool = fake_tool

    register(mcp, get_client=AsyncMock(return_value=FakeClient()),
             format_error=lambda e: str(e))

    await handlers["get_calendar_events"](
        start="2026-05-07",
        end="2026-05-08",
        tz="America/Los_Angeles",
        ctx=None,
    )
    assert captured == {
        "start": "2026-05-07",
        "end": "2026-05-08",
        "tz": "America/Los_Angeles",
    }
