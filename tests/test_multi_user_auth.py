"""Tests for the OAuth-based multi-user authentication.

Tests the RedisStore and DefernoOAuthProvider in isolation using
a mock Redis (or fakeredis if available, otherwise plain dict mock).
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from defernowork_mcp.redis_store import RedisStore, _generate_token
from defernowork_mcp.oauth_provider import DefernoOAuthProvider, ACCESS_TOKEN_TTL


# ---------------------------------------------------------------------------
# In-memory Redis mock for testing (no real Redis needed)
# ---------------------------------------------------------------------------

class FakeRedis:
    """Minimal async Redis mock backed by a dict."""

    def __init__(self):
        self._data: dict[str, str] = {}
        self._ttls: dict[str, float] = {}

    async def set(self, key, value, ex=None):
        self._data[key] = value
        if ex:
            self._ttls[key] = time.time() + ex

    async def get(self, key):
        if key in self._ttls and time.time() > self._ttls[key]:
            del self._data[key]
            del self._ttls[key]
            return None
        return self._data.get(key)

    async def delete(self, *keys):
        for k in keys:
            self._data.pop(k, None)
            self._ttls.pop(k, None)

    async def xadd(self, stream, fields, maxlen=None, approximate=True):
        pass  # audit logging — no-op in tests

    async def aclose(self):
        pass

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._ops = []

    def set(self, key, value, ex=None):
        self._ops.append(("set", key, value, ex))
        return self

    def delete(self, *keys):
        self._ops.append(("delete", *keys))
        return self

    async def execute(self):
        for op in self._ops:
            if op[0] == "set":
                await self._redis.set(op[1], op[2], ex=op[3])
            elif op[0] == "delete":
                for k in op[1:]:
                    await self._redis.delete(k)
        self._ops.clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store():
    s = RedisStore.__new__(RedisStore)
    s._redis = FakeRedis()
    return s


# ---------------------------------------------------------------------------
# RedisStore tests
# ---------------------------------------------------------------------------

class TestRedisStoreClients:
    @pytest.mark.asyncio
    async def test_save_and_load_client(self, store):
        await store.save_client("c1", {"name": "Test"})
        result = await store.load_client("c1")
        assert result == {"name": "Test"}

    @pytest.mark.asyncio
    async def test_load_missing_client(self, store):
        assert await store.load_client("nonexistent") is None


class TestRedisStoreAuthCodes:
    @pytest.mark.asyncio
    async def test_save_and_load_auth_code(self, store):
        await store.save_auth_code("code1", {"client_id": "c1"}, meta={"user": "alice"})
        result = await store.load_auth_code("code1")
        assert result["client_id"] == "c1"

    @pytest.mark.asyncio
    async def test_auth_code_single_use(self, store):
        await store.save_auth_code("code1", {"client_id": "c1"})
        await store.load_auth_code("code1")  # first load
        assert await store.load_auth_code("code1") is None  # consumed

    @pytest.mark.asyncio
    async def test_auth_code_meta_survives_load(self, store):
        await store.save_auth_code("code1", {"client_id": "c1"}, meta={"token": "xyz"})
        await store.load_auth_code("code1")  # consumes code, not meta
        meta = await store.load_auth_code_meta("code1")
        assert meta["token"] == "xyz"


class TestRedisStoreAccessTokens:
    @pytest.mark.asyncio
    async def test_save_and_load_access_token(self, store):
        await store.save_access_token("tok1", {
            "token": "tok1",
            "client_id": "c1",
            "scopes": ["read"],
            "deferno_token": "deferno-abc",
        })
        result = await store.load_access_token("tok1")
        assert result["client_id"] == "c1"

    @pytest.mark.asyncio
    async def test_deferno_token_mapping(self, store):
        await store.save_access_token("tok1", {
            "token": "tok1",
            "client_id": "c1",
            "scopes": [],
            "deferno_token": "backend-token",
        })
        assert await store.load_deferno_token("tok1") == "backend-token"

    @pytest.mark.asyncio
    async def test_delete_access_token(self, store):
        await store.save_access_token("tok1", {
            "token": "tok1", "client_id": "c1", "scopes": [],
            "deferno_token": "d1",
        })
        await store.delete_access_token("tok1")
        assert await store.load_access_token("tok1") is None
        assert await store.load_deferno_token("tok1") is None


class TestRedisStoreRefreshTokens:
    @pytest.mark.asyncio
    async def test_save_and_load_refresh_token(self, store):
        await store.save_refresh_token("ref1", {"client_id": "c1"})
        result = await store.load_refresh_token("ref1")
        assert result["client_id"] == "c1"

    @pytest.mark.asyncio
    async def test_delete_refresh_token(self, store):
        await store.save_refresh_token("ref1", {"client_id": "c1"})
        await store.delete_refresh_token("ref1")
        assert await store.load_refresh_token("ref1") is None


class TestRedisStoreIsolation:
    """Verify that tokens from different users don't leak."""

    @pytest.mark.asyncio
    async def test_two_users_isolated(self, store):
        await store.save_access_token("alice-tok", {
            "token": "alice-tok", "client_id": "c1", "scopes": [],
            "deferno_token": "alice-backend",
        })
        await store.save_access_token("bob-tok", {
            "token": "bob-tok", "client_id": "c2", "scopes": [],
            "deferno_token": "bob-backend",
        })
        assert await store.load_deferno_token("alice-tok") == "alice-backend"
        assert await store.load_deferno_token("bob-tok") == "bob-backend"
        assert await store.load_deferno_token("eve-tok") is None

    @pytest.mark.asyncio
    async def test_revoke_one_user_doesnt_affect_other(self, store):
        await store.save_access_token("alice-tok", {
            "token": "alice-tok", "client_id": "c1", "scopes": [],
            "deferno_token": "alice-backend",
        })
        await store.save_access_token("bob-tok", {
            "token": "bob-tok", "client_id": "c2", "scopes": [],
            "deferno_token": "bob-backend",
        })
        await store.delete_access_token("alice-tok")
        assert await store.load_deferno_token("alice-tok") is None
        assert await store.load_deferno_token("bob-tok") == "bob-backend"


# ---------------------------------------------------------------------------
# Token generation
# ---------------------------------------------------------------------------

class TestTokenGeneration:
    def test_generates_64_char_hex(self):
        token = _generate_token()
        assert len(token) == 64
        int(token, 16)  # must be valid hex

    def test_tokens_are_unique(self):
        tokens = {_generate_token() for _ in range(100)}
        assert len(tokens) == 100


# ---------------------------------------------------------------------------
# Stdio mode (no Redis)
# ---------------------------------------------------------------------------

class TestStdioMode:
    @pytest.mark.asyncio
    async def test_get_client_stdio_does_not_use_redis(self):
        """In stdio mode, _get_client_async should use env/disk, not Redis."""
        from defernowork_mcp import server as srv
        srv._http_transport_mode = False
        srv._redis_store = None
        # Should not raise — resolves token from env or disk, not Redis
        client = await srv._get_client_async()
        assert client is not None
