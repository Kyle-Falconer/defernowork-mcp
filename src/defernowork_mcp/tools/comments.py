"""Comment mutation tools (creator-only)."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import Context, FastMCP

from ..client import DefernoClient, DefernoError


def register(
    mcp: FastMCP,
    get_client: Callable[..., Awaitable[DefernoClient]],
    format_error: Callable[[DefernoError], str],
    compact: Callable[[dict[str, Any]], dict[str, Any]],
    unset: object,
) -> None:
    @mcp.tool()
    async def update_comment(
        comment_id: str,
        body: str | None = unset,
        is_private: bool | None = unset,
        ctx: Context = None,
    ) -> str:
        """Patch a comment's body or visibility.

        Empty payload (no fields supplied) → backend returns 422.
        """
        payload = compact({"body": body, "is_private": is_private})
        async with (await get_client(ctx=ctx)) as client:
            try:
                comment = await client.update_comment(comment_id, payload)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(comment)

    @mcp.tool()
    async def delete_comment(comment_id: str, ctx: Context = None) -> str:
        """Delete a comment owned by the caller."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                await client.delete_comment(comment_id)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps({"deleted": True, "comment_id": comment_id})
