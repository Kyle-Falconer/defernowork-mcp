"""Redis-backed store for MCP OAuth tokens, clients, and audit logs.

All keys use the ``mcp:`` prefix to avoid collisions with Deferno backend keys.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("defernowork-mcp")

# TTLs (seconds)
PENDING_AUTH_TTL = 600       # 10 minutes
AUTH_CODE_TTL = 300          # 5 minutes
ACCESS_TOKEN_TTL = 3600      # 1 hour
REFRESH_TOKEN_TTL = 604800   # 7 days
AUDIT_MAXLEN = 10_000


def _generate_token() -> str:
    """Generate a cryptographically random 32-byte hex token (256 bits)."""
    return secrets.token_hex(32)


class RedisStore:
    """Async Redis wrapper for all MCP OAuth state."""

    def __init__(self, redis_url: str | None = None) -> None:
        url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
        self._redis = aioredis.from_url(url, decode_responses=True)

    async def close(self) -> None:
        await self._redis.aclose()

    # ── Clients (RFC 7591 dynamic registration) ──────────────────────

    async def save_client(self, client_id: str, data: dict[str, Any]) -> None:
        await self._redis.set(f"mcp:client:{client_id}", json.dumps(data))

    async def load_client(self, client_id: str) -> dict[str, Any] | None:
        raw = await self._redis.get(f"mcp:client:{client_id}")
        return json.loads(raw) if raw else None

    # ── Pending auth (MCP authorize → Kanidm redirect) ───────────────

    async def save_pending_auth(self, nonce: str, data: dict[str, Any]) -> None:
        await self._redis.set(
            f"mcp:pending:{nonce}", json.dumps(data), ex=PENDING_AUTH_TTL,
        )

    async def load_pending_auth(self, nonce: str) -> dict[str, Any] | None:
        raw = await self._redis.get(f"mcp:pending:{nonce}")
        if raw:
            await self._redis.delete(f"mcp:pending:{nonce}")
            return json.loads(raw)
        return None

    # ── Authorization codes ──────────────────────────────────────────

    async def save_auth_code(
        self, code: str, data: dict[str, Any], meta: dict[str, Any] | None = None,
    ) -> None:
        """Save an authorization code.

        ``data`` is the AuthorizationCode-compatible dict (returned by load).
        ``meta`` holds extra fields (deferno_token, user_id) that survive
        the load (which is single-use) and are consumed by exchange.
        """
        pipe = self._redis.pipeline()
        pipe.set(f"mcp:auth_code:{code}", json.dumps(data), ex=AUTH_CODE_TTL)
        if meta:
            pipe.set(f"mcp:auth_code_meta:{code}", json.dumps(meta), ex=AUTH_CODE_TTL)
        await pipe.execute()

    async def load_auth_code(self, code: str) -> dict[str, Any] | None:
        raw = await self._redis.get(f"mcp:auth_code:{code}")
        if raw:
            await self._redis.delete(f"mcp:auth_code:{code}")  # single-use
            return json.loads(raw)
        return None

    async def load_auth_code_meta(self, code: str) -> dict[str, Any] | None:
        """Load and consume the metadata stored alongside an auth code."""
        raw = await self._redis.get(f"mcp:auth_code_meta:{code}")
        if raw:
            await self._redis.delete(f"mcp:auth_code_meta:{code}")
            return json.loads(raw)
        return None

    # ── Access tokens ────────────────────────────────────────────────

    async def save_access_token(
        self, token: str, data: dict[str, Any], ttl: int = ACCESS_TOKEN_TTL,
    ) -> None:
        pipe = self._redis.pipeline()
        pipe.set(f"mcp:access:{token}", json.dumps(data), ex=ttl)
        # Also store the Deferno backend token mapping if present
        if "deferno_token" in data:
            pipe.set(
                f"mcp:deferno_token:{token}",
                data["deferno_token"],
                ex=ttl,
            )
        await pipe.execute()

    async def load_access_token(self, token: str) -> dict[str, Any] | None:
        raw = await self._redis.get(f"mcp:access:{token}")
        return json.loads(raw) if raw else None

    async def load_deferno_token(self, mcp_token: str) -> str | None:
        return await self._redis.get(f"mcp:deferno_token:{mcp_token}")

    async def delete_access_token(self, token: str) -> None:
        pipe = self._redis.pipeline()
        pipe.delete(f"mcp:access:{token}")
        pipe.delete(f"mcp:deferno_token:{token}")
        await pipe.execute()

    # ── Refresh tokens ───────────────────────────────────────────────

    async def save_refresh_token(
        self, token: str, data: dict[str, Any], ttl: int = REFRESH_TOKEN_TTL,
    ) -> None:
        await self._redis.set(f"mcp:refresh:{token}", json.dumps(data), ex=ttl)

    async def load_refresh_token(self, token: str) -> dict[str, Any] | None:
        raw = await self._redis.get(f"mcp:refresh:{token}")
        return json.loads(raw) if raw else None

    async def delete_refresh_token(self, token: str) -> None:
        await self._redis.delete(f"mcp:refresh:{token}")

    # ── Audit logging (Redis Stream) ─────────────────────────────────

    async def audit_log(
        self,
        *,
        user_id: str,
        tool: str,
        client_id: str = "",
        session_id: str = "",
    ) -> None:
        try:
            await self._redis.xadd(
                "mcp:audit",
                {
                    "user_id": user_id,
                    "tool": tool,
                    "client_id": client_id,
                    "session_id": session_id,
                    "ts": str(time.time()),
                },
                maxlen=AUDIT_MAXLEN,
                approximate=True,
            )
        except Exception:
            logger.warning("Failed to write audit log", exc_info=True)
