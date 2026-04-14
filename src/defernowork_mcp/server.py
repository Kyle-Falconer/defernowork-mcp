"""Deferno MCP server.

Supports two transports:

**stdio** (default — local use with Claude Desktop / Code, Cursor, etc.)::

    python -m defernowork_mcp
    # or
    defernowork-mcp

**streamable-http** (remote — Claude.ai Connectors, any HTTP MCP client)::

    defernowork-mcp --transport http [--host 0.0.0.0] [--port 8080]

For HTTP transport, include your Deferno bearer token in every request::

    Authorization: Bearer <your-token>

For stdio transport, authenticate once with::

    defernowork-mcp auth

This opens a browser-based login flow and saves the token to
``~/.config/defernowork/credentials.json``.  Alternatively, set
``DEFERNO_TOKEN`` as an environment variable.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
from typing import Any
from urllib.parse import unquote

from mcp.server.fastmcp import FastMCP

from .client import DefernoClient, DefernoError
from .credentials import load_credentials
from .tools import register_auth, register_tasks, register_daily_plan

__all__ = ["create_server", "main", "main_http", "DefernoClient", "DEFAULT_BASE_URL"]

logger = logging.getLogger("defernowork-mcp")

DEFAULT_BASE_URL = "http://127.0.0.1:3000"

# Per-request Bearer token injected by the HTTP auth middleware.
_request_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "deferno_request_token", default=None
)

_UNSET = object()
"""Sentinel for 'caller did not provide this argument'.

Using this instead of None lets us distinguish between 'clear the field'
(explicit None) and 'don't touch the field' (not provided / _UNSET).
"""


def _resolve_base_url() -> str:
    """Resolve the backend URL from env, saved credentials, or default."""
    base_url = os.environ.get("DEFERNO_BASE_URL", DEFAULT_BASE_URL)
    if not os.environ.get("DEFERNO_BASE_URL"):
        creds = load_credentials()
        if creds:
            base_url = creds.get("base_url", base_url)
    return base_url


def _get_client() -> DefernoClient:
    """Return a DefernoClient for the current request/session.

    Token resolution order:
    1. Per-request Bearer header (HTTP transport)
    2. ``DEFERNO_TOKEN`` env var
    3. Saved credentials on disk (``~/.config/defernowork/credentials.json``)
    """
    base_url = _resolve_base_url()
    token = _request_token.get() or os.environ.get("DEFERNO_TOKEN")
    if token is None:
        creds = load_credentials()
        if creds:
            token = creds.get("token")
    return DefernoClient(base_url=base_url, token=token)


def _get_anon_client() -> DefernoClient:
    """Return an unauthenticated DefernoClient (for auth init/verify)."""
    return DefernoClient(base_url=_resolve_base_url())


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop _UNSET-valued keys so POST/PATCH bodies stay minimal.

    Explicit ``None`` is preserved (sent as JSON null) so the backend
    can distinguish 'clear this field' from 'leave it unchanged'.
    """
    return {k: v for k, v in payload.items() if v is not _UNSET}


def _format_error(exc: DefernoError) -> str:
    return f"Deferno API error {exc.status_code}: {exc.message}"


def create_server(http_transport: bool = False) -> FastMCP:
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

    instructions = (
        "Tools for managing a user's Deferno tasks. "
        "Authentication is handled via the Authorization: Bearer <token> "
        "header — no login tool call is needed or available. "
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
    )

    # ── Register tool modules ─────────────────────────────────────────
    register_auth(mcp, _get_client, _get_anon_client, _format_error)
    register_tasks(mcp, _get_client, _format_error, _compact, _UNSET)
    register_daily_plan(mcp, _get_client, _format_error)

    # ── Resources ─────────────────────────────────────────────────────
    @mcp.resource("defernowork://tasks")
    async def all_tasks_resource() -> str:
        """All tasks owned by the authenticated user (JSON array)."""
        async with _get_client() as client:
            tasks = await client.list_tasks()
        return json.dumps(tasks, indent=2)

    @mcp.resource("defernowork://tasks/plan")
    async def plan_resource() -> str:
        """Today's curated daily plan (JSON array)."""
        async with _get_client() as client:
            plan = await client.get_daily_plan()
        return json.dumps(plan, indent=2)

    @mcp.resource("defernowork://tasks/mood-history")
    async def mood_history_resource() -> str:
        """Mood history for finished tasks (JSON array)."""
        async with _get_client() as client:
            history = await client.mood_history()
        return json.dumps(history, indent=2)

    @mcp.resource("defernowork://task/{task_id}")
    async def task_resource(task_id: str) -> str:
        """A single task, addressable by UUID as ``defernowork://task/<id>``."""
        async with _get_client() as client:
            task = await client.get_task(unquote(task_id))
        return json.dumps(task, indent=2)

    return mcp


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

    from starlette.types import ASGIApp, Receive, Scope, Send

    class _BearerAuthMiddleware:
        """Pure-ASGI middleware: extracts Bearer token → _request_token contextvar."""

        def __init__(self, app: ASGIApp) -> None:
            self.app = app

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http":
                headers: dict[bytes, bytes] = dict(scope.get("headers", []))
                auth = headers.get(b"authorization", b"").decode()
                token = auth.removeprefix("Bearer ").strip() or None
                tok = _request_token.set(token)
                try:
                    await self.app(scope, receive, send)
                finally:
                    _request_token.reset(tok)
            else:
                await self.app(scope, receive, send)

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

    app = _BearerAuthMiddleware(mcp_asgi)
    uvicorn.run(app, host=host, port=port, log_level=log_level)
