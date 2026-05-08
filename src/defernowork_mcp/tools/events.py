"""Event CRUD tools."""

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
    async def create_event(
        title: str,
        complete_by: str,
        end_time: str | None = unset,
        description: str | None = unset,
        labels: list[str] | None = unset,
        parent_id: str | None = unset,
        recurrence: dict[str, Any] | None = unset,
        ctx: Context = None,
    ) -> str:
        """Create a time-bound event.

        ``complete_by`` is the start time (ISO-8601). ``end_time`` (if
        provided) must be at or after ``complete_by``.
        """
        payload = compact({
            "title": title,
            "complete_by": complete_by,
            "end_time": end_time,
            "description": description,
            "labels": labels,
            "parent_id": parent_id,
            "recurrence": recurrence,
        })
        async with (await get_client(ctx=ctx)) as client:
            try:
                event = await client.create_event(payload)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(event)

    @mcp.tool()
    async def update_event(
        event_id: str,
        title: str | None = unset,
        complete_by: str | None = unset,
        end_time: str | None = unset,
        description: str | None = unset,
        labels: list[str] | None = unset,
        recurrence: dict[str, Any] | None = unset,
        ctx: Context = None,
    ) -> str:
        """Patch mutable fields on an event. Backend rejects ``end_time`` < ``complete_by``."""
        payload = compact({
            "title": title,
            "complete_by": complete_by,
            "end_time": end_time,
            "description": description,
            "labels": labels,
            "recurrence": recurrence,
        })
        async with (await get_client(ctx=ctx)) as client:
            try:
                event = await client.update_event(event_id, payload)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(event)

    @mcp.tool()
    async def delete_event(event_id: str, ctx: Context = None) -> str:
        """Archive (soft-delete) an event."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                await client.delete_event(event_id)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps({"deleted": True, "event_id": event_id})
