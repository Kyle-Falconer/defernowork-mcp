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
  - Identity is delegated to an upstream OIDC provider (Zitadel).

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

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from .client import DefernoClient, DefernoError
from .credentials import load_credentials
from .refs import COMPACT_ITEM_FIELDS, project, resolve_ref
from .tools import (
    register_auth,
    register_capture,
    register_comments,
    register_daily_plan,
    register_event_occurrences,
    register_item_activity,
    register_items,
    register_occurrences,
    register_tasks,
)

__all__ = [
    "create_server",
    "main",
    "main_http",
    "transport_security_settings",
    "DefernoClient",
    "DEFAULT_BASE_URL",
]

logger = logging.getLogger("defernowork-mcp")

DEFAULT_BASE_URL = "http://127.0.0.1:3000/api"

_UNSET = object()
"""Sentinel for 'caller did not provide this argument'.

Using this instead of None lets us distinguish between 'clear the field'
(explicit None) and 'don't touch the field' (not provided / _UNSET).
"""

# Module-level reference to the OAuth provider (set in create_server for HTTP mode).
# Used by oauth_callback.py to handle the OIDC redirect.
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
      Falls back to None (no in-band auth; the caller must supply a token).

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
    if exc.code:
        return f"Deferno API error {exc.status_code} [{exc.code}]: {exc.message}"
    return f"Deferno API error {exc.status_code}: {exc.message}"


def transport_security_settings() -> TransportSecuritySettings:
    """DNS rebinding protection for the HTTP transport.

    ``MCP_ALLOWED_HOSTS`` adds hosts as a comma-separated list. The loopback
    names are always allowed.

    These settings belong to the transport, not to the server. ``main_http``
    passes them to ``streamable_http_app``.
    """
    raw = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    allowed_hosts = [h.strip() for h in raw.split(",") if h.strip()] if raw else []
    for default in ("localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"):
        if default not in allowed_hosts:
            allowed_hosts.append(default)

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
    )


def create_server(http_transport: bool = False) -> MCPServer:
    global _http_transport_mode, _oauth_provider, _redis_store
    _http_transport_mode = http_transport

    # ── OAuth configuration (HTTP mode only) ──────────────────────
    auth_kwargs: dict = {}
    if http_transport and os.environ.get("ZITADEL_ISSUER_URL"):
        from mcp.server.auth.settings import (
            AuthSettings,
            ClientRegistrationOptions,
            RevocationOptions,
        )
        from .oidc_client import OidcClient
        from .oauth_provider import DefernoOAuthProvider
        from .redis_store import RedisStore

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        _redis_store = RedisStore(redis_url)

        mcp_public_url = os.environ.get("MCP_PUBLIC_URL", "https://app.defernowork.com/mcp")
        oidc_callback_url = f"{mcp_public_url}/oauth/oidc-callback"

        oidc = OidcClient(
            issuer_url=os.environ["ZITADEL_ISSUER_URL"],
            client_id=os.environ.get("ZITADEL_CLIENT_ID", "deferno-mcp"),
            client_secret=os.environ.get("ZITADEL_CLIENT_SECRET", ""),
            callback_url=oidc_callback_url,
        )

        backend_url = os.environ.get(
            "DEFERNO_INTERNAL_URL",
            os.environ.get("DEFERNO_BASE_URL", DEFAULT_BASE_URL),
        )
        _oauth_provider = DefernoOAuthProvider(
            store=_redis_store,
            oidc=oidc,
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
        "Use `whoami` to confirm authentication, `list_items` to index the "
        "user's current items, `search_items` to find items by text, and "
        "`get_item` for full detail on one item. "
        "To create any item, use `capture_item`: answer how it behaves "
        "(`attend`? `repeats`? `obligation` need-vs-want) and the server derives "
        "Task / Chore / Habit / Event. For a subtask under a parent, capture then "
        "`move_item`; for a `desire` score or a sequence chain, capture then "
        "`update_item`. `update_item` edits any existing item, `delete_item` "
        "removes it, `move_item` reparents/reorders it, and `convert_item` "
        "changes its kind — all take any item reference and resolve its kind "
        "for you. "
        "Use `get_daily_plan` to see today's curated plan (auto-seeded from "
        "recurring items + carried-forward items), `add_to_items_plan` / "
        "`remove_from_items_plan` to manage it. When the user asks about their "
        "current tasks or what they should work on today, prefer "
        "`get_daily_plan` over `list_items`. Its rows are kind-tagged (`task` "
        "| `habit` | `chore` | `event`); on the three recurring kinds read "
        "done-ness from `today_occurrence.status` (`scheduled`/`in_progress` = "
        "open, `done_on_time`/`done_late` = done, `dropped` = refused for "
        "today, resolved but never done) — a recurring item's own `status` is "
        "`active`/`archived` and can never say `done`. "
        "Identifiers: every tool that takes an item reference accepts any Ref "
        "input form — a UUID, a Sequence shorthand (`#123`, your personal org "
        "only), a Canonical ref (`acme-123`, resolves cross-org), or an app URL "
        "— and resolves it to the item transparently before acting. Reads return "
        "a Compact projection by default (a small whitelist of fields; the heavy "
        "`description`/body is included on a single-item `get_item` but dropped "
        "from list rows) — pass `full=true` for the complete record. "
        "To comment on or attach files to any item, use the kind-neutral "
        "item-level tools — `post_item_comment` / `list_item_comments` and "
        "`presign_item_attachments` / `commit_item_attachments` / "
        "`list_item_attachments` / `delete_item_attachment` / "
        "`set_item_attachment_caption` — which take any item reference; pass an "
        "optional occurrence `date` to target a specific Event occurrence. "
        "Edit or delete a comment with `update_comment` / `delete_comment` by "
        "comment id; an Event-occurrence comment is edited/deleted with "
        "`patch_event_occurrence_comment` / `delete_event_occurrence_comment`."
    )

    mcp = MCPServer(
        "defernowork",
        instructions=instructions,
        **auth_kwargs,
    )

    # ── Register tool modules ─────────────────────────────────────
    register_auth(mcp, _get_client_async, _format_error)
    register_tasks(mcp, _get_client_async, _format_error, _compact, _UNSET)
    register_capture(mcp, _get_client_async, _format_error)
    register_event_occurrences(mcp, _get_client_async, _format_error)
    register_occurrences(mcp, _get_client_async, _format_error)
    register_comments(mcp, _get_client_async, _format_error, _compact, _UNSET)
    register_items(mcp, _get_client_async, _format_error, _compact, _UNSET)
    register_item_activity(mcp, _get_client_async, _format_error)
    register_daily_plan(mcp, _get_client_async, _format_error)

    # ── Resources ─────────────────────────────────────────────────
    # Bounded surfaces only (ADR-0002): the unbounded all-tasks resource is
    # gone; plan + mood-history are naturally bounded; single-item reads go
    # through the any-ref item resource below (Compact projection).
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

    @mcp.resource("defernowork://item/{ref}")
    async def item_resource(ref: str) -> str:
        """A single item (Task/Habit/Chore/Event) by any Ref input form.

        ``{ref}`` accepts any reference the classifier auto-routes — a UUID, a
        Sequence shorthand (``#123``, which arrives URL-encoded as ``%23123``),
        or a Canonical ref (``acme-123``). It is resolved transparently and the
        item is returned in **Compact projection** (a small whitelist of fields
        including ``description``; heavy arrays are dropped).
        """
        async with (await _get_client_async()) as client:
            item_id = await resolve_ref(client, unquote(ref))
            record = await client.get_item(item_id)
        return json.dumps(project(record, COMPACT_ITEM_FIELDS), indent=2)

    return mcp


# ----------------------------------------------------------------- transports

def main() -> None:
    """Entry point for stdio transport (Claude Desktop / Code, Cursor, etc.)."""
    logging.basicConfig(level=os.environ.get("DEFERNO_LOG_LEVEL", "WARNING"))
    create_server().run("stdio")


def main_http(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Entry point for the remote streamable-http transport."""
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "uvicorn is required for HTTP transport: pip install 'defernowork-mcp[http]'"
        ) from exc

    log_level = os.environ.get("DEFERNO_LOG_LEVEL", "WARNING").lower()
    logging.basicConfig(level=log_level.upper())

    mcp = create_server(http_transport=True)

    mcp_asgi = mcp.streamable_http_app(
        transport_security=transport_security_settings(),
        host=host,
    )

    # If OAuth is configured, add custom routes
    if _oauth_provider is not None:
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route, Mount
        from .oauth_callback import oidc_callback

        # Custom OAuth/OIDC discovery metadata.
        #
        # We override MCPServer's built-in /.well-known/oauth-authorization-server
        # because the upstream ClientAuthenticator has a bug: it requires
        # client_id in the form body even for client_secret_basic, but the
        # TypeScript MCP SDK (used by Claude Code) only sends it in the
        # Authorization header per RFC 6749.  By advertising only
        # client_secret_post, clients send client_id in the form body.
        #
        # We also serve /.well-known/openid-configuration as an alias for
        # clients (like Claude.ai's connector) that use OIDC discovery.
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

        if isinstance(mcp_asgi, Starlette):
            # Insert at position 0 to override MCPServer's built-in routes
            mcp_asgi.routes.insert(0,
                Route("/.well-known/oauth-authorization-server", oauth_metadata_handler, methods=["GET"]),
            )
            mcp_asgi.routes.insert(1,
                Route("/.well-known/openid-configuration", oauth_metadata_handler, methods=["GET"]),
            )
            mcp_asgi.routes.append(
                Route("/oauth/oidc-callback", oidc_callback, methods=["GET"]),
            )
        else:
            logger.warning(
                "Cannot add OIDC callback route: mcp_asgi is not a Starlette app"
            )

    uvicorn.run(mcp_asgi, host=host, port=port, log_level=log_level)
