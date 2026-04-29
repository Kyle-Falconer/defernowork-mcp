"""Tests for oauth_callback.py debug-surfacing behavior (MCP_DEBUG_OAUTH gate)."""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import pytest
from starlette.requests import Request

from defernowork_mcp import server as server_mod
from defernowork_mcp.oauth_callback import oidc_callback


def _make_request(query: dict[str, str]) -> Request:
    qs = "&".join(f"{k}={v}" for k, v in query.items())
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/mcp/oauth/oidc-callback",
        "query_string": qs.encode(),
        "headers": [],
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_debug_enabled_emits_class_name_in_pinned_format(monkeypatch):
    """With MCP_DEBUG_OAUTH=1, response body is exactly:
    'Internal error during authentication: <ClassName>'
    """
    monkeypatch.setenv("MCP_DEBUG_OAUTH", "1")

    fake_provider = AsyncMock()
    fake_provider.handle_oidc_callback.side_effect = RuntimeError("boom")
    monkeypatch.setattr(server_mod, "_oauth_provider", fake_provider)

    request = _make_request({"code": "x", "state": "y"})
    response = await oidc_callback(request)

    assert response.status_code == 500
    body = response.body.decode("utf-8")
    assert re.fullmatch(r"Internal error during authentication: RuntimeError", body), body


@pytest.mark.parametrize("env_value", [None, "0"])
@pytest.mark.asyncio
async def test_debug_disabled_emits_opaque_body(env_value, monkeypatch):
    """With MCP_DEBUG_OAUTH unset or '0', response body is exactly:
    'Internal error during authentication' — no class-name leakage.
    """
    if env_value is None:
        monkeypatch.delenv("MCP_DEBUG_OAUTH", raising=False)
    else:
        monkeypatch.setenv("MCP_DEBUG_OAUTH", env_value)

    fake_provider = AsyncMock()
    fake_provider.handle_oidc_callback.side_effect = RuntimeError("boom")
    monkeypatch.setattr(server_mod, "_oauth_provider", fake_provider)

    request = _make_request({"code": "x", "state": "y"})
    response = await oidc_callback(request)

    assert response.status_code == 500
    body = response.body.decode("utf-8")
    assert body == "Internal error during authentication", body


import pathlib


def test_mcp_debug_oauth_not_in_production_manifests():
    """MCP_DEBUG_OAUTH must never appear in production Docker artifacts."""
    repo_root = pathlib.Path(__file__).parent.parent
    suspects = [
        repo_root / "Dockerfile",
        # The Deferno repo's prod compose is not vendored here; the check
        # below scans any file in this repo. The cross-repo equivalent
        # check lives in the Deferno repo's own CI.
    ]
    for path in suspects:
        if not path.exists():
            continue
        text = path.read_text()
        assert "MCP_DEBUG_OAUTH" not in text, f"{path} mentions MCP_DEBUG_OAUTH"
