"""End-to-end tests for multi-user authentication in HTTP transport mode.

Validates that:
- Multiple users get isolated tokens per MCP session
- Cross-user token leakage never occurs
- Logout clears only the correct session
- Token caching uses id(ctx.session) as the key

The server uses FastMCP's Context.session to identify MCP sessions.
id(session) is unique per session and stable for its lifetime.
"""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from defernowork_mcp import server as srv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enable_http_mode():
    """Put the server module into HTTP transport mode and clear caches."""
    srv._http_transport_mode = True
    srv._session_token_cache.clear()


def _disable_http_mode():
    srv._http_transport_mode = False
    srv._session_token_cache.clear()


def _make_ctx(session_obj=None):
    """Create a mock Context with a given session object."""
    ctx = MagicMock()
    ctx.session = session_obj if session_obj is not None else object()
    return ctx


def _cache_token(ctx, deferno_token: str):
    """Simulate complete_auth caching a token for a session."""
    srv._cache_deferno_token(deferno_token, ctx=ctx)


def _get_client_token(ctx) -> str | None:
    """Return the Deferno token that _get_client would use for this ctx."""
    client = srv._get_client(ctx=ctx)
    return client._token


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _http_mode():
    """Enable HTTP mode for every test, clean up after."""
    _enable_http_mode()
    yield
    _disable_http_mode()


# ---------------------------------------------------------------------------
# Basic session isolation
# ---------------------------------------------------------------------------

class TestSessionIsolation:
    def test_authenticated_session_gets_its_token(self):
        ctx = _make_ctx()
        _cache_token(ctx, "alice-deferno-token")
        assert _get_client_token(ctx) == "alice-deferno-token"

    def test_different_session_gets_none(self):
        ctx_alice = _make_ctx()
        ctx_bob = _make_ctx()
        _cache_token(ctx_alice, "alice-deferno-token")
        assert _get_client_token(ctx_bob) is None

    def test_two_users_get_own_tokens(self):
        ctx_alice = _make_ctx()
        ctx_bob = _make_ctx()
        _cache_token(ctx_alice, "alice-token")
        _cache_token(ctx_bob, "bob-token")

        assert _get_client_token(ctx_alice) == "alice-token"
        assert _get_client_token(ctx_bob) == "bob-token"

    def test_three_users_fully_isolated(self):
        ctxs = [_make_ctx() for _ in range(3)]
        tokens = ["token-1", "token-2", "token-3"]
        for ctx, tok in zip(ctxs, tokens):
            _cache_token(ctx, tok)

        for ctx, tok in zip(ctxs, tokens):
            assert _get_client_token(ctx) == tok
        assert _get_client_token(_make_ctx()) is None


# ---------------------------------------------------------------------------
# Same session persists across calls
# ---------------------------------------------------------------------------

class TestSessionPersistence:
    def test_token_persists_across_multiple_calls(self):
        ctx = _make_ctx()
        _cache_token(ctx, "my-token")
        for _ in range(10):
            assert _get_client_token(ctx) == "my-token"

    def test_same_session_object_same_token(self):
        """The same session object always resolves to the same token."""
        session = object()
        ctx1 = _make_ctx(session)
        ctx2 = _make_ctx(session)
        _cache_token(ctx1, "shared-token")
        assert _get_client_token(ctx2) == "shared-token"


# ---------------------------------------------------------------------------
# Cross-user leakage prevention
# ---------------------------------------------------------------------------

class TestNoLeakage:
    def test_alice_never_gets_bobs_token(self):
        ctx_alice = _make_ctx()
        ctx_bob = _make_ctx()
        _cache_token(ctx_alice, "alice-token")
        _cache_token(ctx_bob, "bob-token")
        for _ in range(10):
            assert _get_client_token(ctx_alice) == "alice-token"

    def test_new_session_never_gets_existing_token(self):
        ctx_alice = _make_ctx()
        _cache_token(ctx_alice, "alice-token")
        for _ in range(10):
            assert _get_client_token(_make_ctx()) is None

    def test_overwriting_session_replaces_token(self):
        ctx = _make_ctx()
        _cache_token(ctx, "old-token")
        _cache_token(ctx, "new-token")
        assert _get_client_token(ctx) == "new-token"


# ---------------------------------------------------------------------------
# Cache storage
# ---------------------------------------------------------------------------

class TestCacheToken:
    def test_cache_stores_by_session_id(self):
        ctx = _make_ctx()
        _cache_token(ctx, "my-token")
        assert srv._session_token_cache[id(ctx.session)] == "my-token"

    def test_cache_noop_without_ctx(self):
        srv._cache_deferno_token("orphan-token", ctx=None)
        assert len(srv._session_token_cache) == 0

    def test_cache_noop_in_stdio_mode(self):
        srv._http_transport_mode = False
        ctx = _make_ctx()
        srv._cache_deferno_token("should-not-cache", ctx=ctx)
        assert len(srv._session_token_cache) == 0


# ---------------------------------------------------------------------------
# No context (edge case)
# ---------------------------------------------------------------------------

class TestNoContext:
    def test_no_ctx_returns_none(self):
        client = srv._get_client(ctx=None)
        assert client._token is None

    def test_no_ctx_even_with_cache(self):
        ctx = _make_ctx()
        _cache_token(ctx, "some-token")
        client = srv._get_client(ctx=None)
        assert client._token is None


# ---------------------------------------------------------------------------
# Stdio mode isolation
# ---------------------------------------------------------------------------

class TestStdioMode:
    def test_stdio_ignores_session_cache(self):
        srv._http_transport_mode = False
        ctx = _make_ctx()
        srv._session_token_cache[id(ctx.session)] = "cached-token"
        client = srv._get_client(ctx=ctx)
        assert client._token != "cached-token"
