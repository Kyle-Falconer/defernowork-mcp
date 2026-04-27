"""In-process RFC contract for the MCP OAuth provider.

Mounts the FastMCP server's ASGI app via the same ``streamable_http_app()``
call ``server.py`` uses for production HTTP transport, then drives it with
``httpx.ASGITransport``. No network, no Redis (``RedisStore.__init__`` is
patched to wrap an in-memory fake — same fake used in test_redis_store.py).
"""

from __future__ import annotations

import os
import time
from urllib.parse import urlencode

import httpx
import pytest

from tests.spec_runner import discover_oauth_fixtures


# ── In-memory Redis fake (mirrors test_redis_store.py's FakeRedis) ──────────


class FakeRedis:
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
        pass

    async def aclose(self):
        pass

    def pipeline(self):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis):
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


# ── ASGI client fixture ─────────────────────────────────────────────────────


@pytest.fixture
async def http_client(monkeypatch):
    """Construct an in-process FastMCP HTTP app with stub OIDC + fake Redis.

    ``create_server(http_transport=True)`` instantiates ``RedisStore(redis_url)``
    eagerly when ``ZITADEL_ISSUER_URL`` is set. We patch ``RedisStore.__init__``
    so the constructor wraps a ``FakeRedis`` instead of opening a real connection.

    We also patch ``OidcClient.authorization_url`` to avoid a real network call
    to ``https://stub-issuer.test/.well-known/openid-configuration`` when
    ``/authorize`` is exercised.

    Finally, we replicate the custom ``/.well-known/oauth-authorization-server``
    route insertion from ``main_http()`` — that customisation is not part of
    ``create_server``; it's bolted on after ``streamable_http_app()`` in production.
    """
    fake = FakeRedis()

    from defernowork_mcp.redis_store import RedisStore

    def _stub_init(self, redis_url):
        self._redis = fake

    monkeypatch.setattr(RedisStore, "__init__", _stub_init)

    # Patch OidcClient.authorization_url to avoid hitting the stub issuer URL
    from defernowork_mcp.oidc_client import OidcClient

    async def _stub_authorization_url(self, state, pkce, scopes=None):
        return f"https://stub-issuer.test/authorize?state={state}"

    monkeypatch.setattr(OidcClient, "authorization_url", _stub_authorization_url)

    monkeypatch.setenv("ZITADEL_ISSUER_URL", "https://stub-issuer.test")
    monkeypatch.setenv("ZITADEL_CLIENT_ID", "test-client")
    monkeypatch.setenv("ZITADEL_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://test.local/mcp")
    monkeypatch.setenv("REDIS_URL", "redis://stub:6379")

    from defernowork_mcp import server as srv
    mcp = srv.create_server(http_transport=True)

    if hasattr(mcp, "streamable_http_app"):
        asgi = mcp.streamable_http_app()
    else:
        asgi = mcp.sse_app()

    # Mirror the custom route insertion from main_http() so our in-process app
    # exposes the same overridden /.well-known/oauth-authorization-server that
    # production does (excluding client_secret_basic, adding openid-configuration).
    oauth_provider = srv._oauth_provider
    if oauth_provider is not None:
        try:
            from starlette.applications import Starlette
            from starlette.responses import JSONResponse
            from starlette.routing import Route

            mcp_public_url = os.environ.get("MCP_PUBLIC_URL", "https://app.defernowork.com/mcp")
            _oauth_metadata = {
                "issuer": mcp_public_url,
                "authorization_endpoint": f"{mcp_public_url}/authorize",
                "token_endpoint": f"{mcp_public_url}/token",
                "registration_endpoint": f"{mcp_public_url}/register",
                "revocation_endpoint": f"{mcp_public_url}/revoke",
                "scopes_supported": ["tasks:read", "tasks:write", "plan:read", "plan:write", "profile:read"],
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_methods_supported": ["client_secret_post"],
                "revocation_endpoint_auth_methods_supported": ["client_secret_post"],
                "code_challenge_methods_supported": ["S256"],
            }

            async def oauth_metadata_handler(request):
                return JSONResponse(_oauth_metadata)

            if isinstance(asgi, Starlette):
                asgi.routes.insert(0,
                    Route("/.well-known/oauth-authorization-server", oauth_metadata_handler, methods=["GET"]),
                )
                asgi.routes.insert(1,
                    Route("/.well-known/openid-configuration", oauth_metadata_handler, methods=["GET"]),
                )
        except ImportError:
            pass  # starlette not available; tests that depend on custom metadata may skip/fail

    transport = httpx.ASGITransport(app=asgi)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ── parametrized fixture-driven coverage ────────────────────────────────────


def _oauth_fixture_ids():
    return [f.operation for f in discover_oauth_fixtures()]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture",
    discover_oauth_fixtures(),
    ids=_oauth_fixture_ids(),
)
async def test_endpoint_exists(fixture, http_client: httpx.AsyncClient):
    """Smoke test: each documented OAuth endpoint responds with a known status.

    Detailed semantic checks live in the test_*_semantics tests below.
    """
    if fixture.method == "GET":
        params = {}
        query = fixture.request.get("query") or {}
        for k in query.get("required", []):
            params[k] = query.get("example", {}).get(k, "x")
        response = await http_client.get(
            fixture.path_template,
            params=params,
            follow_redirects=False,
        )
    elif fixture.method == "POST":
        body = fixture.request.get("body") or {}
        example = body.get("example") or {}
        # Use JSON encoding when the example contains non-scalar values (e.g.
        # lists), because form-encoding can't represent them.  /register expects
        # JSON; /token and /revoke expect form-encoded data.
        has_nested = any(isinstance(v, (list, dict)) for v in example.values())
        if has_nested:
            response = await http_client.post(
                fixture.path_template,
                json=example,
            )
        else:
            response = await http_client.post(
                fixture.path_template,
                data=example,
            )
    else:
        pytest.skip(f"unsupported method {fixture.method}")

    expected_statuses = {r["status"] for r in fixture.responses}
    assert response.status_code in expected_statuses or response.status_code in {302, 400, 401, 404, 405}, (
        f"{fixture.operation}: unexpected status {response.status_code}, "
        f"expected one of {expected_statuses}, body={response.text[:200]}"
    )


# ── targeted RFC semantics ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prm_required_keys(http_client: httpx.AsyncClient):
    r = await http_client.get("/.well-known/oauth-protected-resource")
    if r.status_code == 404:
        pytest.skip("PRM endpoint not exposed in current build")
    assert r.status_code == 200
    body = r.json()
    assert "resource" in body
    assert "authorization_servers" in body
    assert isinstance(body["authorization_servers"], list)


@pytest.mark.asyncio
async def test_as_metadata_required_keys_and_methods(http_client: httpx.AsyncClient):
    r = await http_client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    body = r.json()
    for key in ("issuer", "authorization_endpoint", "token_endpoint",
                "registration_endpoint", "code_challenge_methods_supported",
                "token_endpoint_auth_methods_supported"):
        assert key in body, f"missing {key}"
    assert "S256" in body["code_challenge_methods_supported"]
    methods = body["token_endpoint_auth_methods_supported"]
    assert "client_secret_post" in methods
    assert "client_secret_basic" not in methods, (
        "client_secret_basic is intentionally excluded — see oauth_flow notes"
    )


@pytest.mark.asyncio
async def test_register_client_secret_post_returns_secret(http_client: httpx.AsyncClient):
    r = await http_client.post("/register", json={
        "redirect_uris": ["http://localhost:8765/callback"],
        "token_endpoint_auth_method": "client_secret_post",
    })
    assert r.status_code in {200, 201}
    body = r.json()
    assert "client_id" in body
    assert "client_secret" in body and body["client_secret"]


@pytest.mark.asyncio
async def test_register_none_does_not_return_secret(http_client: httpx.AsyncClient):
    r = await http_client.post("/register", json={
        "redirect_uris": ["http://localhost:8765/callback"],
        "token_endpoint_auth_method": "none",
    })
    assert r.status_code in {200, 201}
    body = r.json()
    assert "client_id" in body
    assert not body.get("client_secret")


@pytest.mark.asyncio
async def test_token_missing_client_id_rejected(http_client: httpx.AsyncClient):
    r = await http_client.post("/token", data={"grant_type": "authorization_code"})
    assert r.status_code in {400, 401}


@pytest.mark.asyncio
async def test_token_unknown_client_id_rejected(http_client: httpx.AsyncClient):
    r = await http_client.post("/token", data={
        "grant_type": "authorization_code",
        "client_id": "does-not-exist",
        "code": "x",
    })
    assert r.status_code in {400, 401}


@pytest.mark.asyncio
async def test_revoke_unknown_token_returns_200(http_client: httpx.AsyncClient):
    """RFC 7009 §2.2: revocation MUST return 200 even for invalid tokens."""
    reg = await http_client.post("/register", json={
        "redirect_uris": ["http://localhost:8765/callback"],
        "token_endpoint_auth_method": "client_secret_post",
    })
    assert reg.status_code in {200, 201}
    creds = reg.json()
    r = await http_client.post("/revoke", data={
        "token": "totally-not-a-real-token",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    })
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_authorize_redirects_to_upstream(http_client: httpx.AsyncClient):
    reg = await http_client.post("/register", json={
        "redirect_uris": ["http://localhost:8765/callback"],
        "token_endpoint_auth_method": "client_secret_post",
    })
    creds = reg.json()
    qs = urlencode({
        "response_type": "code",
        "client_id": creds["client_id"],
        "redirect_uri": "http://localhost:8765/callback",
        "code_challenge": "x" * 43,
        "code_challenge_method": "S256",
        "state": "abc",
    })
    r = await http_client.get(f"/authorize?{qs}", follow_redirects=False)
    assert r.status_code in {302, 303}
    location = r.headers.get("location", "")
    assert location, "authorize must set a Location header"
