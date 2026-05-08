"""Feedback admin tools (list / stats / patch).

Feedback creation requires multipart/form-data attachments and is therefore
not exposed in the MCP — users submit feedback via the web UI directly.
Attachment downloads return raw binary, also not exposed.
"""

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
    async def list_feedback(ctx: Context = None) -> str:
        """List submitted feedback (admin only)."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                items = await client.list_feedback()
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(items)

    @mcp.tool()
    async def feedback_stats(ctx: Context = None) -> str:
        """Return aggregate feedback statistics (admin only)."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                stats = await client.feedback_stats()
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(stats)

    @mcp.tool()
    async def update_feedback(
        feedback_id: str,
        status: str,
        admin_notes: str | None = unset,
        ctx: Context = None,
    ) -> str:
        """Update a feedback item's status / admin notes (admin only)."""
        payload = compact({"status": status, "admin_notes": admin_notes})
        async with (await get_client(ctx=ctx)) as client:
            try:
                item = await client.update_feedback(feedback_id, payload)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(item)
