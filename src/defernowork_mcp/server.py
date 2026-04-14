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
from .credentials import load_credentials, save_credentials, clear_credentials

__all__ = ["create_server", "main", "main_http", "DefernoClient"]

logger = logging.getLogger("defernowork-mcp")

# Per-request Bearer token injected by the HTTP auth middleware.
# Falls back to DEFERNO_TOKEN env var for stdio transport.
_request_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "deferno_request_token", default=None
)


def _get_client() -> DefernoClient:
    """Return a DefernoClient for the current request/session.

    Token resolution order:
    1. Per-request Bearer header (HTTP transport)
    2. ``DEFERNO_TOKEN`` env var
    3. Saved credentials on disk (``~/.config/defernowork/credentials.json``)
    """
    base_url = os.environ.get("DEFERNO_BASE_URL", "http://127.0.0.1:3000")
    token = _request_token.get() or os.environ.get("DEFERNO_TOKEN")
    if token is None:
        creds = load_credentials()
        if creds:
            token = creds.get("token")
            if not os.environ.get("DEFERNO_BASE_URL"):
                base_url = creds.get("base_url", base_url)
    return DefernoClient(base_url=base_url, token=token)


def _get_anon_client() -> DefernoClient:
    """Return an unauthenticated DefernoClient (for auth init/verify)."""
    base_url = os.environ.get("DEFERNO_BASE_URL", "http://127.0.0.1:3000")
    creds = load_credentials()
    if creds and not os.environ.get("DEFERNO_BASE_URL"):
        base_url = creds.get("base_url", base_url)
    return DefernoClient(base_url=base_url)


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop None-valued keys so PATCH/POST bodies stay minimal."""
    return {k: v for k, v in payload.items() if v is not None}


def _format_error(exc: DefernoError) -> str:
    return f"Deferno API error {exc.status_code}: {exc.message}"


def create_server(http_transport: bool = False) -> FastMCP:  # noqa: C901
    # In mcp >= ~1.23, FastMCP auto-enables DNS-rebinding protection when its
    # default host is 127.0.0.1, accepting only localhost:* / 127.0.0.1:* as
    # Host headers.  When running behind nginx the proxy forwards the external
    # hostname (e.g. "deferno.work"), which the SDK rejects with 421.
    #
    # We explicitly allow the external host (read from MCP_ALLOWED_HOSTS, a
    # comma-separated list) so the server works correctly in production.
    # The container is NOT directly internet-accessible; it lives behind the
    # Docker network and nginx, so relaxing this check is safe.
    security_kwargs: dict = {}
    try:
        from mcp.server.transport_security import TransportSecuritySettings

        raw = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
        allowed_hosts = [h.strip() for h in raw.split(",") if h.strip()] if raw else []
        # Always include the standard loopback aliases so local/stdio usage
        # continues to work without any env-var configuration.
        for default in ("localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"):
            if default not in allowed_hosts:
                allowed_hosts.append(default)

        security_kwargs["transport_security"] = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
        )
    except ImportError:
        # Older SDK versions don't have TransportSecuritySettings — skip.
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

    # ------------------------------------------------------------------ auth
    @mcp.tool()
    async def start_auth() -> str:
        """Begin the Deferno authentication flow.

        Returns a URL for the user to open in their browser and a
        ``session_id`` needed by ``complete_auth``.  After the user
        signs in, they will see a short code to paste back here.
        """
        async with _get_anon_client() as client:
            try:
                result = await client.cli_init()
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps({
            "auth_url": result["auth_url"],
            "session_id": result["session_id"],
            "instructions": (
                "Show the auth_url to the user and ask them to open it "
                "in their browser. After they sign in, they will see a "
                "short code. Ask the user to paste that code, then call "
                "complete_auth with the session_id and code."
            ),
        })

    @mcp.tool()
    async def complete_auth(session_id: str, code: str) -> str:
        """Finish authentication by exchanging the browser code for a token.

        ``session_id`` comes from the ``start_auth`` response.
        ``code`` is the short code the user copied from their browser
        after signing in.  Saves credentials to disk so future
        sessions authenticate automatically.
        """
        async with _get_anon_client() as client:
            try:
                result = await client.cli_verify(session_id, code)
            except DefernoError as exc:
                return _format_error(exc)
        token = result["token"]
        user = result.get("user", {})
        username = user.get("username", "")
        base_url = client.base_url
        save_credentials(token, username, base_url)
        return json.dumps({"authenticated": True, "username": username})

    @mcp.tool()
    async def logout() -> str:
        """Log out and remove saved credentials."""
        async with _get_client() as client:
            try:
                await client.logout()
            except DefernoError as exc:
                # Still clear local credentials even if the server call fails
                clear_credentials()
                return _format_error(exc)
        clear_credentials()
        return "Logged out and credentials removed."

    @mcp.tool()
    async def whoami() -> str:
        """Return the currently authenticated Deferno user.

        Call this first to confirm that the Authorization header is valid
        before issuing task operations.
        """
        async with _get_client() as client:
            try:
                result = await client.whoami()
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps(result)

    # ------------------------------------------------------------------ tasks
    @mcp.tool()
    async def list_tasks() -> str:
        """List every task owned by the authenticated user.

        Returns a JSON array of task objects. Use ``get_task`` for full
        detail on a specific task by id.
        """
        async with _get_client() as client:
            try:
                tasks = await client.list_tasks()
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps(tasks)

    @mcp.tool()
    async def get_task(task_id: str) -> str:
        """Fetch a single task by id (UUID)."""
        async with _get_client() as client:
            try:
                task = await client.get_task(task_id)
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps(task)

    @mcp.tool()
    async def create_task(
        title: str,
        description: str,
        labels: list[str] | None = None,
        parent_id: str | None = None,
        assignee: str | None = None,
        complete_by: str | None = None,
        productive: float | None = None,
        desire: float | None = None,
        recurrence: dict[str, Any] | None = None,
    ) -> str:
        """Create a new task.

        ``complete_by`` must be an ISO-8601 UTC timestamp.
        ``parent_id`` attaches the new task as a child of an existing task.
        ``productive`` and ``desire`` are floats in [0, 1] representing how
        productive this task feels and how much the user wants to do it.
        ``recurrence`` sets a repeat schedule. Use ``{"type": "daily"}``,
        ``{"type": "every_n_days", "n": 3}``, or
        ``{"type": "weekly", "days": ["Mon", "Wed", "Fri"]}``.
        """
        payload = _compact(
            {
                "title": title,
                "description": description,
                "labels": labels,
                "parent_id": parent_id,
                "assignee": assignee,
                "complete_by": complete_by,
                "productive": productive,
                "desire": desire,
                "recurrence": recurrence,
            }
        )
        async with _get_client() as client:
            try:
                task = await client.create_task(payload)
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps(task)

    @mcp.tool()
    async def update_task(
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        labels: list[str] | None = None,
        assignee: str | None = None,
        complete_by: str | None = None,
        productive: float | None = None,
        desire: float | None = None,
        recurrence: dict[str, Any] | None = None,
    ) -> str:
        """Patch mutable fields on a task.

        ``status`` must be one of ``open``, ``in-progress``, ``done``,
        ``dropped``, ``pruned``. The backend rejects completing a task
        while any of its children are still active.
        ``recurrence`` sets or clears a repeat schedule (see ``create_task``).
        """
        payload = _compact(
            {
                "title": title,
                "description": description,
                "status": status,
                "labels": labels,
                "assignee": assignee,
                "complete_by": complete_by,
                "productive": productive,
                "desire": desire,
                "recurrence": recurrence,
            }
        )
        async with _get_client() as client:
            try:
                task = await client.update_task(task_id, payload)
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps(task)

    @mcp.tool()
    async def set_task_status(task_id: str, status: str) -> str:
        """Convenience wrapper around ``update_task`` for status changes.

        Accepts ``open``, ``in-progress``, ``done``, ``dropped``, ``pruned``.
        """
        async with _get_client() as client:
            try:
                task = await client.update_task(task_id, {"status": status})
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps(task)

    @mcp.tool()
    async def split_task(
        task_id: str,
        first_title: str,
        first_description: str,
        second_title: str,
        second_description: str,
    ) -> str:
        """Decompose a task into two child tasks while preserving the parent.

        Returns the updated parent and both new children.
        """
        payload = {
            "first_title": first_title,
            "first_description": first_description,
            "second_title": second_title,
            "second_description": second_description,
        }
        async with _get_client() as client:
            try:
                result = await client.split_task(task_id, payload)
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps(result)

    @mcp.tool()
    async def fold_task(
        task_id: str,
        title: str,
        description: str,
        labels: list[str] | None = None,
        desire: float | None = None,
        productive: float | None = None,
        complete_by: str | None = None,
    ) -> str:
        """Insert a new next-step task directly after ``task_id`` in the sequence.

        Preserves any existing downstream chain. Returns the original task
        and the newly created next task.
        """
        payload = _compact(
            {
                "title": title,
                "description": description,
                "labels": labels,
                "desire": desire,
                "productive": productive,
                "complete_by": complete_by,
            }
        )
        async with _get_client() as client:
            try:
                result = await client.fold_task(task_id, payload)
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps(result)

    @mcp.tool()
    async def merge_task(task_id: str) -> str:
        """Roll the active children of a task back into the parent.

        Child content is appended to the parent description; the children are
        marked as ``pruned`` but remain recoverable. Pass the id of any
        child whose parent should receive the merge.
        """
        async with _get_client() as client:
            try:
                result = await client.merge_task(task_id)
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps(result)

    @mcp.tool()
    async def get_daily_tasks() -> str:
        """Return today's curated daily plan.

        This is an alias for ``get_daily_plan`` — prefer that tool instead.
        """
        async with _get_client() as client:
            try:
                plan = await client.get_daily_plan()
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps(plan)

    # -------------------------------------------------------------- daily plan
    @mcp.tool()
    async def get_daily_plan(date: str | None = None) -> str:
        """Return today's curated daily plan.

        The plan auto-seeds from recurring tasks and carries forward
        incomplete items from yesterday. Done tasks stay in the plan.
        ``date`` is optional (YYYY-MM-DD); defaults to today.
        Prefer this over ``get_daily_tasks`` when the user asks what
        they should work on today.
        """
        async with _get_client() as client:
            try:
                plan = await client.get_daily_plan(date)
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps(plan)

    @mcp.tool()
    async def add_to_plan(task_id: str, date: str | None = None) -> str:
        """Add a task to the daily plan.

        ``task_id`` is the UUID of an existing task. ``date`` defaults to today.
        """
        async with _get_client() as client:
            try:
                await client.add_to_plan(task_id, date)
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps({"added": True, "task_id": task_id})

    @mcp.tool()
    async def remove_from_plan(task_id: str, date: str | None = None) -> str:
        """Remove a task from the daily plan.

        ``task_id`` is the UUID of the task to remove. ``date`` defaults to today.
        """
        async with _get_client() as client:
            try:
                await client.remove_from_plan(task_id, date)
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps({"removed": True, "task_id": task_id})

    @mcp.tool()
    async def get_mood_history() -> str:
        """Return the user's historical mood-per-task log for finished tasks."""
        async with _get_client() as client:
            try:
                history = await client.mood_history()
            except DefernoError as exc:
                return _format_error(exc)
        return json.dumps(history)

    # -------------------------------------------------------------- resources
    @mcp.resource("defernowork://tasks")
    async def all_tasks_resource() -> str:
        """All tasks owned by the authenticated user (JSON array)."""
        async with _get_client() as client:
            tasks = await client.list_tasks()
        return json.dumps(tasks, indent=2)

    @mcp.resource("defernowork://tasks/today")
    async def today_resource() -> str:
        """Today's curated daily plan (JSON array)."""
        async with _get_client() as client:
            plan = await client.get_daily_plan()
        return json.dumps(plan, indent=2)

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
    """Entry point for remote HTTP/SSE transport.

    Clients must pass ``Authorization: Bearer <deferno-token>`` with every
    request. The token is extracted by :class:`_BearerAuthMiddleware` and
    stored in :data:`_request_token` so tool handlers can create a
    per-request :class:`~defernowork_mcp.client.DefernoClient`.
    """
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

    # Prefer streamable HTTP (MCP 2024-11-05 spec); fall back to SSE.
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
