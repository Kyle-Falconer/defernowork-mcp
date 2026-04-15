"""Daily plan tools: get, add, remove, reorder."""

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
    async def get_daily_plan(date: str | None = None, ctx: Context = None) -> str:
        """Return today's curated daily plan.

        The plan auto-seeds from recurring tasks and carries forward
        incomplete items from yesterday. Done tasks stay in the plan.
        ``date`` is optional (YYYY-MM-DD); defaults to today.
        Prefer this over ``list_tasks`` when the user asks what
        they should work on today.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                plan = await client.get_daily_plan(date)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(plan)

    @mcp.tool()
    async def add_to_plan(task_id: str, date: str | None = None, ctx: Context = None) -> str:
        """Add a task to the daily plan.

        ``task_id`` is the UUID of an existing task. ``date`` defaults to today.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                await client.add_to_plan(task_id, date)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps({"added": True, "task_id": task_id})

    @mcp.tool()
    async def remove_from_plan(task_id: str, date: str | None = None, ctx: Context = None) -> str:
        """Remove a task from the daily plan.

        ``task_id`` is the UUID of the task to remove. ``date`` defaults to today.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                await client.remove_from_plan(task_id, date)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps({"removed": True, "task_id": task_id})

    @mcp.tool()
    async def reorder_plan(task_ids: list[str], date: str | None = None, ctx: Context = None) -> str:
        """Replace the daily plan ordering with the given task ID list.

        ``task_ids`` is the full ordered list of task UUIDs for the plan.
        ``date`` defaults to today.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                await client.reorder_plan(task_ids, date)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps({"reordered": True, "count": len(task_ids)})

    @mcp.tool()
    async def get_calendar_events(start: str, end: str, ctx: Context = None) -> str:
        """Query calendar events for a date range.

        Returns recurring task instances (expanded from RRULE schedules)
        plus one-off tasks with due dates in the range.
        ``start`` and ``end`` are YYYY-MM-DD strings.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                events = await client.get_calendar_events(start, end)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(events)
