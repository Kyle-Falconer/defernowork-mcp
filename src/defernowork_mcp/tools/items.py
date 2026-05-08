"""Cross-kind item tools: calendar + plan over Tasks/Habits/Chores/Events."""

from __future__ import annotations

import json
from typing import Awaitable, Callable

from mcp.server.fastmcp import Context, FastMCP

from ..client import DefernoClient, DefernoError


def register(
    mcp: FastMCP,
    get_client: Callable[..., Awaitable[DefernoClient]],
    format_error: Callable[[DefernoError], str],
) -> None:
    @mcp.tool()
    async def get_items_calendar(
        start: str,
        end: str,
        tz: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Calendar view across all item kinds (Task, Habit, Chore, Event).

        ``start`` and ``end`` are YYYY-MM-DD; ``end`` is exclusive.
        ``tz`` is an optional IANA timezone for local-midnight alignment.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                items = await client.get_items_calendar(start, end, tz=tz)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(items)

    @mcp.tool()
    async def get_items_plan(
        date: str | None = None,
        tz: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Daily plan across all item kinds (Task, Habit, Chore, Event).

        Returns a polymorphic array — each entry has a ``kind`` discriminator.
        ``date`` defaults to today; ``tz`` is an optional IANA timezone.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                items = await client.get_items_plan(date=date, tz=tz)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(items)

    @mcp.tool()
    async def add_to_items_plan(
        task_id: str,
        date: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Add an item (any kind) to the daily plan."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                result = await client.add_to_items_plan(task_id, date=date)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)

    @mcp.tool()
    async def remove_from_items_plan(
        task_id: str,
        date: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Remove an item from the daily plan."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                result = await client.remove_from_items_plan(task_id, date=date)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)

    @mcp.tool()
    async def reorder_items_plan(
        task_ids: list[str],
        date: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Replace the daily plan ordering with the given full list of IDs."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                result = await client.reorder_items_plan(task_ids, date=date)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)
