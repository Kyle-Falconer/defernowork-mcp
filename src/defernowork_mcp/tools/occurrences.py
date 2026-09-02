"""Kind-neutral occurrence tools (fix #3b).

The chore/habit/event occurrence families were near-isomorphic, so they collapse
into three tools that resolve the item's kind from the ref and dispatch to the
per-kind backend call: ``list_occurrences``, ``set_occurrence_status``,
``reschedule_occurrence``. The chore-specific "mark the next one done" case
(targeting the *earliest unresolved* occurrence, not a ref+date) folds into
``set_occurrence_status`` as a dateless next-mode (omit ``date``).

Status is normalised to one enum -- ``in_progress`` / ``done`` / ``dropped`` --
mapped client-side to each kind's native operation:

- **Chore / Event** support all three natively (chore PUT status, event POST
  action). ``dropped`` records the occurrence as skipped (the row is kept).
- **Habit** occurrences are done-or-not, so ``done`` marks done, ``dropped``
  marks explicitly not-done, and ``in_progress`` is a **no-op**.

The old standalone clear/undo ops fold into ``dropped`` -- but only the **Habit**
case is exact (clearing and marking-not-done both reset the occurrence to
unrecorded). For an **Event**, ``dropped`` records a terminal *Dropped* status
(and sweeps subtasks); it is NOT the old ``delete_event_occurrence`` row-erase (a
clean revert to *Scheduled*). That revert is intentionally no longer exposed --
re-mark the occurrence to change it.
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable

from mcp.server.mcpserver import Context, MCPServer

from ..client import DefernoClient, DefernoError
from ..refs import resolve_ref_with_kind

_OCC_KINDS = ("chore", "habit", "event")
_OCC_STATUS = ("in_progress", "done", "dropped")


def register(
    mcp: MCPServer,
    get_client: Callable[..., Awaitable[DefernoClient]],
    format_error: Callable[[DefernoError], str],
) -> None:
    @mcp.tool()
    async def list_occurrences(
        ref: str,
        from_date: str | None = None,
        to_date: str | None = None,
        ctx: Context = None,
    ) -> str:
        """List derived occurrences for a recurring item (Chore / Habit / Event).

        ``ref`` is any item ref — see the server instructions; the item's kind is
        resolved from it and the call dispatches to the matching backend.
        ``from_date`` / ``to_date`` are
        ``YYYY-MM-DD`` (inclusive). Returns the unified-Occurrence shape (id,
        scheduled_date, status, ...).
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                uuid, kind = await resolve_ref_with_kind(client, ref)
                if kind == "chore":
                    occs = await client.list_chore_occurrences(
                        uuid, from_date=from_date, to_date=to_date
                    )
                elif kind == "habit":
                    occs = await client.list_habit_occurrences(
                        uuid, from_date=from_date, to_date=to_date
                    )
                elif kind == "event":
                    occs = await client.list_event_occurrences(uuid, from_date, to_date)
                else:
                    return (
                        f"list_occurrences: a {kind} has no occurrences "
                        "(only Chore / Habit / Event do)"
                    )
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(occs)

    @mcp.tool()
    async def set_occurrence_status(
        ref: str,
        date: str | None = None,
        status: str | None = None,
        cascade_subtasks: bool = False,
        ctx: Context = None,
    ) -> str:
        """Set a single occurrence's status for a Chore / Habit / Event.

        ``ref`` is any item ref (see server instructions); ``date`` is the
        ``YYYY-MM-DD`` occurrence date. ``status`` is one of ``in_progress`` /
        ``done`` / ``dropped`` (``dropped`` records the occurrence as skipped —
        the row is kept).

        Omit ``date`` for the **dateless next-mode** (Chore only): ``status`` is
        applied to the chore's *earliest unresolved* occurrence — the common "I
        just did the dishes, don't make me look up which date is overdue" case.
        Habit/Event require an explicit ``date``.

        Per-kind: a **Habit** occurrence is done-or-not, so ``done`` marks it
        done, ``dropped`` marks it explicitly not-done, and ``in_progress`` is a
        **no-op**. ``cascade_subtasks`` applies to **Events** only — when the
        occurrence has materialized subtasks, pass ``true`` to sweep them to the
        matching terminal status (else a non-terminal subtask yields a 409); it
        is ignored for Chore/Habit.
        """
        if status not in _OCC_STATUS:
            return (
                f"set_occurrence_status: status must be one of "
                f"{', '.join(_OCC_STATUS)}"
            )
        async with (await get_client(ctx=ctx)) as client:
            try:
                uuid, kind = await resolve_ref_with_kind(client, ref)
                if date is None:
                    if kind == "chore":
                        occ = await client.mark_next_chore_done(uuid, status=status)
                    else:
                        return (
                            "set_occurrence_status: dateless next-mode (no date) "
                            "is supported only for Chore; pass a date for "
                            "Habit/Event"
                        )
                elif kind == "chore":
                    occ = await client.set_chore_occurrence_status(uuid, date, status)
                elif kind == "event":
                    occ = await client.set_event_occurrence(
                        uuid, date, status, cascade_subtasks
                    )
                elif kind == "habit":
                    if status == "in_progress":
                        return json.dumps(
                            {
                                "ok": True,
                                "note": "in_progress is a no-op for a Habit "
                                "(occurrences are done-or-not)",
                            }
                        )
                    occ = await client.mark_habit_occurrence(
                        uuid, status == "done", date=date
                    )
                else:
                    return (
                        f"set_occurrence_status: a {kind} has no occurrences "
                        "(only Chore / Habit / Event do)"
                    )
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(occ)

    @mcp.tool()
    async def reschedule_occurrence(
        ref: str,
        date: str,
        new_date: str,
        ctx: Context = None,
    ) -> str:
        """Move a single occurrence to ``new_date`` without touching the cadence.

        ``ref`` is any item ref (see server instructions); ``date`` and
        ``new_date`` are ``YYYY-MM-DD`` occurrence dates (not item references).
        Works for Chore / Habit / Event. NOTE (v0.2): Chore and Habit reschedule
        return 501 today (legacy storage) and are exposed for forward
        compatibility; Event reschedule is live.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                uuid, kind = await resolve_ref_with_kind(client, ref)
                if kind == "chore":
                    occ = await client.reschedule_chore_occurrence(uuid, date, new_date)
                elif kind == "habit":
                    occ = await client.reschedule_habit_occurrence(uuid, date, new_date)
                elif kind == "event":
                    occ = await client.reschedule_event_occurrence(uuid, date, new_date)
                else:
                    return (
                        f"reschedule_occurrence: a {kind} has no occurrences "
                        "(only Chore / Habit / Event do)"
                    )
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(occ)
