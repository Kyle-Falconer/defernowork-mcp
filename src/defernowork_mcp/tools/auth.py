"""Authentication tools: start_auth, complete_auth, logout, whoami."""

from __future__ import annotations

import json
from typing import Any, Callable, Awaitable

from mcp.server.fastmcp import Context, FastMCP

from ..client import DefernoClient, DefernoError
from .. import server as _server_mod
from ..credentials import save_credentials, clear_credentials


def register(
    mcp: FastMCP,
    get_client: Callable[..., Awaitable[DefernoClient]],
    get_anon_client: Callable[[], DefernoClient],
    format_error: Callable[[DefernoError], str],
) -> None:
    @mcp.tool()
    async def start_auth() -> str:
        """Begin the Deferno authentication flow.

        Returns a URL for the user to open in their browser.
        The user authenticates via Kanidm,
        then sees a short code to paste back here.

        NOTE: In HTTP transport with OAuth enabled, authentication is
        handled automatically by the transport layer. This tool is only
        needed for stdio/CLI transport.
        """
        if _server_mod._http_transport_mode and _server_mod._oauth_provider is not None:
            return json.dumps({
                "message": (
                    "Authentication is handled automatically via OAuth 2.0. "
                    "No action needed — the transport layer manages tokens."
                ),
            })
        async with get_anon_client() as client:
            try:
                result = await client.cli_init()
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps({
            "auth_url": result["auth_url"],
            "session_id": result["session_id"],
            "instructions": (
                "Show the auth_url to the user and ask them to open it "
                "in their browser. They will authenticate via Kanidm "
                "(password, passkey, or MFA). After approving, they will "
                "see a short code. Ask them to paste that code, then call "
                "complete_auth with the session_id and code."
            ),
        })

    @mcp.tool()
    async def complete_auth(session_id: str, code: str) -> str:
        """Finish authentication by exchanging the browser code for a token.

        ``session_id`` comes from the ``start_auth`` response.
        ``code`` is the short code the user copied from their browser
        after signing in via Kanidm.

        NOTE: In HTTP transport with OAuth enabled, authentication is
        handled automatically. This tool is only needed for stdio/CLI.
        """
        if _server_mod._http_transport_mode and _server_mod._oauth_provider is not None:
            return json.dumps({
                "message": (
                    "Authentication is handled automatically via OAuth 2.0. "
                    "No action needed."
                ),
            })
        async with get_anon_client() as client:
            try:
                result = await client.cli_verify(session_id, code)
            except DefernoError as exc:
                return format_error(exc)
        token = result["token"]
        user = result.get("user", {})
        username = user.get("username", "")
        base_url = client.base_url
        save_credentials(token, username, base_url)
        return json.dumps({"authenticated": True, "username": username})

    @mcp.tool()
    async def logout(ctx: Context = None) -> str:
        """Log out and remove saved credentials."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                await client.logout()
            except DefernoError as exc:
                clear_credentials()
                return format_error(exc)
        clear_credentials()
        return "Logged out and credentials removed."

    @mcp.tool()
    async def whoami(ctx: Context = None) -> str:
        """Return the currently authenticated Deferno user.

        Call this first to confirm that the Authorization header is valid
        before issuing task operations.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                result = await client.whoami()
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)
