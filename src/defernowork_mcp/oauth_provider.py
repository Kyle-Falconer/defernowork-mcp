"""OAuth 2.0 Authorization Server provider that delegates identity to the upstream OIDC provider.

Implements the ``OAuthAuthorizationServerProvider`` protocol from the MCP library.
All persistent state is stored in Redis via ``RedisStore``.

Flow overview:
  1. Client calls /authorize → we redirect to the upstream identity provider
  2. User authenticates → the upstream provider redirects to our callback
  3. Callback exchanges the OIDC code, obtains Deferno session, issues MCP auth code
  4. Client exchanges auth code at /token → we return access + refresh tokens
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Any

import httpx
from pydantic import AnyUrl

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthToken,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull

from .oidc_client import OidcClient, OidcPKCE
from .redis_store import (
    ACCESS_TOKEN_TTL,
    REFRESH_TOKEN_TTL,
    RedisStore,
    _generate_token,
)

logger = logging.getLogger("defernowork-mcp")


class DefernoOAuthProvider:
    """MCP OAuth AS backed by an upstream OIDC provider (identity) and Redis (state)."""

    def __init__(
        self,
        store: RedisStore,
        oidc: OidcClient,
        backend_internal_url: str,
    ) -> None:
        self.store = store
        self.oidc = oidc
        self.backend_internal_url = backend_internal_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=10)

    # ── Client registration (RFC 7591) ───────────────────────────────

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = await self.store.load_client(client_id)
        if data is None:
            return None
        return OAuthClientInformationFull(**data)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        client_id = client_info.client_id or secrets.token_hex(16)
        client_secret = client_info.client_secret or secrets.token_hex(32)
        client_info.client_id = client_id
        client_info.client_secret = client_secret
        client_info.client_id_issued_at = int(time.time())
        await self.store.save_client(client_id, client_info.model_dump(mode="json"))

    # ── Authorization ────────────────────────────────────────────────

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Redirect user to the upstream OIDC provider for authentication.

        Stores the MCP authorization params in Redis so the OIDC callback
        can complete the flow.
        """
        nonce = secrets.token_hex(20)
        pkce = OidcPKCE.generate()

        await self.store.save_pending_auth(nonce, {
            # MCP client's original params (need these to issue the auth code)
            "client_id": client.client_id,
            "client_name": client.client_name or "",
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "state": params.state,
            "scopes": params.scopes,
            "code_challenge": params.code_challenge,
            "resource": params.resource,
            # OIDC PKCE state (to exchange the OIDC code later)
            "oidc_pkce_verifier": pkce.verifier,
        })

        # Build the OIDC authorization URL.  The nonce is our state param
        # so the callback can look up the pending auth.
        return await self.oidc.authorization_url(
            state=nonce,
            pkce=pkce,
        )

    # ── OIDC callback (called from oauth_callback.py route) ────────

    async def handle_oidc_callback(
        self,
        oidc_state: str,
        oidc_code: str,
    ) -> tuple[str, str, str | None]:
        """Process the OIDC redirect and return (mcp_auth_code, redirect_uri, state).

        This is called by the Starlette callback route, not by the MCP framework.
        """
        # 1. Load pending auth
        pending = await self.store.load_pending_auth(oidc_state)
        if pending is None:
            raise ValueError("Unknown or expired authorization session")

        # 2. Exchange OIDC code for identity
        identity = await self.oidc.exchange_code(
            code=oidc_code,
            pkce_verifier=pending["oidc_pkce_verifier"],
        )
        logger.info(
            "OIDC identity: sub=%s user=%s",
            identity.subject, identity.username,
        )

        # 3. Get a Deferno backend session for this user
        deferno_token = await self._get_deferno_session(
            oidc_subject=identity.subject,
            oidc_username=identity.username,
            mcp_client_id=pending["client_id"],
            mcp_client_name=pending.get("client_name", ""),
        )

        # 4. Generate MCP authorization code
        mcp_code = _generate_token()
        await self.store.save_auth_code(
            mcp_code,
            data={
                "code": mcp_code,
                "client_id": pending["client_id"],
                "scopes": pending["scopes"] or [],
                "code_challenge": pending["code_challenge"],
                "redirect_uri": pending["redirect_uri"],
                "redirect_uri_provided_explicitly": pending["redirect_uri_provided_explicitly"],
                "resource": pending.get("resource"),
                "expires_at": time.time() + 300,
            },
            meta={
                "deferno_token": deferno_token,
                "user_id": identity.subject,
                "username": identity.username,
            },
        )

        return mcp_code, pending["redirect_uri"], pending["state"]

    async def _get_deferno_session(
        self,
        oidc_subject: str,
        oidc_username: str,
        mcp_client_id: str,
        mcp_client_name: str,
    ) -> str:
        """Call the Deferno auth service to create a session from OIDC identity."""
        secret = os.environ["INTERNAL_SHARED_SECRET"]
        resp = await self._http.post(
            f"{self.backend_internal_url}/internal/mcp-session",
            json={
                "oidc_subject": oidc_subject,
                "oidc_username": oidc_username,
                "mcp_client_id": mcp_client_id,
                "mcp_client_name": mcp_client_name,
            },
            headers={"X-Internal-Secret": secret},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["token"]

    # ── Authorization code exchange ──────────────────────────────────

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        data = await self.store.load_auth_code(authorization_code)
        if data is None:
            return None
        if data["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=data["code"],
            scopes=data["scopes"],
            expires_at=data["expires_at"],
            client_id=data["client_id"],
            code_challenge=data["code_challenge"],
            redirect_uri=data["redirect_uri"],
            redirect_uri_provided_explicitly=data["redirect_uri_provided_explicitly"],
            resource=data.get("resource"),
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        # load_authorization_code consumed the main key but left the meta key.
        meta = await self.store.load_auth_code_meta(authorization_code.code) or {}
        deferno_token = meta.get("deferno_token", "")
        user_id = meta.get("user_id", "")

        # Issue tokens
        access_tok = _generate_token()
        refresh_tok = _generate_token()

        await self.store.save_access_token(access_tok, {
            "token": access_tok,
            "client_id": client.client_id,
            "scopes": authorization_code.scopes,
            "expires_at": int(time.time()) + ACCESS_TOKEN_TTL,
            "user_id": user_id,
            "deferno_token": deferno_token,
            "resource": authorization_code.resource,
        })

        await self.store.save_refresh_token(refresh_tok, {
            "token": refresh_tok,
            "client_id": client.client_id,
            "scopes": authorization_code.scopes,
            "expires_at": int(time.time()) + REFRESH_TOKEN_TTL,
            "access_token": access_tok,
            "user_id": user_id,
            "deferno_token": deferno_token,
        })

        return OAuthToken(
            access_token=access_tok,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
            refresh_token=refresh_tok,
        )

    # ── Access token validation ──────────────────────────────────────

    async def load_access_token(self, token: str) -> AccessToken | None:
        data = await self.store.load_access_token(token)
        if data is None:
            return None
        return AccessToken(
            token=data["token"],
            client_id=data["client_id"],
            scopes=data["scopes"],
            expires_at=data.get("expires_at"),
            resource=data.get("resource"),
        )

    # ── Refresh token ────────────────────────────────────────────────

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        data = await self.store.load_refresh_token(refresh_token)
        if data is None:
            return None
        if data["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=data["token"],
            client_id=data["client_id"],
            scopes=data["scopes"],
            expires_at=data.get("expires_at"),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Load full refresh data to get deferno_token and user_id
        data = await self.store.load_refresh_token(refresh_token.token)
        old_deferno_token = data.get("deferno_token", "") if data else ""
        user_id = data.get("user_id", "") if data else ""

        # Delete old tokens
        old_access = data.get("access_token", "") if data else ""
        if old_access:
            await self.store.delete_access_token(old_access)
        await self.store.delete_refresh_token(refresh_token.token)

        # Issue new tokens
        new_access = _generate_token()
        new_refresh = _generate_token()
        effective_scopes = scopes or refresh_token.scopes

        await self.store.save_access_token(new_access, {
            "token": new_access,
            "client_id": client.client_id,
            "scopes": effective_scopes,
            "expires_at": int(time.time()) + ACCESS_TOKEN_TTL,
            "user_id": user_id,
            "deferno_token": old_deferno_token,
        })

        await self.store.save_refresh_token(new_refresh, {
            "token": new_refresh,
            "client_id": client.client_id,
            "scopes": effective_scopes,
            "expires_at": int(time.time()) + REFRESH_TOKEN_TTL,
            "access_token": new_access,
            "user_id": user_id,
            "deferno_token": old_deferno_token,
        })

        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            scope=" ".join(effective_scopes) if effective_scopes else None,
            refresh_token=new_refresh,
        )

    # ── Revocation (RFC 7009) ────────────────────────────────────────

    async def revoke_token(
        self,
        token: AccessToken | RefreshToken,
    ) -> None:
        if isinstance(token, AccessToken):
            await self.store.delete_access_token(token.token)
        elif isinstance(token, RefreshToken):
            # Also revoke the associated access token
            data = await self.store.load_refresh_token(token.token)
            if data and "access_token" in data:
                await self.store.delete_access_token(data["access_token"])
            await self.store.delete_refresh_token(token.token)
