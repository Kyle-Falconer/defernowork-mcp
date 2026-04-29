"""Helper-level tests: _compact, server creation, _generate_token, stdio mode.

Migrated verbatim from test_server.py and test_multi_user_auth.py per the
spec's "Migration of existing tests" table. No assertion is dropped.
"""

from __future__ import annotations

import pytest

from defernowork_mcp.server import _compact, _UNSET, create_server, DEFAULT_BASE_URL
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


def test_create_server_returns_fastmcp():
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
