"""Tests for server helpers and tool registration."""

from __future__ import annotations

import pytest

from defernowork_mcp.server import _compact, _UNSET, create_server, DEFAULT_BASE_URL


# ── _compact with _UNSET sentinel ─────────────────────────────────────────


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


# ── Server creation ───────────────────────────────────────────────────────


def test_create_server_returns_fastmcp():
    server = create_server()
    assert server is not None


def test_default_base_url_is_localhost():
    assert DEFAULT_BASE_URL == "http://127.0.0.1:3000"
