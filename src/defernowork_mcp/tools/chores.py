"""Chore CRUD + occurrence-tracking tools."""

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
    async def create_chore(
        title: str,
        description: str | None = unset,
        complete_by: str | None = unset,
        recurrence: dict[str, Any] | None = unset,
        parent_id: str | None = unset,
        labels: list[str] | None = unset,
        ctx: Context = None,
    ) -> str:
        """Create a recurring chore that lingers until done.

        Chores differ from habits in that an unfinished occurrence carries
        forward (Missed/InProgress) rather than resetting each period.
        ``complete_by`` should be the first scheduled date as ISO-8601.
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
                chore = await client.create_chore(payload)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(chore)

    @mcp.tool()
    async def update_chore(
        chore_id: str,
        title: str | None = unset,
        description: str | None = unset,
        complete_by: str | None = unset,
        recurrence: dict[str, Any] | None = unset,
        labels: list[str] | None = unset,
        ctx: Context = None,
    ) -> str:
        """Patch mutable fields on a chore. Omitted fields stay untouched.

        ``complete_by`` cannot be cleared on chores. Pass new value to shift
        the schedule. Updating ``recurrence`` rotates the chore's series ID
        so prior occurrences remain attached to the old definition.
        """
        payload = compact({
            "title": title,
            "description": description,
            "complete_by": complete_by,
            "recurrence": recurrence,
            "labels": labels,
        })
        async with (await get_client(ctx=ctx)) as client:
            try:
                chore = await client.update_chore(chore_id, payload)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(chore)

    @mcp.tool()
    async def delete_chore(chore_id: str, ctx: Context = None) -> str:
        """Archive (soft-delete) a chore."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                await client.delete_chore(chore_id)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps({"deleted": True, "chore_id": chore_id})

    @mcp.tool()
    async def list_chore_occurrences(
        chore_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
        ctx: Context = None,
    ) -> str:
        """List derived occurrences for a chore in the given date window.

        Each occurrence has a status: ``Scheduled``, ``Missed``,
        ``InProgress``, ``Skipped``, ``DoneOnTime``, or ``DoneLate``.
        Dates use YYYY-MM-DD; range is inclusive on both ends.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                occurrences = await client.list_chore_occurrences(
                    chore_id, from_date=from_date, to_date=to_date
                )
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(occurrences)

    @mcp.tool()
    async def set_chore_occurrence_status(
        chore_id: str,
        date: str,
        status: str,
        ctx: Context = None,
    ) -> str:
        """Set the status of a single chore occurrence.

        ``status`` must be one of ``"in_progress"``, ``"done"``, or
        ``"skipped"``. ``date`` is YYYY-MM-DD.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                occurrence = await client.set_chore_occurrence_status(
                    chore_id, date, status
                )
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(occurrence)

    @mcp.tool()
    async def mark_next_chore_done(
        chore_id: str,
        status: str = "done",
        ctx: Context = None,
    ) -> str:
        """Apply ``status`` to the earliest unresolved occurrence of a chore.

        Useful for the common "I just did the dishes" case where the user
        doesn't want to look up which date is overdue. 404 if no
        unresolved occurrence exists.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                occurrence = await client.mark_next_chore_done(chore_id, status=status)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(occurrence)
