"""Habit CRUD + occurrence-tracking tools."""

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
    async def create_habit(
        title: str,
        description: str | None = unset,
        complete_by: str | None = unset,
        recurrence: dict[str, Any] | None = unset,
        parent_id: str | None = unset,
        labels: list[str] | None = unset,
        ctx: Context = None,
    ) -> str:
        """Create a recurring habit that resets each period.

        Habits differ from chores in that an unfinished occurrence does not
        carry forward — each period gets a fresh start.
        ``recurrence`` follows the same shape as Task: ``{"type": "daily"}``,
        ``{"type": "every_n_days", "n": 3}``, or
        ``{"type": "weekly", "days": ["Mon", "Wed"]}``.
        """
        payload = compact({
            "title": title,
            "description": description,
            "complete_by": complete_by,
            "recurrence": recurrence,
            "parent_id": parent_id,
            "labels": labels,
        })
        async with (await get_client(ctx=ctx)) as client:
            try:
                habit = await client.create_habit(payload)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(habit)

    @mcp.tool()
    async def update_habit(
        habit_id: str,
        title: str | None = unset,
        description: str | None = unset,
        complete_by: str | None = unset,
        recurrence: dict[str, Any] | None = unset,
        labels: list[str] | None = unset,
        ctx: Context = None,
    ) -> str:
        """Patch mutable fields on a habit. Omitted fields stay untouched."""
        payload = compact({
            "title": title,
            "description": description,
            "complete_by": complete_by,
            "recurrence": recurrence,
            "labels": labels,
        })
        async with (await get_client(ctx=ctx)) as client:
            try:
                habit = await client.update_habit(habit_id, payload)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(habit)

    @mcp.tool()
    async def delete_habit(habit_id: str, ctx: Context = None) -> str:
        """Archive (soft-delete) a habit."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                await client.delete_habit(habit_id)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps({"deleted": True, "habit_id": habit_id})

    @mcp.tool()
    async def list_habit_occurrences(
        habit_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
        ctx: Context = None,
    ) -> str:
        """List occurrences for a habit in a date window.

        Dates use YYYY-MM-DD; range is inclusive on both ends.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                occurrences = await client.list_habit_occurrences(
                    habit_id, from_date=from_date, to_date=to_date
                )
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(occurrences)

    @mcp.tool()
    async def mark_habit_occurrence(
        habit_id: str,
        done: bool,
        date: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Mark a habit occurrence as done or not-done.

        ``date`` is YYYY-MM-DD; defaults to today on the server side.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                occurrence = await client.mark_habit_occurrence(
                    habit_id, done, date=date
                )
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(occurrence)

    @mcp.tool()
    async def clear_habit_occurrence(
        habit_id: str,
        date: str,
        ctx: Context = None,
    ) -> str:
        """Clear an explicitly-marked habit occurrence at ``date`` (YYYY-MM-DD)."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                await client.clear_habit_occurrence(habit_id, date)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps({"cleared": True, "habit_id": habit_id, "date": date})
