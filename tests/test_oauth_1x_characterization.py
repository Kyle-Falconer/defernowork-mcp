"""Characterization of the OAuth authorization server surface under mcp 1.x.

These tests record what the surface does today, running against the installed
mcp 1.28.0. They are the contract the SDK 2.x port has to satisfy. Where the
current behavior looks wrong, the test pins it anyway and the comment says so.

The surface has two owners:

  - The SDK owns the routes and their handlers. ``create_auth_routes`` mounts
    ``/authorize``, ``/token``, ``/register`` and ``/revoke``, plus its own
    ``/.well-known/oauth-authorization-server``. ``ClientAuthenticator`` decides
    how a client proves who it is. Every status code and error body below comes
    from the SDK unless a comment says otherwise.
  - This repo owns ``DefernoOAuthProvider`` (token issuance, storage, revocation
    side effects) and the two custom discovery routes that ``main_http()`` bolts
    on after ``streamable_http_app()``.

Each test names its owner. Behavior owned by the SDK can shift under the port;
behavior owned by the provider should not.

The app under test is the one ``main_http()`` assembles. The fixture replaces
``uvicorn.run`` with a capture function, so the entry point runs to completion
and hands over the app instead of serving it. Nothing here rebuilds a route or a
response body, so every assertion reads production wiring.

The app then runs in process over ``httpx.ASGITransport``. Redis is an in-memory
fake. The upstream OIDC leg is stubbed at three points: ``authorization_url``,
``exchange_code``, and ``DefernoOAuthProvider._get_deferno_session``. Stubbing
those three is what makes a real end-to-end token exchange possible, because
``handle_oidc_callback`` is the only thing that mints an MCP authorization code.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest


# ── Fixed inputs ────────────────────────────────────────────────────────────

MCP_PUBLIC_URL = "https://test.local/mcp"
BASE_URL = "http://test"
REDIRECT_URI = "http://localhost:8765/callback"

# The body of the two custom discovery routes, as main_http() builds it from
# MCP_PUBLIC_URL. It is repo-owned and the port must not change it. The SDK's own
# metadata body differs; test_as_metadata_shadows_the_sdk_route reads both.
EXPECTED_AS_METADATA = {
    "issuer": "https://test.local/mcp",
    "authorization_endpoint": "https://test.local/mcp/authorize",
    "token_endpoint": "https://test.local/mcp/token",
    "registration_endpoint": "https://test.local/mcp/register",
    "revocation_endpoint": "https://test.local/mcp/revoke",
    "scopes_supported": [
        "tasks:read", "tasks:write", "plan:read", "plan:write", "profile:read",
    ],
    "response_types_supported": ["code"],
    "grant_types_supported": ["authorization_code", "refresh_token"],
    "token_endpoint_auth_methods_supported": ["client_secret_post"],
    "revocation_endpoint_auth_methods_supported": ["client_secret_post"],
    "code_challenge_methods_supported": ["S256"],
}

# One year, from ACCESS_TOKEN_TTL in redis_store.py.
EXPECTED_EXPIRES_IN = 31_536_000


# ── In-memory Redis fake (mirrors test_oauth_provider_contract.py) ──────────


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


# ── PKCE ────────────────────────────────────────────────────────────────────


def pkce_pair() -> tuple[str, str]:
    """Return a real S256 verifier and challenge, built the way OidcPKCE does.

    The SDK verifies the challenge for real, so the happy path needs a genuine
    pair and the failure path needs a verifier from a different pair.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ── Harness ─────────────────────────────────────────────────────────────────


async def serve_one_route(route: Any, path: str) -> httpx.Response:
    """GET ``path`` from an app that holds ``route`` and nothing else.

    A Starlette ``Route`` is a plain object, so the same instance can go into a
    second app and call the same handler. That is how a shadowed route can be
    read without disturbing the app under test.
    """
    from starlette.applications import Starlette

    transport = httpx.ASGITransport(app=Starlette(routes=[route]))
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        return await client.get(path)


def routes_at(app: Any, path: str) -> list[Any]:
    """Every route the app registers at exactly ``path``, in match order."""
    return [route for route in app.routes if getattr(route, "path", None) == path]


@dataclass
class OAuthSurface:
    """The in-process app plus the shortcuts every test needs."""

    client: httpx.AsyncClient
    app: Any
    provider: Any

    async def register(self, **metadata: Any) -> httpx.Response:
        """POST /register with the given client metadata."""
        body: dict[str, Any] = {"redirect_uris": [REDIRECT_URI]}
        body.update(metadata)
        return await self.client.post("/register", json=body)

    async def register_ok(self, **metadata: Any) -> dict[str, Any]:
        response = await self.register(**metadata)
        assert response.status_code == 201, response.text
        return response.json()

    async def mint_code(
        self,
        creds: dict[str, Any],
        challenge: str,
        *,
        redirect_uri: str = REDIRECT_URI,
        scope: str | None = None,
        state: str = "client-state",
    ) -> str:
        """Drive /authorize and the OIDC callback to get an MCP auth code.

        The stubbed ``authorization_url`` echoes the provider's nonce back as
        the upstream ``state``. Feeding that nonce to the callback route is what
        completes the pending authorization and mints the code.
        """
        query = {
            "response_type": "code",
            "client_id": creds["client_id"],
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        if scope is not None:
            query["scope"] = scope

        redirect = await self.client.get(f"/authorize?{urlencode(query)}")
        assert redirect.status_code == 302, redirect.text
        nonce = parse_qs(urlparse(redirect.headers["location"]).query)["state"][0]

        callback = await self.client.get(
            f"/oauth/oidc-callback?code=upstream-code&state={nonce}",
        )
        assert callback.status_code == 302, callback.text
        return parse_qs(urlparse(callback.headers["location"]).query)["code"][0]

    async def mint_tokens(
        self,
        creds: dict[str, Any],
        *,
        scope: str | None = None,
    ) -> dict[str, Any]:
        """Run a complete authorization_code grant and return the token body."""
        verifier, challenge = pkce_pair()
        code = await self.mint_code(creds, challenge, scope=scope)
        form = {
            "grant_type": "authorization_code",
            "client_id": creds["client_id"],
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
        }
        if creds.get("client_secret"):
            form["client_secret"] = creds["client_secret"]
        response = await self.client.post("/token", data=form)
        assert response.status_code == 200, response.text
        return response.json()

    async def ping_mcp(self, access_token: str) -> httpx.Response:
        """Call the protected /mcp endpoint with a bearer token.

        This is the only way to prove a token is alive or dead through HTTP
        rather than through the store: the resource server resolves it with the
        same ``load_access_token`` the SDK's bearer middleware calls.
        """
        return await self.client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "characterization", "version": "1"},
                },
            },
        )


@pytest.fixture
async def surface(monkeypatch):
    """Run the production ``main_http()`` in process, with the OIDC leg stubbed.

    ``uvicorn.run`` is replaced by a capture function. ``main_http()`` therefore
    does everything it does in production -- build the server, call
    ``streamable_http_app()``, insert the two custom discovery routes at the
    front, append the OIDC callback -- and then hands the finished app over
    instead of serving it. The fixture builds no route and no response body of
    its own.

    ``create_server`` is wrapped to record the FastMCP instance, because the
    session manager behind ``/mcp`` is reachable only through it.

    Running the real entry point also runs its ``logging.basicConfig`` call,
    which sets the root logger level for the rest of the session.
    """
    fake = FakeRedis()

    from defernowork_mcp.redis_store import RedisStore

    def _stub_store_init(self, redis_url=None):
        self._redis = fake

    monkeypatch.setattr(RedisStore, "__init__", _stub_store_init)

    from defernowork_mcp.oidc_client import OidcClient, OidcIdentity

    async def _stub_authorization_url(self, state, pkce, scopes=None):
        return f"https://stub-issuer.test/authorize?state={state}"

    async def _stub_exchange_code(self, code, pkce_verifier):
        return OidcIdentity(
            subject="upstream-subject",
            username="tester",
            display_name="Tester",
            email="tester@example.test",
        )

    monkeypatch.setattr(OidcClient, "authorization_url", _stub_authorization_url)
    monkeypatch.setattr(OidcClient, "exchange_code", _stub_exchange_code)

    from defernowork_mcp.oauth_provider import DefernoOAuthProvider

    async def _stub_deferno_session(
        self, oidc_subject, oidc_username, mcp_client_id, mcp_client_name,
    ):
        return "deferno-session-token"

    monkeypatch.setattr(
        DefernoOAuthProvider, "_get_deferno_session", _stub_deferno_session,
    )

    monkeypatch.setenv("ZITADEL_ISSUER_URL", "https://stub-issuer.test")
    monkeypatch.setenv("ZITADEL_CLIENT_ID", "test-client")
    monkeypatch.setenv("ZITADEL_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("MCP_PUBLIC_URL", MCP_PUBLIC_URL)
    monkeypatch.setenv("REDIS_URL", "redis://stub:6379")
    monkeypatch.setenv("INTERNAL_SHARED_SECRET", "stub-secret")
    # DNS rebinding protection rejects any Host it was not told about, and the
    # ASGI transport sends the base URL's host.
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "test")

    import uvicorn

    from defernowork_mcp import server as srv

    # main_http() keeps neither the server nor the app it builds, so both are
    # taken as it goes past: the server from create_server, the app from the
    # uvicorn.run call that would otherwise start serving.
    servers: list[Any] = []
    real_create_server = srv.create_server

    def _recording_create_server(*args, **kwargs):
        server = real_create_server(*args, **kwargs)
        servers.append(server)
        return server

    captured: dict[str, Any] = {}

    def _capture_run(app, **kwargs):
        captured["app"] = app

    monkeypatch.setattr(srv, "create_server", _recording_create_server)
    monkeypatch.setattr(uvicorn, "run", _capture_run)

    original_provider = srv._oauth_provider
    original_store = srv._redis_store
    original_mode = srv._http_transport_mode
    try:
        srv.main_http()

        asgi = captured["app"]
        mcp = servers[-1]

        transport = httpx.ASGITransport(app=asgi)

        # streamable_http_app() puts the session manager behind the app's
        # lifespan, and ASGITransport never runs a lifespan. Starting it by hand
        # is what makes a live bearer token usable against /mcp.
        #
        # It runs in its own task because it wraps an anyio task group, and a
        # task group has to be entered and exited by the same task. Setup and
        # teardown of an async fixture are two different tasks.
        running = asyncio.Event()
        finished = asyncio.Event()

        async def run_session_manager() -> None:
            async with mcp.session_manager.run():
                running.set()
                await finished.wait()

        session_task = asyncio.create_task(run_session_manager())
        await running.wait()
        try:
            async with httpx.AsyncClient(
                transport=transport,
                base_url=BASE_URL,
                follow_redirects=False,
            ) as client:
                yield OAuthSurface(
                    client=client, app=asgi, provider=srv._oauth_provider,
                )
        finally:
            finished.set()
            await session_task
    finally:
        srv._oauth_provider = original_provider
        srv._redis_store = original_store
        srv._http_transport_mode = original_mode


# ── Discovery: the repo's custom AS metadata route ──────────────────────────


async def test_as_metadata_exact_url_status_and_body(surface: OAuthSurface):
    """Repo-owned. The custom route answers the bare well-known path only.

    The body is asserted whole against the literal main_http() built. Every
    endpoint URL is MCP_PUBLIC_URL plus a fixed suffix, which is why the issuer
    carries the /mcp path segment.
    """
    response = await surface.client.get("/.well-known/oauth-authorization-server")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == EXPECTED_AS_METADATA


async def test_as_metadata_trailing_slash_redirects(surface: OAuthSurface):
    """Starlette-owned. A trailing slash gets a 307 to the bare path.

    Nothing follows redirects here, so the 307 is visible. A client that does
    not follow redirects sees no metadata at this URL.
    """
    response = await surface.client.get("/.well-known/oauth-authorization-server/")

    assert response.status_code == 307
    assert response.headers["location"] == (
        f"{BASE_URL}/.well-known/oauth-authorization-server"
    )


async def test_as_metadata_resource_suffixed_form_is_404(surface: OAuthSurface):
    """No route serves the RFC 8414 path-suffixed form in process.

    ``test_oauth_flow.py`` asserts that
    ``/.well-known/oauth-authorization-server/mcp`` returns 200 on staging, so
    something in front of the app rewrites it there. The app itself does not,
    and the port inherits that gap.
    """
    response = await surface.client.get("/.well-known/oauth-authorization-server/mcp")
    assert response.status_code == 404

    with_slash = await surface.client.get(
        "/.well-known/oauth-authorization-server/mcp/",
    )
    assert with_slash.status_code == 404


async def test_as_metadata_rejects_post(surface: OAuthSurface):
    """Repo-owned. The custom route registers GET alone."""
    response = await surface.client.post("/.well-known/oauth-authorization-server")
    assert response.status_code == 405


async def test_as_metadata_shadows_the_sdk_route(surface: OAuthSurface):
    """The SDK's own AS metadata is unreachable, and that is deliberate.

    Two routes are registered at this path. ``create_auth_routes`` adds the
    SDK's, then main_http() inserts the custom one at index 0, and Starlette
    matches in order. Both bodies are read here: the one the app serves, and the
    one the shadowed route would serve if the insert were dropped.

    The difference is ``client_secret_basic``. The SDK advertises it alongside
    ``client_secret_post`` on both endpoints. A client that reads the SDK body,
    registers for Basic and then sends both credentials in the Authorization
    header alone gets a 401; the Basic-auth tests below record how. Keeping the
    custom route in front is the behavior most at risk in the port.
    """
    registered = routes_at(surface.app, "/.well-known/oauth-authorization-server")
    assert len(registered) == 2
    assert surface.app.routes.index(registered[0]) == 0

    served = (
        await surface.client.get("/.well-known/oauth-authorization-server")
    ).json()
    assert served == EXPECTED_AS_METADATA
    assert served["token_endpoint_auth_methods_supported"] == ["client_secret_post"]
    assert served["revocation_endpoint_auth_methods_supported"] == [
        "client_secret_post",
    ]

    shadowed = (
        await serve_one_route(
            registered[1], "/.well-known/oauth-authorization-server",
        )
    ).json()
    assert shadowed != served
    assert shadowed["token_endpoint_auth_methods_supported"] == [
        "client_secret_post", "client_secret_basic",
    ]
    assert shadowed["revocation_endpoint_auth_methods_supported"] == [
        "client_secret_post", "client_secret_basic",
    ]


# ── Discovery: openid-configuration alias ───────────────────────────────────


async def test_openid_configuration_serves_the_same_body(surface: OAuthSurface):
    """Repo-owned. The OIDC discovery alias shares one handler with AS metadata.

    Only one route is registered at this path. The SDK never serves OIDC
    discovery, so the alias exists because main_http() adds it and for no other
    reason.
    """
    assert len(routes_at(surface.app, "/.well-known/openid-configuration")) == 1

    response = await surface.client.get("/.well-known/openid-configuration")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == EXPECTED_AS_METADATA


async def test_openid_configuration_trailing_slash_redirects(surface: OAuthSurface):
    response = await surface.client.get("/.well-known/openid-configuration/")

    assert response.status_code == 307
    assert response.headers["location"] == (
        f"{BASE_URL}/.well-known/openid-configuration"
    )


async def test_openid_configuration_resource_suffixed_form_is_404(
    surface: OAuthSurface,
):
    """The alias answers the bare path alone, with or without a trailing slash."""
    response = await surface.client.get("/.well-known/openid-configuration/mcp")
    assert response.status_code == 404

    with_slash = await surface.client.get("/.well-known/openid-configuration/mcp/")
    assert with_slash.status_code == 404


# ── Discovery: protected resource metadata ──────────────────────────────────


async def test_prm_exact_url_status_and_body(surface: OAuthSurface):
    """SDK-owned. The PRM path carries the resource path segment.

    ``build_resource_metadata_url`` splices ``/.well-known/oauth-protected-
    resource`` between the host and the ``/mcp`` path of the resource server
    URL, so the route is registered at the suffixed path and only there.

    ``scopes_supported`` is absent because ``AuthSettings.required_scopes`` is
    never set. The resource advertises no scopes at all.
    """
    response = await surface.client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "resource": "https://test.local/mcp",
        "authorization_servers": ["https://test.local/mcp"],
        "bearer_methods_supported": ["header"],
    }


async def test_prm_bare_path_is_404(surface: OAuthSurface):
    """The unsuffixed PRM path is not served, with or without a trailing slash."""
    bare = await surface.client.get("/.well-known/oauth-protected-resource")
    assert bare.status_code == 404

    with_slash = await surface.client.get("/.well-known/oauth-protected-resource/")
    assert with_slash.status_code == 404


async def test_prm_trailing_slash_redirects(surface: OAuthSurface):
    response = await surface.client.get("/.well-known/oauth-protected-resource/mcp/")

    assert response.status_code == 307
    assert response.headers["location"] == (
        f"{BASE_URL}/.well-known/oauth-protected-resource/mcp"
    )


# ── Registration: success shapes ────────────────────────────────────────────


async def test_register_client_secret_post_response_shape(surface: OAuthSurface):
    """SDK-owned. Registration returns 201 and the full client record.

    This checks the response alone. The round trip that spends these
    credentials is test_registered_credentials_drive_authorize_and_token.

    ``client_secret_expires_at`` is absent: ``ClientRegistrationOptions``
    leaves ``client_secret_expiry_seconds`` at None, so issued secrets never
    expire. The SDK's JSON renderer drops every None field, which is why the
    key is missing rather than null.
    """
    response = await surface.register(
        token_endpoint_auth_method="client_secret_post",
        client_name="characterization",
    )

    assert response.status_code == 201
    assert response.headers["content-type"] == "application/json"
    body = response.json()

    assert set(body) == {
        "client_id",
        "client_id_issued_at",
        "client_secret",
        "client_name",
        "grant_types",
        "redirect_uris",
        "response_types",
        "scope",
        "token_endpoint_auth_method",
    }
    assert body["token_endpoint_auth_method"] == "client_secret_post"
    assert body["redirect_uris"] == [REDIRECT_URI]
    assert body["grant_types"] == ["authorization_code", "refresh_token"]
    assert body["response_types"] == ["code"]
    assert body["scope"] == "tasks:read tasks:write plan:read plan:write profile:read"
    assert len(body["client_secret"]) == 64
    assert isinstance(body["client_id_issued_at"], int)


async def test_register_defaults_to_client_secret_post(surface: OAuthSurface):
    """SDK-owned. Omitting the auth method picks client_secret_post."""
    body = await surface.register_ok()

    assert body["token_endpoint_auth_method"] == "client_secret_post"
    assert body["client_secret"]


async def test_register_public_client_gets_no_secret(surface: OAuthSurface):
    """SDK-owned. ``none`` issues no secret, and the key is absent entirely."""
    body = await surface.register_ok(token_endpoint_auth_method="none")

    assert "client_secret" not in body
    assert body["token_endpoint_auth_method"] == "none"


async def test_register_accepts_client_secret_basic(surface: OAuthSurface):
    """SDK-owned, and wider than what the metadata advertises.

    The custom AS metadata lists ``client_secret_post`` alone, but registration
    still accepts ``client_secret_basic``. A client that reads the metadata will
    never ask for it; a client that asks anyway gets it.
    """
    body = await surface.register_ok(
        token_endpoint_auth_method="client_secret_basic",
    )

    assert body["token_endpoint_auth_method"] == "client_secret_basic"
    assert body["client_secret"]


# ── Registration: the credentials work downstream ───────────────────────────


async def test_registered_credentials_drive_authorize_and_token(
    surface: OAuthSurface,
):
    """End to end. The client_id from /register authenticates at both endpoints.

    /authorize accepts the client_id and its registered redirect URI, the OIDC
    callback mints a code against that same client, and /token accepts the
    issued secret.
    """
    creds = await surface.register_ok(client_name="round-trip")
    verifier, challenge = pkce_pair()

    query = {
        "response_type": "code",
        "client_id": creds["client_id"],
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "round-trip-state",
    }
    redirect = await surface.client.get(f"/authorize?{urlencode(query)}")
    assert redirect.status_code == 302
    assert redirect.headers["location"].startswith(
        "https://stub-issuer.test/authorize?state=",
    )

    nonce = parse_qs(urlparse(redirect.headers["location"]).query)["state"][0]
    callback = await surface.client.get(
        f"/oauth/oidc-callback?code=upstream-code&state={nonce}",
    )
    assert callback.status_code == 302

    returned = parse_qs(urlparse(callback.headers["location"]).query)
    assert returned["state"] == ["round-trip-state"]
    code = returned["code"][0]

    token = await surface.client.post("/token", data={
        "grant_type": "authorization_code",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    })
    assert token.status_code == 200
    assert token.json()["token_type"] == "Bearer"


async def test_public_client_completes_the_grant_without_a_secret(
    surface: OAuthSurface,
):
    """A client registered with ``none`` exchanges a code with no secret at all."""
    creds = await surface.register_ok(token_endpoint_auth_method="none")
    verifier, challenge = pkce_pair()
    code = await surface.mint_code(creds, challenge)

    response = await surface.client.post("/token", data={
        "grant_type": "authorization_code",
        "client_id": creds["client_id"],
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    })

    assert response.status_code == 200
    assert response.json()["token_type"] == "Bearer"


async def test_public_client_secret_is_ignored_not_rejected(surface: OAuthSurface):
    """SDK-owned, and surprising. A public client may send any secret it likes.

    ``ClientAuthenticator`` only compares secrets when the stored client has
    one. A client registered with ``none`` has none, so a bogus
    ``client_secret`` in the form is neither checked nor refused.
    """
    creds = await surface.register_ok(token_endpoint_auth_method="none")
    verifier, challenge = pkce_pair()
    code = await surface.mint_code(creds, challenge)

    response = await surface.client.post("/token", data={
        "grant_type": "authorization_code",
        "client_id": creds["client_id"],
        "client_secret": "not-a-real-secret",
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    })

    assert response.status_code == 200


# ── Registration: rejections ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("metadata", "error_description"),
    [
        pytest.param(
            {},
            "redirect_uris: Field required",
            id="missing-redirect-uris",
        ),
        pytest.param(
            {"redirect_uris": []},
            "redirect_uris: List should have at least 1 item after validation, not 0",
            id="empty-redirect-uris",
        ),
        pytest.param(
            {"redirect_uris": ["not a uri"]},
            "redirect_uris.0: Input should be a valid URL, relative URL without a base",
            id="malformed-redirect-uri",
        ),
        pytest.param(
            {
                "redirect_uris": [REDIRECT_URI],
                "grant_types": ["authorization_code"],
            },
            "grant_types must be authorization_code and refresh_token",
            id="grant-types-missing-refresh-token",
        ),
        pytest.param(
            {"redirect_uris": [REDIRECT_URI], "response_types": ["token"]},
            "response_types must include 'code' for authorization_code grant",
            id="response-types-without-code",
        ),
        pytest.param(
            {"redirect_uris": [REDIRECT_URI], "scope": "tasks:read not:a:scope"},
            "Requested scopes are not valid: not:a:scope",
            id="scope-outside-valid-scopes",
        ),
    ],
)
async def test_register_rejections(
    surface: OAuthSurface, metadata: dict, error_description: str,
):
    """SDK-owned. Every rejection is 400 with the same two-key error body."""
    response = await surface.client.post("/register", json=metadata)

    assert response.status_code == 400
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "error": "invalid_client_metadata",
        "error_description": error_description,
    }


async def test_register_form_encoded_body_raises(surface: OAuthSurface):
    """SDK-owned latent bug, pinned rather than fixed.

    ``RegistrationHandler`` calls ``request.json()`` and catches ``ValidationError``
    alone. A form-encoded or empty body raises ``JSONDecodeError`` out of the
    handler. Behind uvicorn that surfaces as a bare 500 instead of the RFC 7591
    ``invalid_client_metadata`` response. Over ASGITransport the exception
    propagates to the caller.
    """
    with pytest.raises(json.JSONDecodeError):
        await surface.client.post("/register", data={"redirect_uris": REDIRECT_URI})


# ── Token: authorization_code grant ─────────────────────────────────────────


async def test_authorization_code_grant_form_fields_and_success_body(
    surface: OAuthSurface,
):
    """The complete grant, with the request form and the response body pinned.

    The form below is the whole request: six fields, every one of them
    required. The server accepting it proves the set is sufficient. The test
    after this one drops each field in turn and records the refusal, which
    proves the set is minimal.

    ``client_secret`` travels in the body. That is what ``client_secret_post``
    means, and it is the only client authentication method the metadata
    advertises.

    The success body owes ``token_type`` and ``expires_in`` to the provider's
    ``exchange_authorization_code``. ``scope`` is absent here; the scope tests
    below explain why.
    """
    creds = await surface.register_ok()
    verifier, challenge = pkce_pair()
    code = await surface.mint_code(creds, challenge)

    form = {
        "grant_type": "authorization_code",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }
    response = await surface.client.post("/token", data=form)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"

    body = response.json()
    assert set(body) == {
        "access_token", "token_type", "expires_in", "refresh_token",
    }
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == EXPECTED_EXPIRES_IN
    assert len(body["access_token"]) == 64
    assert len(body["refresh_token"]) == 64
    assert body["access_token"] != body["refresh_token"]


@pytest.mark.parametrize(
    ("dropped", "status", "error", "error_description"),
    [
        pytest.param(
            "grant_type", 400, "invalid_request",
            ": Unable to extract tag using discriminator 'grant_type'",
            id="no-grant-type",
        ),
        pytest.param(
            "client_id", 401, "unauthorized_client",
            "Missing client_id",
            id="no-client-id",
        ),
        pytest.param(
            "client_secret", 401, "unauthorized_client",
            "Client secret is required",
            id="no-client-secret",
        ),
        pytest.param(
            "code", 400, "invalid_request",
            "authorization_code.code: Field required",
            id="no-code",
        ),
        pytest.param(
            "code_verifier", 400, "invalid_request",
            "authorization_code.code_verifier: Field required",
            id="no-code-verifier",
        ),
        pytest.param(
            "redirect_uri", 400, "invalid_request",
            "redirect_uri did not match the one used when creating auth code",
            id="no-redirect-uri",
        ),
    ],
)
async def test_authorization_code_grant_needs_every_form_field(
    surface: OAuthSurface,
    dropped: str,
    status: int,
    error: str,
    error_description: str,
):
    """Every one of the six fields is load-bearing, and each refusal differs.

    This is what pins the form the success test sends. Drop a field and the
    grant fails, with the status and the error body recorded here.

    Each case mints its own code, because a code survives only until the first
    request that reads it. The two that fail at 401 never reach the code:
    client authentication runs first, so the code is still spendable afterwards.

    Dropping ``grant_type`` produces a raw pydantic message with a leading
    colon, the same way an unrecognized grant type does.
    """
    creds = await surface.register_ok()
    verifier, challenge = pkce_pair()
    code = await surface.mint_code(creds, challenge)

    form = {
        "grant_type": "authorization_code",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }
    del form[dropped]

    response = await surface.client.post("/token", data=form)

    assert response.status_code == status
    assert response.json() == {
        "error": error,
        "error_description": error_description,
    }


async def test_authorization_code_is_single_use(surface: OAuthSurface):
    """Provider-owned. ``load_auth_code`` deletes the key as it reads it.

    A replay therefore looks identical to an unknown code, which is what the
    RFC asks for.
    """
    creds = await surface.register_ok()
    verifier, challenge = pkce_pair()
    code = await surface.mint_code(creds, challenge)
    form = {
        "grant_type": "authorization_code",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }

    first = await surface.client.post("/token", data=form)
    assert first.status_code == 200

    replay = await surface.client.post("/token", data=form)
    assert replay.status_code == 400
    assert replay.json() == {
        "error": "invalid_grant",
        "error_description": "authorization code does not exist",
    }


async def test_issued_access_token_works_against_the_resource(
    surface: OAuthSurface,
):
    """End to end. A freshly issued token reaches the MCP endpoint."""
    creds = await surface.register_ok()
    tokens = await surface.mint_tokens(creds)

    response = await surface.ping_mcp(tokens["access_token"])

    assert response.status_code == 200
    assert '"serverInfo"' in response.text


async def test_unknown_bearer_token_is_rejected_with_prm_pointer(
    surface: OAuthSurface,
):
    """SDK-owned. The 401 carries the RFC 9728 metadata URL for discovery."""
    response = await surface.ping_mcp("not-a-real-token")

    assert response.status_code == 401
    assert response.json() == {
        "error": "invalid_token",
        "error_description": "Authentication required",
    }
    assert response.headers["www-authenticate"] == (
        'Bearer error="invalid_token", '
        'error_description="Authentication required", '
        'resource_metadata="https://test.local/.well-known/oauth-protected-resource/mcp"'
    )


# ── Token: refresh_token grant ──────────────────────────────────────────────


async def test_refresh_grant_form_fields_and_rotation(surface: OAuthSurface):
    """Provider-owned rotation, asserted through the HTTP surface.

    The refresh request is four fields. It drops the three the authorization
    code grant needs -- code, verifier and redirect URI -- and adds the refresh
    token. The test after this one drops each of the four in turn.

    ``exchange_refresh_token`` deletes the presented refresh token and the
    access token paired with it, then issues a new pair. All three consequences
    are checked: the old access token stops working, the new one works, and the
    old refresh token cannot be presented again.
    """
    creds = await surface.register_ok()
    first = await surface.mint_tokens(creds)

    form = {
        "grant_type": "refresh_token",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": first["refresh_token"],
    }
    response = await surface.client.post("/token", data=form)

    assert response.status_code == 200
    second = response.json()
    assert set(second) == {
        "access_token", "token_type", "expires_in", "refresh_token",
    }
    assert second["token_type"] == "Bearer"
    assert second["expires_in"] == EXPECTED_EXPIRES_IN
    assert second["access_token"] != first["access_token"]
    assert second["refresh_token"] != first["refresh_token"]

    assert (await surface.ping_mcp(first["access_token"])).status_code == 401
    assert (await surface.ping_mcp(second["access_token"])).status_code == 200

    replay = await surface.client.post("/token", data=form)
    assert replay.status_code == 400
    assert replay.json() == {
        "error": "invalid_grant",
        "error_description": "refresh token does not exist",
    }


@pytest.mark.parametrize(
    ("dropped", "status", "error", "error_description"),
    [
        pytest.param(
            "grant_type", 400, "invalid_request",
            ": Unable to extract tag using discriminator 'grant_type'",
            id="no-grant-type",
        ),
        pytest.param(
            "client_id", 401, "unauthorized_client",
            "Missing client_id",
            id="no-client-id",
        ),
        pytest.param(
            "client_secret", 401, "unauthorized_client",
            "Client secret is required",
            id="no-client-secret",
        ),
        pytest.param(
            "refresh_token", 400, "invalid_request",
            "refresh_token.refresh_token: Field required",
            id="no-refresh-token",
        ),
    ],
)
async def test_refresh_grant_needs_every_form_field(
    surface: OAuthSurface,
    dropped: str,
    status: int,
    error: str,
    error_description: str,
):
    """All four fields are load-bearing, and each refusal differs.

    The two client authentication failures match the authorization_code grant
    exactly, because ``ClientAuthenticator`` runs before either grant is parsed.
    """
    creds = await surface.register_ok()
    tokens = await surface.mint_tokens(creds)

    form = {
        "grant_type": "refresh_token",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": tokens["refresh_token"],
    }
    del form[dropped]

    response = await surface.client.post("/token", data=form)

    assert response.status_code == status
    assert response.json() == {
        "error": error,
        "error_description": error_description,
    }


async def test_refresh_grant_can_narrow_scope_but_not_widen_it(
    surface: OAuthSurface,
):
    """SDK-owned. The requested scope must be a subset of the refresh token's."""
    creds = await surface.register_ok()
    tokens = await surface.mint_tokens(creds, scope="tasks:read tasks:write")
    assert tokens["scope"] == "tasks:read tasks:write"

    narrowed = await surface.client.post("/token", data={
        "grant_type": "refresh_token",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": tokens["refresh_token"],
        "scope": "tasks:read",
    })
    assert narrowed.status_code == 200
    assert narrowed.json()["scope"] == "tasks:read"

    widened = await surface.client.post("/token", data={
        "grant_type": "refresh_token",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": narrowed.json()["refresh_token"],
        "scope": "plan:read",
    })
    assert widened.status_code == 400
    assert widened.json() == {
        "error": "invalid_scope",
        "error_description": "cannot request scope `plan:read` not provided by refresh token",
    }


# ── Token: scope handling ───────────────────────────────────────────────────


async def test_authorize_without_scope_grants_no_scopes(surface: OAuthSurface):
    """Pinned as a latent bug: an omitted scope grants nothing, not the default.

    ``validate_scope(None)`` returns None, the provider stores ``scopes or []``,
    and the token comes back with an empty scope list. The ``scope`` key is
    absent from the response because the SDK drops None fields.

    The client registered with five default scopes and receives a token good for
    none of them. Nothing enforces scopes today, so this is invisible until
    something starts checking.
    """
    creds = await surface.register_ok()
    tokens = await surface.mint_tokens(creds)

    assert "scope" not in tokens

    stored = await surface.provider.load_access_token(tokens["access_token"])
    assert stored is not None
    assert stored.scopes == []


async def test_authorize_with_scope_echoes_it_in_the_token(surface: OAuthSurface):
    """The requested scope reaches the token when the client asks for it."""
    creds = await surface.register_ok()
    tokens = await surface.mint_tokens(creds, scope="tasks:read plan:read")

    assert tokens["scope"] == "tasks:read plan:read"

    stored = await surface.provider.load_access_token(tokens["access_token"])
    assert stored.scopes == ["tasks:read", "plan:read"]


# ── Token: client authentication failures ───────────────────────────────────


@pytest.mark.parametrize(
    ("form", "error_description"),
    [
        pytest.param(
            {"grant_type": "authorization_code"},
            "Missing client_id",
            id="missing-client-id",
        ),
        pytest.param(
            {},
            "Missing client_id",
            id="empty-form",
        ),
        pytest.param(
            {
                "grant_type": "authorization_code",
                "client_id": "does-not-exist",
                "code": "irrelevant",
            },
            "Invalid client_id",
            id="unknown-client-id",
        ),
    ],
)
async def test_token_client_authentication_failures_without_registration(
    surface: OAuthSurface, form: dict, error_description: str,
):
    """SDK-owned. Client authentication failures are 401 ``unauthorized_client``.

    ``ClientAuthenticator`` runs before the grant is parsed, so these never
    reach any grant validation.
    """
    response = await surface.client.post("/token", data=form)

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "error": "unauthorized_client",
        "error_description": error_description,
    }


async def test_token_wrong_client_secret_is_401(surface: OAuthSurface):
    """SDK-owned. A wrong secret fails authentication, not grant validation."""
    creds = await surface.register_ok()

    response = await surface.client.post("/token", data={
        "grant_type": "authorization_code",
        "client_id": creds["client_id"],
        "client_secret": "wrong-secret",
        "code": "irrelevant",
        "code_verifier": "irrelevant",
        "redirect_uri": REDIRECT_URI,
    })

    assert response.status_code == 401
    assert response.json() == {
        "error": "unauthorized_client",
        "error_description": "Invalid client_secret",
    }


async def test_token_rejects_json_body(surface: OAuthSurface):
    """SDK-owned. The token endpoint reads a form and nothing else.

    A JSON body parses to an empty form, so the request fails at client
    authentication with the same error a request with no credentials gets.
    """
    creds = await surface.register_ok()

    response = await surface.client.post("/token", json={
        "grant_type": "authorization_code",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "code": "irrelevant",
        "code_verifier": "irrelevant",
    })

    assert response.status_code == 401
    assert response.json() == {
        "error": "unauthorized_client",
        "error_description": "Missing client_id",
    }


# ── Token: HTTP Basic credentials ───────────────────────────────────────────


async def test_basic_auth_alone_fails_for_a_client_secret_post_client(
    surface: OAuthSurface,
):
    """The reason the metadata omits ``client_secret_basic``, pinned.

    ``ClientAuthenticator`` reads ``client_id`` from the form body before it
    looks at the Authorization header. A client that follows RFC 6749 and puts
    both credentials in the header alone never gets past that read.
    """
    creds = await surface.register_ok(
        token_endpoint_auth_method="client_secret_post",
    )
    header = base64.b64encode(
        f"{creds['client_id']}:{creds['client_secret']}".encode(),
    ).decode()

    response = await surface.client.post(
        "/token",
        data={"grant_type": "authorization_code", "code": "irrelevant",
              "code_verifier": "irrelevant", "redirect_uri": REDIRECT_URI},
        headers={"Authorization": f"Basic {header}"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "error": "unauthorized_client",
        "error_description": "Missing client_id",
    }


async def test_basic_auth_secret_is_not_read_for_a_client_secret_post_client(
    surface: OAuthSurface,
):
    """Adding client_id to the form is still not enough.

    The stored client's method is ``client_secret_post``, so the authenticator
    looks for the secret in the form body only. The Basic header is ignored and
    the request fails for a missing secret.
    """
    creds = await surface.register_ok(
        token_endpoint_auth_method="client_secret_post",
    )
    header = base64.b64encode(
        f"{creds['client_id']}:{creds['client_secret']}".encode(),
    ).decode()

    response = await surface.client.post(
        "/token",
        data={"grant_type": "authorization_code", "client_id": creds["client_id"],
              "code": "irrelevant", "code_verifier": "irrelevant",
              "redirect_uri": REDIRECT_URI},
        headers={"Authorization": f"Basic {header}"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "error": "unauthorized_client",
        "error_description": "Client secret is required",
    }


async def test_basic_auth_works_only_with_client_id_duplicated_in_the_form(
    surface: OAuthSurface,
):
    """A ``client_secret_basic`` client needs client_id in both places.

    With the header alone the request fails. With client_id also in the form the
    grant succeeds. That duplication is not what RFC 6749 asks for, and it is
    why the metadata advertises ``client_secret_post`` instead.
    """
    creds = await surface.register_ok(
        token_endpoint_auth_method="client_secret_basic",
    )
    header = base64.b64encode(
        f"{creds['client_id']}:{creds['client_secret']}".encode(),
    ).decode()

    header_only = await surface.client.post(
        "/token",
        data={"grant_type": "authorization_code", "code": "irrelevant",
              "code_verifier": "irrelevant", "redirect_uri": REDIRECT_URI},
        headers={"Authorization": f"Basic {header}"},
    )
    assert header_only.status_code == 401
    assert header_only.json()["error_description"] == "Missing client_id"

    verifier, challenge = pkce_pair()
    code = await surface.mint_code(creds, challenge)
    accepted = await surface.client.post(
        "/token",
        data={"grant_type": "authorization_code", "client_id": creds["client_id"],
              "code": code, "code_verifier": verifier,
              "redirect_uri": REDIRECT_URI},
        headers={"Authorization": f"Basic {header}"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["token_type"] == "Bearer"


async def test_client_secret_basic_client_rejects_the_form_secret(
    surface: OAuthSurface,
):
    """The two methods do not interoperate in either direction."""
    creds = await surface.register_ok(
        token_endpoint_auth_method="client_secret_basic",
    )

    response = await surface.client.post("/token", data={
        "grant_type": "authorization_code",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "code": "irrelevant",
        "code_verifier": "irrelevant",
        "redirect_uri": REDIRECT_URI,
    })

    assert response.status_code == 401
    assert response.json() == {
        "error": "unauthorized_client",
        "error_description": (
            "Missing or invalid Basic authentication in Authorization header"
        ),
    }


# ── Token: grant validation failures ────────────────────────────────────────


async def test_token_unknown_authorization_code(surface: OAuthSurface):
    """SDK-owned. An unknown code is 400 ``invalid_grant``."""
    creds = await surface.register_ok()

    response = await surface.client.post("/token", data={
        "grant_type": "authorization_code",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "code": "no-such-code",
        "code_verifier": "irrelevant",
        "redirect_uri": REDIRECT_URI,
    })

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_grant",
        "error_description": "authorization code does not exist",
    }


async def test_token_wrong_code_verifier(surface: OAuthSurface):
    """SDK-owned. PKCE is verified for real; a verifier from another pair fails."""
    creds = await surface.register_ok()
    _, challenge = pkce_pair()
    code = await surface.mint_code(creds, challenge)
    wrong_verifier, _ = pkce_pair()

    response = await surface.client.post("/token", data={
        "grant_type": "authorization_code",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "code": code,
        "code_verifier": wrong_verifier,
        "redirect_uri": REDIRECT_URI,
    })

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_grant",
        "error_description": "incorrect code_verifier",
    }


async def test_token_mismatched_redirect_uri(surface: OAuthSurface):
    """SDK-owned. The redirect URI must match the one used at /authorize."""
    creds = await surface.register_ok()
    verifier, challenge = pkce_pair()
    code = await surface.mint_code(creds, challenge)

    response = await surface.client.post("/token", data={
        "grant_type": "authorization_code",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": "http://localhost:8765/somewhere-else",
    })

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_request",
        "error_description": (
            "redirect_uri did not match the one used when creating auth code"
        ),
    }


async def test_token_omitted_redirect_uri_is_also_a_mismatch(
    surface: OAuthSurface,
):
    """The code recorded an explicit redirect URI, so omitting it fails too."""
    creds = await surface.register_ok()
    verifier, challenge = pkce_pair()
    code = await surface.mint_code(creds, challenge)

    response = await surface.client.post("/token", data={
        "grant_type": "authorization_code",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "code": code,
        "code_verifier": verifier,
    })

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_request",
        "error_description": (
            "redirect_uri did not match the one used when creating auth code"
        ),
    }


@pytest.mark.parametrize(
    ("extra_form", "error", "error_description"),
    [
        pytest.param(
            {"grant_type": "authorization_code", "code": "x",
             "redirect_uri": REDIRECT_URI},
            "invalid_request",
            "authorization_code.code_verifier: Field required",
            id="missing-code-verifier",
        ),
        pytest.param(
            {"grant_type": "refresh_token"},
            "invalid_request",
            "refresh_token.refresh_token: Field required",
            id="missing-refresh-token",
        ),
        pytest.param(
            {"grant_type": "refresh_token", "refresh_token": "no-such-token"},
            "invalid_grant",
            "refresh token does not exist",
            id="unknown-refresh-token",
        ),
        pytest.param(
            {"grant_type": "password", "username": "a", "password": "b"},
            "invalid_request",
            ": Input tag 'password' found using 'grant_type' does not match any of "
            "the expected tags: 'authorization_code', 'refresh_token'",
            id="unsupported-grant-type",
        ),
    ],
)
async def test_token_grant_validation_failures(
    surface: OAuthSurface, extra_form: dict, error: str, error_description: str,
):
    """SDK-owned. Grant-level failures are 400 with an ``error`` and a description.

    The unsupported grant type is reported as ``invalid_request`` rather than
    ``unsupported_grant_type``. Pydantic's discriminated union rejects the value
    before the handler's own grant check runs, so that branch is unreachable and
    the description is a raw pydantic message, leading colon and all.
    """
    creds = await surface.register_ok()
    form = {
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    }
    form.update(extra_form)

    response = await surface.client.post("/token", data=form)

    assert response.status_code == 400
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "error": error,
        "error_description": error_description,
    }


async def test_token_rejects_get(surface: OAuthSurface):
    """SDK-owned. The route registers POST and OPTIONS."""
    assert (await surface.client.get("/token")).status_code == 405


# ── Authorize: failure paths ────────────────────────────────────────────────


async def test_authorize_unknown_client_returns_json_not_a_redirect(
    surface: OAuthSurface,
):
    """SDK-owned. With no known client there is nowhere safe to redirect."""
    _, challenge = pkce_pair()
    query = {
        "response_type": "code",
        "client_id": "no-such-client",
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "client-state",
    }

    response = await surface.client.get(f"/authorize?{urlencode(query)}")

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_request",
        "error_description": "Client ID 'no-such-client' not found",
        "state": "client-state",
    }


async def test_authorize_unregistered_redirect_uri_returns_json(
    surface: OAuthSurface,
):
    """SDK-owned. An unregistered redirect URI is never redirected to."""
    creds = await surface.register_ok()
    _, challenge = pkce_pair()
    query = {
        "response_type": "code",
        "client_id": creds["client_id"],
        "redirect_uri": "http://attacker.test/callback",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "client-state",
    }

    response = await surface.client.get(f"/authorize?{urlencode(query)}")

    assert response.status_code == 400
    assert response.json() == {
        "error": "invalid_request",
        "error_description": (
            "Redirect URI 'http://attacker.test/callback' not registered for client"
        ),
        "state": "client-state",
    }


@pytest.mark.parametrize(
    ("overrides", "error", "error_description"),
    [
        pytest.param(
            {"scope": "not:a:scope"},
            "invalid_scope",
            "Client was not registered with scope not:a:scope",
            id="unregistered-scope",
        ),
        pytest.param(
            {"code_challenge": None},
            "invalid_request",
            "code_challenge: Field required",
            id="missing-pkce-challenge",
        ),
        pytest.param(
            {"code_challenge_method": "plain"},
            "invalid_request",
            "code_challenge_method: Input should be 'S256'",
            id="plain-pkce-method",
        ),
        pytest.param(
            {"response_type": "token"},
            "unsupported_response_type",
            "response_type: Input should be 'code'",
            id="implicit-response-type",
        ),
    ],
)
async def test_authorize_errors_redirect_back_to_the_client(
    surface: OAuthSurface, overrides: dict, error: str, error_description: str,
):
    """SDK-owned. Once the client and redirect URI check out, errors redirect.

    The error, the description and the client's state ride back as query
    parameters on the registered redirect URI.
    """
    creds = await surface.register_ok()
    _, challenge = pkce_pair()
    query = {
        "response_type": "code",
        "client_id": creds["client_id"],
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "client-state",
    }
    for key, value in overrides.items():
        if value is None:
            query.pop(key)
        else:
            query[key] = value

    response = await surface.client.get(f"/authorize?{urlencode(query)}")

    assert response.status_code == 302
    assert response.headers["cache-control"] == "no-store"
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == REDIRECT_URI
    assert parse_qs(location.query) == {
        "error": [error],
        "error_description": [error_description],
        "state": ["client-state"],
    }


# ── Revocation ──────────────────────────────────────────────────────────────


async def test_revoke_access_token_returns_empty_200_and_kills_the_token(
    surface: OAuthSurface,
):
    """The success response is a bodyless 200 with no content type.

    The SDK returns a bare ``Response``, so there is no JSON and no
    Content-Type header, only the two cache headers.
    """
    creds = await surface.register_ok()
    tokens = await surface.mint_tokens(creds)
    assert (await surface.ping_mcp(tokens["access_token"])).status_code == 200

    response = await surface.client.post("/revoke", data={
        "token": tokens["access_token"],
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    })

    assert response.status_code == 200
    assert response.content == b""
    assert "content-type" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"

    assert (await surface.ping_mcp(tokens["access_token"])).status_code == 401
    assert await surface.provider.load_access_token(tokens["access_token"]) is None


async def test_revoking_an_access_token_leaves_the_refresh_token_alive(
    surface: OAuthSurface,
):
    """Provider-owned, and asymmetric on purpose.

    ``revoke_token`` deletes only the access token when given one. The paired
    refresh token survives and can still mint a new access token.
    """
    creds = await surface.register_ok()
    tokens = await surface.mint_tokens(creds)

    await surface.client.post("/revoke", data={
        "token": tokens["access_token"],
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    })

    refreshed = await surface.client.post("/token", data={
        "grant_type": "refresh_token",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": tokens["refresh_token"],
    })
    assert refreshed.status_code == 200
    assert (await surface.ping_mcp(refreshed.json()["access_token"])).status_code == 200


async def test_revoking_a_refresh_token_also_kills_its_access_token(
    surface: OAuthSurface,
):
    """Provider-owned. Revoking the refresh token takes the pair down together.

    ``revoke_token`` reads the ``access_token`` field the provider stored beside
    the refresh token and deletes it as well.
    """
    creds = await surface.register_ok()
    tokens = await surface.mint_tokens(creds)

    response = await surface.client.post("/revoke", data={
        "token": tokens["refresh_token"],
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    })

    assert response.status_code == 200
    assert response.content == b""
    assert (await surface.ping_mcp(tokens["access_token"])).status_code == 401

    reuse = await surface.client.post("/token", data={
        "grant_type": "refresh_token",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": tokens["refresh_token"],
    })
    assert reuse.status_code == 400
    assert reuse.json() == {
        "error": "invalid_grant",
        "error_description": "refresh token does not exist",
    }


async def test_revoke_unknown_token_returns_200(surface: OAuthSurface):
    """RFC 7009 section 2.2: an invalid token still gets a 200."""
    creds = await surface.register_ok()

    response = await surface.client.post("/revoke", data={
        "token": "totally-not-a-real-token",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    })

    assert response.status_code == 200
    assert response.content == b""


async def test_revoke_another_clients_token_is_a_silent_no_op(
    surface: OAuthSurface,
):
    """SDK-owned. Cross-client revocation returns 200 and changes nothing.

    The handler compares the token's client to the authenticated client and
    skips the provider call on a mismatch. The caller cannot tell the difference
    between this and an unknown token, which is the point.
    """
    victim = await surface.register_ok(client_name="victim")
    attacker = await surface.register_ok(client_name="attacker")
    tokens = await surface.mint_tokens(victim)

    response = await surface.client.post("/revoke", data={
        "token": tokens["access_token"],
        "client_id": attacker["client_id"],
        "client_secret": attacker["client_secret"],
    })

    assert response.status_code == 200
    assert (await surface.ping_mcp(tokens["access_token"])).status_code == 200


@pytest.mark.parametrize(
    ("form", "status", "error", "error_description"),
    [
        pytest.param(
            {"client_id": "@registered", "client_secret": "@secret"},
            400,
            "invalid_request",
            "token: Field required",
            id="missing-token",
        ),
        pytest.param(
            {"token": "x", "client_id": "@registered", "client_secret": "@secret",
             "token_type_hint": "bogus"},
            400,
            "invalid_request",
            "token_type_hint: Input should be 'access_token' or 'refresh_token'",
            id="bad-token-type-hint",
        ),
        pytest.param(
            {"token": "x"},
            401,
            "unauthorized_client",
            "Missing client_id",
            id="missing-client-id",
        ),
        pytest.param(
            {"token": "x", "client_id": "@registered", "client_secret": "wrong"},
            401,
            "unauthorized_client",
            "Invalid client_secret",
            id="wrong-client-secret",
        ),
        pytest.param(
            {"token": "x", "client_id": "no-such-client", "client_secret": "@secret"},
            401,
            "unauthorized_client",
            "Invalid client_id",
            id="unknown-client-id",
        ),
    ],
)
async def test_revoke_failures(
    surface: OAuthSurface,
    form: dict,
    status: int,
    error: str,
    error_description: str,
):
    """SDK-owned. Revocation failures return JSON with an ``error`` pair.

    Unlike the success path these carry a Content-Type and no cache headers.
    Client authentication failures are 401 and run before the token parameter is
    validated, so a request missing both fails on the credentials.
    """
    creds = await surface.register_ok()
    substitutions = {
        "@registered": creds["client_id"],
        "@secret": creds["client_secret"],
    }
    resolved = {k: substitutions.get(v, v) for k, v in form.items()}

    response = await surface.client.post("/revoke", data=resolved)

    assert response.status_code == status
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "error": error,
        "error_description": error_description,
    }


# ── CORS preflight ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/token", "/register", "/revoke"])
async def test_cors_preflight_is_allowed_on_the_client_facing_endpoints(
    surface: OAuthSurface, path: str,
):
    """SDK-owned. Browser-based clients need these three to answer OPTIONS."""
    response = await surface.client.options(path, headers={
        "Origin": "https://inspector.test",
        "Access-Control-Request-Method": "POST",
    })

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


async def test_authorize_has_no_cors_preflight(surface: OAuthSurface):
    """SDK-owned. /authorize is a browser redirect target, not a fetch target."""
    response = await surface.client.options("/authorize", headers={
        "Origin": "https://inspector.test",
        "Access-Control-Request-Method": "GET",
    })

    assert response.status_code == 405
