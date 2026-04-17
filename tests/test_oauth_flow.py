"""Integration tests for the MCP OAuth flow against the live staging server.

These tests exercise the exact same endpoints Claude Code hits, in the same
order, to verify the OAuth registration → authorize → token exchange pipeline.

Run against staging:
    pytest tests/test_oauth_flow.py -v

Requires: the MCP server at MCP_BASE_URL to be running.
"""

from __future__ import annotations

import os
import base64
from urllib.parse import urlencode, urlparse, parse_qs

import httpx
import pytest

MCP_BASE_URL = os.environ.get("MCP_BASE_URL", "https://defernowork.com")


@pytest.fixture
def client():
    with httpx.Client(base_url=MCP_BASE_URL, timeout=15, follow_redirects=False) as c:
        yield c


# ── Discovery ────────────────────────────────────────────────────────────


class TestDiscovery:
    """Verify well-known metadata endpoints return valid JSON."""

    def test_protected_resource_metadata(self, client: httpx.Client):
        """PRM endpoint returns JSON with authorization_servers."""
        resp = client.get("/.well-known/oauth-protected-resource/mcp")
        assert resp.status_code == 200
        data = resp.json()
        assert "resource" in data
        assert "authorization_servers" in data
        assert isinstance(data["authorization_servers"], list)
        assert len(data["authorization_servers"]) > 0

    def test_oauth_authorization_server_metadata(self, client: httpx.Client):
        """RFC 8414 AS metadata returns valid JSON (not SPA HTML)."""
        resp = client.get("/.well-known/oauth-authorization-server/mcp")
        assert resp.status_code == 200
        assert "application/json" in resp.headers.get("content-type", "")
        data = resp.json()
        assert data["issuer"] == f"{MCP_BASE_URL}/mcp"
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "registration_endpoint" in data
        assert "code_challenge_methods_supported" in data
        assert "S256" in data["code_challenge_methods_supported"]

    def test_metadata_does_not_advertise_client_secret_basic(self, client: httpx.Client):
        """Metadata must NOT advertise client_secret_basic.

        The upstream FastMCP ClientAuthenticator has a bug: it requires
        client_id in the form body even for client_secret_basic, but the
        TypeScript MCP SDK (Claude Code) only sends it in the Authorization
        header per RFC 6749.  We work around this by only advertising
        client_secret_post.
        """
        resp = client.get("/.well-known/oauth-authorization-server/mcp")
        data = resp.json()
        auth_methods = data.get("token_endpoint_auth_methods_supported", [])
        assert "client_secret_basic" not in auth_methods, (
            "client_secret_basic must not be advertised — FastMCP cannot handle it"
        )
        assert "client_secret_post" in auth_methods

    def test_prm_points_to_as_metadata(self, client: httpx.Client):
        """PRM authorization_servers URL resolves to valid AS metadata."""
        prm = client.get("/.well-known/oauth-protected-resource/mcp").json()
        as_url = prm["authorization_servers"][0]

        # Derive the RFC 8414 discovery URL from the AS URL
        parsed = urlparse(as_url)
        discovery_path = f"/.well-known/oauth-authorization-server{parsed.path}"
        resp = client.get(discovery_path)
        assert resp.status_code == 200
        data = resp.json()
        assert data["issuer"] == as_url


# ── Client Registration ─────────────────────────────────────────────────


class TestRegistration:
    """Test dynamic client registration (RFC 7591)."""

    def _register(self, client: httpx.Client, auth_method: str = "client_secret_post") -> dict:
        """Register a test client and return the response data."""
        resp = client.post(
            "/mcp/register",
            json={
                "redirect_uris": ["http://localhost:9999/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": auth_method,
                "client_name": "oauth-flow-test",
            },
        )
        assert resp.status_code == 201, f"Registration failed: {resp.text}"
        return resp.json()

    def test_register_client_secret_post(self, client: httpx.Client):
        """Register with client_secret_post: must return client_id and client_secret."""
        data = self._register(client, "client_secret_post")
        assert "client_id" in data
        assert "client_secret" in data
        assert data["client_secret"]  # must be non-empty
        assert data.get("token_endpoint_auth_method") == "client_secret_post"

    def test_register_none(self, client: httpx.Client):
        """Register with token_endpoint_auth_method=none (public client).

        Public clients must NOT receive a client_secret.  If they do, the
        ClientAuthenticator will demand a secret on /token even though the
        client was never given one to send.
        """
        data = self._register(client, "none")
        assert "client_id" in data
        # A public client must either have no secret or an empty/null secret
        secret = data.get("client_secret")
        assert not secret, (
            f"Public client (auth_method=none) received a client_secret: {secret!r}. "
            "This will cause the token endpoint to demand a secret the client "
            "doesn't have — breaking the OAuth flow for Claude Code."
        )

    def test_register_default_auth_method(self, client: httpx.Client):
        """Register without specifying auth method: server picks a default."""
        resp = client.post(
            "/mcp/register",
            json={
                "redirect_uris": ["http://localhost:9999/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_name": "oauth-flow-test-default",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        # Default should be client_secret_post
        assert data.get("token_endpoint_auth_method") == "client_secret_post"


# ── Token Endpoint Auth ──────────────────────────────────────────────────


class TestTokenEndpointAuth:
    """Test that the /token endpoint correctly handles client authentication.

    These tests don't complete a full OAuth flow — they verify the auth layer
    accepts/rejects credentials correctly before the grant_type logic runs.
    """

    def _register(self, client: httpx.Client, auth_method: str) -> dict:
        resp = client.post(
            "/mcp/register",
            json={
                "redirect_uris": ["http://localhost:9999/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": auth_method,
                "client_name": "token-auth-test",
            },
        )
        assert resp.status_code == 201
        return resp.json()

    def test_token_client_secret_post_passes_auth(self, client: httpx.Client):
        """client_secret_post: client_id + client_secret in form body passes auth.

        Expected: auth passes, fails on grant-level validation (not 401).
        """
        reg = self._register(client, "client_secret_post")
        resp = client.post(
            "/mcp/token",
            data={
                "grant_type": "authorization_code",
                "code": "fake-code",
                "client_id": reg["client_id"],
                "client_secret": reg["client_secret"],
                "code_verifier": "fake-verifier",
                "redirect_uri": "http://localhost:9999/callback",
            },
        )
        # Should NOT be 401 (auth failure).  We expect 400 (invalid code).
        assert resp.status_code != 401, (
            f"client_secret_post auth failed with 401: {resp.text}"
        )

    def test_token_client_secret_post_wrong_secret(self, client: httpx.Client):
        """client_secret_post with wrong secret should 401."""
        reg = self._register(client, "client_secret_post")
        resp = client.post(
            "/mcp/token",
            data={
                "grant_type": "authorization_code",
                "code": "fake-code",
                "client_id": reg["client_id"],
                "client_secret": "wrong-secret",
                "code_verifier": "fake-verifier",
            },
        )
        assert resp.status_code == 401

    def test_token_client_secret_post_missing_secret(self, client: httpx.Client):
        """client_secret_post without secret should 401."""
        reg = self._register(client, "client_secret_post")
        resp = client.post(
            "/mcp/token",
            data={
                "grant_type": "authorization_code",
                "code": "fake-code",
                "client_id": reg["client_id"],
                "code_verifier": "fake-verifier",
            },
        )
        assert resp.status_code == 401

    def test_token_none_auth_passes_without_secret(self, client: httpx.Client):
        """token_endpoint_auth_method=none: no secret needed, auth should pass.

        This is the critical path for Claude Code — it registers as a public
        client and sends only client_id in the token request.

        Expected: auth passes, fails on grant-level validation (not 401).
        """
        reg = self._register(client, "none")
        resp = client.post(
            "/mcp/token",
            data={
                "grant_type": "authorization_code",
                "code": "fake-code",
                "client_id": reg["client_id"],
                "code_verifier": "fake-verifier",
                "redirect_uri": "http://localhost:9999/callback",
            },
        )
        assert resp.status_code != 401, (
            f"Public client (auth_method=none) got 401 on /token: {resp.text}. "
            "This means the server is demanding a client_secret from a public "
            "client that was never given one."
        )

    def test_token_missing_client_id(self, client: httpx.Client):
        """Token request without client_id should 401."""
        resp = client.post(
            "/mcp/token",
            data={
                "grant_type": "authorization_code",
                "code": "fake-code",
                "code_verifier": "fake-verifier",
            },
        )
        assert resp.status_code == 401
        assert "client_id" in resp.json().get("error_description", "").lower()

    def test_token_unknown_client_id(self, client: httpx.Client):
        """Token request with unknown client_id should 401."""
        resp = client.post(
            "/mcp/token",
            data={
                "grant_type": "authorization_code",
                "code": "fake-code",
                "client_id": "nonexistent-client-id",
                "code_verifier": "fake-verifier",
            },
        )
        assert resp.status_code == 401


# ── Full Claude Code Flow Simulation ─────────────────────────────────────


class TestClaudeCodeFlow:
    """Simulate the exact sequence of requests Claude Code makes.

    This tests everything up to the browser redirect (which we can't automate
    without a real Zitadel session).
    """

    def test_full_discovery_and_registration_flow(self, client: httpx.Client):
        """Simulate Claude Code's startup: PRM → AS metadata → register.

        Steps:
        1. POST /mcp → 401 (unauthenticated, triggers OAuth)
        2. GET /.well-known/oauth-protected-resource/mcp → PRM
        3. GET /.well-known/oauth-authorization-server/mcp → AS metadata
        4. POST /mcp/register → client registration
        5. Verify all data is consistent and usable
        """
        # Step 1: Initial MCP request triggers 401
        resp = client.post("/mcp")
        assert resp.status_code == 401, (
            f"Expected 401 on unauthenticated /mcp, got {resp.status_code}"
        )

        # Step 2: Discover protected resource metadata
        prm_resp = client.get("/.well-known/oauth-protected-resource/mcp")
        assert prm_resp.status_code == 200
        prm = prm_resp.json()
        auth_server_url = prm["authorization_servers"][0]

        # Step 3: Discover AS metadata (RFC 8414 path-aware)
        parsed = urlparse(auth_server_url)
        as_discovery = f"/.well-known/oauth-authorization-server{parsed.path}"
        as_resp = client.get(as_discovery)
        assert as_resp.status_code == 200
        as_meta = as_resp.json()

        # Step 4: Register client
        reg_endpoint = urlparse(as_meta["registration_endpoint"]).path
        reg_resp = client.post(
            reg_endpoint,
            json={
                "redirect_uris": ["http://localhost:9999/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_name": "claude-code-flow-test",
            },
        )
        assert reg_resp.status_code == 201
        reg = reg_resp.json()

        # Step 5: Verify consistency
        token_method = reg.get("token_endpoint_auth_method", "client_secret_post")
        if token_method == "none":
            assert not reg.get("client_secret"), (
                "Public client should not receive a client_secret"
            )
        else:
            assert reg.get("client_secret"), (
                f"Client with auth_method={token_method} should receive a client_secret"
            )

        # Verify authorize endpoint is reachable
        auth_url = as_meta["authorization_endpoint"]
        auth_path = urlparse(auth_url).path
        authorize_resp = client.get(
            auth_path,
            params={
                "response_type": "code",
                "client_id": reg["client_id"],
                "code_challenge": "test-challenge",
                "code_challenge_method": "S256",
                "redirect_uri": "http://localhost:9999/callback",
                "state": "test-state",
                "scope": "tasks:read tasks:write",
                "resource": f"{MCP_BASE_URL}/mcp",
            },
        )
        # Should redirect to OIDC provider (302), not error
        assert authorize_resp.status_code == 302, (
            f"Authorize should redirect, got {authorize_resp.status_code}: "
            f"{authorize_resp.text[:200]}"
        )

    def test_token_exchange_after_registration(self, client: httpx.Client):
        """After registration, token exchange with correct client auth should
        pass the auth layer (fail on invalid code, not 401).

        This validates that the registered client_id + client_secret work
        correctly with the token endpoint auth.
        """
        # Discover
        as_resp = client.get("/.well-known/oauth-authorization-server/mcp")
        as_meta = as_resp.json()

        # Register (let server pick auth method)
        reg_resp = client.post(
            urlparse(as_meta["registration_endpoint"]).path,
            json={
                "redirect_uris": ["http://localhost:9999/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_name": "token-exchange-test",
            },
        )
        reg = reg_resp.json()

        # Build token request based on auth method
        token_data = {
            "grant_type": "authorization_code",
            "code": "fake-auth-code",
            "client_id": reg["client_id"],
            "code_verifier": "fake-verifier",
            "redirect_uri": "http://localhost:9999/callback",
        }
        auth_method = reg.get("token_endpoint_auth_method", "client_secret_post")
        if auth_method == "client_secret_post" and reg.get("client_secret"):
            token_data["client_secret"] = reg["client_secret"]

        token_path = urlparse(as_meta["token_endpoint"]).path
        resp = client.post(token_path, data=token_data)

        # Must NOT be 401 — auth layer should pass
        assert resp.status_code != 401, (
            f"Token auth failed (401) for auth_method={auth_method}: {resp.text}. "
            f"Registration returned: client_secret={'present' if reg.get('client_secret') else 'absent'}, "
            f"token_endpoint_auth_method={auth_method}"
        )
        # Should be 400 (invalid code) — proving auth passed but code is fake
        assert resp.status_code == 400, (
            f"Expected 400 (invalid code), got {resp.status_code}: {resp.text}"
        )
