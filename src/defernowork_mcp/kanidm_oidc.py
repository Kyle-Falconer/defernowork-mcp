"""Kanidm OIDC client for the upstream identity leg of the OAuth dance.

Handles OIDC discovery, building authorization URLs with PKCE, exchanging
authorization codes for ID tokens, and extracting user identity.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("defernowork-mcp")


@dataclass(frozen=True)
class KanidmIdentity:
    """Identity extracted from a Kanidm ID token."""
    subject: str
    username: str
    display_name: str
    email: str | None


@dataclass(frozen=True)
class KanidmPKCE:
    """PKCE challenge + verifier pair."""
    verifier: str
    challenge: str

    @staticmethod
    def generate() -> KanidmPKCE:
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return KanidmPKCE(verifier=verifier, challenge=challenge)


class KanidmOIDCClient:
    """Async OIDC client that talks to Kanidm."""

    def __init__(
        self,
        issuer_url: str,
        client_id: str,
        client_secret: str,
        callback_url: str,
    ) -> None:
        self.issuer_url = issuer_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.callback_url = callback_url
        self._discovery: dict[str, Any] | None = None
        self._http = httpx.AsyncClient(verify=False, timeout=15)

    async def close(self) -> None:
        await self._http.aclose()

    async def _discover(self) -> dict[str, Any]:
        if self._discovery is None:
            url = f"{self.issuer_url}/.well-known/openid-configuration"
            resp = await self._http.get(url)
            resp.raise_for_status()
            self._discovery = resp.json()
        return self._discovery

    async def authorization_url(
        self,
        state: str,
        pkce: KanidmPKCE,
        scopes: list[str] | None = None,
    ) -> str:
        """Build the Kanidm authorize URL with PKCE."""
        disc = await self._discover()
        endpoint = disc["authorization_endpoint"]
        scope_str = " ".join(scopes or ["openid", "email", "profile"])
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.callback_url,
            "response_type": "code",
            "scope": scope_str,
            "state": state,
            "code_challenge": pkce.challenge,
            "code_challenge_method": "S256",
        }
        qs = "&".join(f"{k}={httpx.URL('', params={k: v}).params}" for k, v in params.items())
        # Build manually to avoid double-encoding
        return f"{endpoint}?{'&'.join(f'{k}={v}' for k, v in httpx.QueryParams(params).multi_items())}"

    async def exchange_code(
        self,
        code: str,
        pkce_verifier: str,
    ) -> KanidmIdentity:
        """Exchange a Kanidm authorization code for an ID token and extract identity."""
        disc = await self._discover()
        token_endpoint = disc["token_endpoint"]

        resp = await self._http.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.callback_url,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code_verifier": pkce_verifier,
            },
        )
        resp.raise_for_status()
        token_data = resp.json()

        # Fetch userinfo for identity claims (simpler than JWT validation
        # for an internal trust boundary — Kanidm is on our Docker network)
        userinfo_endpoint = disc.get("userinfo_endpoint")
        if userinfo_endpoint:
            access_token = token_data["access_token"]
            ui_resp = await self._http.get(
                userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            ui_resp.raise_for_status()
            claims = ui_resp.json()
        else:
            # Fall back to decoding ID token claims without signature
            # verification (internal trust boundary)
            import json
            from base64 import urlsafe_b64decode
            id_token = token_data["id_token"]
            payload = id_token.split(".")[1]
            payload += "=" * (4 - len(payload) % 4)
            claims = json.loads(urlsafe_b64decode(payload))

        return KanidmIdentity(
            subject=claims.get("sub", ""),
            username=claims.get("preferred_username", claims.get("sub", "")),
            display_name=claims.get("name", claims.get("preferred_username", "")),
            email=claims.get("email"),
        )
