"""Deferno MCP server.

Supports two transports:

**stdio** (default — local use with Claude Desktop / Code, Cursor, etc.)::

    python -m defernowork_mcp
    # or
    defernowork-mcp

**streamable-http** (remote — Claude.ai Connectors, any HTTP MCP client)::

    defernowork-mcp --transport http [--host 0.0.0.0] [--port 8080]

For HTTP transport, authentication is handled via OAuth 2.0:
  - The server exposes ``/.well-known/oauth-authorization-server`` (RFC 8414)
  - Clients discover endpoints, register dynamically (RFC 7591), and
    authenticate via Authorization Code + PKCE.
  - Identity is delegated to Kanidm (OIDC).

For stdio transport, authenticate once with::

    defernowork-mcp auth

This opens a browser-based login flow and saves the token to
``~/.config/defernowork/credentials.json``.  Alternatively, set
``DEFERNO_TOKEN`` as an environment variable.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import unquote

from mcp.server.fastmcp import Context, FastMCP

from .client import DefernoClient, DefernoError
from .credentials import load_credentials
from .tools import register_auth, register_tasks, register_daily_plan

__all__ = ["create_server", "main", "main_http", "DefernoClient", "DEFAULT_BASE_URL"]

logger = logging.getLogger("defernowork-mcp")

DEFAULT_BASE_URL = "http://127.0.0.1:3000"

_UNSET = object()
"""Sentinel for 'caller did not provide this argument'.

Using this instead of None lets us distinguish between 'clear the field'
(explicit None) and 'don't touch the field' (not provided / _UNSET).
"""

# Module-level reference to the OAuth provider (set in create_server for HTTP mode).
# Used by oauth_callback.py to handle the Kanidm redirect.
_oauth_provider: Any = None

# Module-level reference to the Redis store (set in create_server for HTTP mode).
_redis_store: Any = None

_http_transport_mode = False


def _resolve_base_url() -> str:
    """Resolve the backend URL from env, saved credentials, or default."""
    base_url = os.environ.get("DEFERNO_BASE_URL", DEFAULT_BASE_URL)
    if not os.environ.get("DEFERNO_BASE_URL"):
        creds = load_credentials()
        if creds:
            base_url = creds.get("base_url", base_url)
    return base_url


def _get_client(ctx: Context | None = None) -> DefernoClient:
    """Return a DefernoClient for the current request/session.

    Token resolution order:
    **HTTP transport with OAuth:**
      1. Extract MCP access token from the authenticated context.
      2. Look up the associated Deferno backend token from Redis.

    **HTTP transport (legacy, no OAuth):**
      Falls back to None (user must use start_auth/complete_auth tools).

    **stdio transport (local single-user):**
      1. ``DEFERNO_TOKEN`` env var
      2. Saved credentials on disk
    """
    base_url = _resolve_base_url()

    if _http_transport_mode:
        token = None
        # In OAuth mode, the Deferno token is stored in Redis alongside
        # the MCP access token. We need to get the MCP access token from
        # the auth context and look up the Deferno token.
        #
        # For now during migration, also support the legacy in-memory cache
        # for stdio-over-HTTP testing.  This will be removed in Phase 3.
        if ctx is not None and _redis_store is not None:
            # Try to get the MCP access token from Starlette auth context
            try:
                from mcp.server.auth.middleware.auth_context import (
                    get_access_token,
                )
                access_token = get_access_token()
                if access_token:
                    import asyncio
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # We're in an async context — use the store directly
                        # This is called from async tool handlers, so we can't
                        # do a sync lookup.  Use a cached approach instead.
                        pass
            except Exception:
                pass
        return DefernoClient(base_url=base_url, token=token)

    # stdio mode: single user, safe to check env and disk.
    token = os.environ.get("DEFERNO_TOKEN")
    if token is None:
        creds = load_credentials()
        if creds:
            token = creds.get("token")
    return DefernoClient(base_url=base_url, token=token)


async def _get_client_async(ctx: Context | None = None) -> DefernoClient:
    """Async version of _get_client that can do Redis lookups."""
    base_url = _resolve_base_url()

    if _http_transport_mode and _redis_store is not None:
        token = None
        try:
            from mcp.server.auth.middleware.auth_context import get_access_token
            access_token = get_access_token()
            if access_token:
                token = await _redis_store.load_deferno_token(access_token.token)
                if token:
                    logger.debug("Resolved Deferno token from MCP access token")
        except Exception:
            logger.debug("Could not resolve token from auth context", exc_info=True)
        return DefernoClient(base_url=base_url, token=token)

    if not _http_transport_mode:
        token = os.environ.get("DEFERNO_TOKEN")
        if token is None:
            creds = load_credentials()
            if creds:
                token = creds.get("token")
        return DefernoClient(base_url=base_url, token=token)

    return DefernoClient(base_url=base_url, token=None)


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop _UNSET-valued keys so POST/PATCH bodies stay minimal."""
    return {k: v for k, v in payload.items() if v is not _UNSET}


def _format_error(exc: DefernoError) -> str:
    return f"Deferno API error {exc.status_code}: {exc.message}"


def create_server(http_transport: bool = False) -> FastMCP:
    global _http_transport_mode, _oauth_provider, _redis_store
    _http_transport_mode = http_transport

    security_kwargs: dict = {}
    try:
        from mcp.server.transport_security import TransportSecuritySettings

        raw = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
        allowed_hosts = [h.strip() for h in raw.split(",") if h.strip()] if raw else []
        for default in ("localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"):
            if default not in allowed_hosts:
                allowed_hosts.append(default)

        security_kwargs["transport_security"] = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
        )
    except ImportError:
        pass

    # ── OAuth configuration (HTTP mode only) ──────────────────────
    auth_kwargs: dict = {}
    if http_transport and os.environ.get("KANIDM_ISSUER_URL"):
        from mcp.server.auth.settings import (
            AuthSettings,
            ClientRegistrationOptions,
            RevocationOptions,
        )
        from .kanidm_oidc import KanidmOIDCClient
        from .oauth_provider import DefernoOAuthProvider
        from .redis_store import RedisStore

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_store = RedisStore(redis_url)

        mcp_public_url = os.environ.get("MCP_PUBLIC_URL", "https://deferno.work/mcp")
        kanidm_callback_url = f"{mcp_public_url}/oauth/kanidm-callback"

        kanidm = KanidmOIDCClient(
            issuer_url=os.environ["KANIDM_ISSUER_URL"],
            client_id=os.environ.get("KANIDM_CLIENT_ID", "deferno-mcp"),
            client_secret=os.environ.get("KANIDM_CLIENT_SECRET", ""),
            callback_url=kanidm_callback_url,
        )

        backend_url = os.environ.get(
            "DEFERNO_INTERNAL_URL",
            os.environ.get("DEFERNO_BASE_URL", DEFAULT_BASE_URL),
        )
        _oauth_provider = DefernoOAuthProvider(
            store=_redis_store,
            kanidm=kanidm,
            backend_internal_url=backend_url,
        )

        auth_kwargs["auth_server_provider"] = _oauth_provider
        auth_kwargs["auth"] = AuthSettings(
            issuer_url=mcp_public_url,
            resource_server_url=mcp_public_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=[
                    "tasks:read", "tasks:write",
                    "plan:read", "plan:write",
                    "profile:read",
                ],
                default_scopes=[
                    "tasks:read", "tasks:write",
                    "plan:read", "plan:write",
                    "profile:read",
                ],
            ),
            revocation_options=RevocationOptions(enabled=True),
        )
        logger.info("OAuth 2.0 AS configured: issuer=%s", mcp_public_url)

    instructions = (
        "Tools for managing a user's Deferno tasks. "
        "Authentication is handled via OAuth 2.0 — if you receive a 401, "
        "follow the standard OAuth discovery flow (RFC 9728 PRM → RFC 8414 "
        "AS metadata → Authorization Code + PKCE). "
        "Use `whoami` to confirm authentication, `list_tasks` or the "
        "`defernowork://tasks` resource to index the user's current tasks, and "
        "`create_task` / `update_task` for normal CRUD. Use "
        "`split_task` to decompose a task into two subtasks, `fold_task` to insert "
        "a next-step task in a sequence, and `merge_task` to roll active children "
        "back into their parent. "
        "Use `get_daily_plan` to see today's curated plan (auto-seeded from "
        "recurring tasks + carried-forward items), `add_to_plan` / "
        "`remove_from_plan` to manage it. When the user asks about their "
        "current tasks or what they should work on today, prefer "
        "`get_daily_plan` over `list_tasks`."
    )

    mcp = FastMCP(
        "defernowork",
        instructions=instructions,
        **security_kwargs,
        **auth_kwargs,
    )

    # ── Register tool modules ─────────────────────────────────────
    register_auth(mcp, _get_client_async, _get_anon_client, _format_error)
    register_tasks(mcp, _get_client_async, _format_error, _compact, _UNSET)
    register_daily_plan(mcp, _get_client_async, _format_error)

    # ── Resources ─────────────────────────────────────────────────
    @mcp.resource("defernowork://tasks")
    async def all_tasks_resource() -> str:
        """All tasks owned by the authenticated user (JSON array)."""
        async with (await _get_client_async()) as client:
            tasks = await client.list_tasks()
        return json.dumps(tasks, indent=2)

    @mcp.resource("defernowork://tasks/plan")
    async def plan_resource() -> str:
        """Today's curated daily plan (JSON array)."""
        async with (await _get_client_async()) as client:
            plan = await client.get_daily_plan()
        return json.dumps(plan, indent=2)

    @mcp.resource("defernowork://tasks/mood-history")
    async def mood_history_resource() -> str:
        """Mood history for finished tasks (JSON array)."""
        async with (await _get_client_async()) as client:
            history = await client.mood_history()
        return json.dumps(history, indent=2)

    @mcp.resource("defernowork://task/{task_id}")
    async def task_resource(task_id: str) -> str:
        """A single task, addressable by UUID as ``defernowork://task/<id>``."""
        async with (await _get_client_async()) as client:
            task = await client.get_task(unquote(task_id))
        return json.dumps(task, indent=2)

    return mcp


def _get_anon_client() -> DefernoClient:
    """Return an unauthenticated DefernoClient (for auth init/verify)."""
    return DefernoClient(base_url=_resolve_base_url())


# ----------------------------------------------------------------- transports

def main() -> None:
    """Entry point for stdio transport (Claude Desktop / Code, Cursor, etc.)."""
    logging.basicConfig(level=os.environ.get("DEFERNO_LOG_LEVEL", "WARNING"))
    create_server().run()


def main_http(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Entry point for remote HTTP/SSE transport."""
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "uvicorn is required for HTTP transport: pip install 'defernowork-mcp[http]'"
        ) from exc

    log_level = os.environ.get("DEFERNO_LOG_LEVEL", "WARNING").lower()
    logging.basicConfig(level=log_level.upper())

    mcp = create_server(http_transport=True)

    if hasattr(mcp, "streamable_http_app"):
        mcp_asgi = mcp.streamable_http_app()
    elif hasattr(mcp, "sse_app"):
        logger.warning(
            "streamable_http_app() not available; falling back to SSE transport. "
            "Upgrade: pip install 'mcp>=1.2.0'"
        )
        mcp_asgi = mcp.sse_app()
    else:
        raise SystemExit(
            "mcp package does not expose an HTTP ASGI app. "
            "Install mcp>=1.2.0: pip install 'mcp>=1.2.0'"
        )

    # If OAuth is configured, add the Kanidm callback route
    if _oauth_provider is not None:
        from starlette.applications import Starlette
        from starlette.routing import Route, Mount
        from .oauth_callback import kanidm_callback

        # The MCP Starlette app already has auth routes.
        # We need to add our Kanidm callback.  Since mcp_asgi is a
        # Starlette app, we can add routes to it.
        if isinstance(mcp_asgi, Starlette):
            mcp_asgi.routes.append(
                Route("/oauth/kanidm-callback", kanidm_callback, methods=["GET"]),
            )
        else:
            logger.warning(
                "Cannot add Kanidm callback route: mcp_asgi is not a Starlette app"
            )

    uvicorn.run(mcp_asgi, host=host, port=port, log_level=log_level)
