"""Helper-level tests: _compact, server creation, _generate_token, stdio mode.

Migrated verbatim from test_server.py and test_multi_user_auth.py per the
spec's "Migration of existing tests" table. No assertion is dropped.
"""

from __future__ import annotations

import pytest

from defernowork_mcp.server import (
    _compact,
    _UNSET,
    create_server,
    transport_security_settings,
    DEFAULT_BASE_URL,
)
from defernowork_mcp.redis_store import _generate_token


# ── _compact with _UNSET sentinel (from test_server.py) ─────────────────────


class TestCompact:
    def test_strips_unset_values(self):
        result = _compact({"a": 1, "b": _UNSET, "c": "hello"})
        assert result == {"a": 1, "c": "hello"}

    def test_preserves_none_values(self):
        """None means 'clear this field' and must be sent as JSON null."""
        result = _compact({"title": "keep", "complete_by": None, "desire": _UNSET})
        assert result == {"title": "keep", "complete_by": None}

    def test_preserves_false_and_zero(self):
        result = _compact({"flag": False, "count": 0, "gone": _UNSET})
        assert result == {"flag": False, "count": 0}

    def test_empty_dict(self):
        assert _compact({}) == {}

    def test_all_unset(self):
        assert _compact({"a": _UNSET, "b": _UNSET}) == {}

    def test_all_none(self):
        """All None values preserved — they're explicit clears."""
        result = _compact({"a": None, "b": None})
        assert result == {"a": None, "b": None}


# ── Server creation (from test_server.py) ───────────────────────────────────


def test_create_server_returns_mcpserver():
    server = create_server()
    assert server is not None


def test_default_base_url_is_localhost():
    assert DEFAULT_BASE_URL == "http://127.0.0.1:3000/api"


# ── Token generation (from test_multi_user_auth.py) ─────────────────────────


class TestTokenGeneration:
    def test_generates_64_char_hex(self):
        token = _generate_token()
        assert len(token) == 64
        int(token, 16)  # must be valid hex

    def test_tokens_are_unique(self):
        tokens = {_generate_token() for _ in range(100)}
        assert len(tokens) == 100


# ── Stdio mode (from test_multi_user_auth.py) ───────────────────────────────


class TestStdioMode:
    @pytest.mark.asyncio
    async def test_get_client_stdio_does_not_use_redis(self):
        """In stdio mode, _get_client_async should use env/disk, not Redis."""
        from defernowork_mcp import server as srv
        srv._http_transport_mode = False
        srv._redis_store = None
        client = await srv._get_client_async()
        assert client is not None


# ── Transport settings live on the transport, not the server ────────────────
#
# SDK 2.x took transport arguments off the server constructor. They belong to
# ``run()`` and to ``streamable_http_app()`` now, so ``main_http`` is where the
# HTTP ones are supplied.


class TestTransportSecuritySettings:
    def test_loopback_is_always_allowed(self, monkeypatch):
        monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
        settings = transport_security_settings()

        assert settings.enable_dns_rebinding_protection
        for host in ("localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"):
            assert host in settings.allowed_hosts

    def test_env_var_adds_hosts(self, monkeypatch):
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "app.example.com, mcp.example.com")
        settings = transport_security_settings()

        assert "app.example.com" in settings.allowed_hosts
        assert "mcp.example.com" in settings.allowed_hosts
        # The loopback defaults survive alongside them.
        assert "127.0.0.1" in settings.allowed_hosts

    def test_blank_env_var_leaves_only_the_defaults(self, monkeypatch):
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "   ")
        settings = transport_security_settings()

        assert set(settings.allowed_hosts) == {
            "localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*",
        }


def test_server_constructor_carries_no_transport_settings():
    """The server no longer knows about hosts, ports, or transport security."""
    fields = set(type(create_server().settings).model_fields)

    assert not fields & {
        "host", "port", "transport_security", "stateless_http", "json_response",
    }


def test_asgi_app_accepts_what_main_http_passes():
    """``main_http`` builds the app with exactly these arguments."""
    app = create_server(http_transport=True).streamable_http_app(
        transport_security=transport_security_settings(),
        host="0.0.0.0",
    )

    assert app is not None
