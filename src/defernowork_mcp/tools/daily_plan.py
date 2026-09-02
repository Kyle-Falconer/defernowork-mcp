"""Daily plan tool: today's curated, server-seeded plan view.

Plan mutations and the calendar view are the kind-neutral item tools
(``add_to_items_plan`` / ``remove_from_items_plan`` / ``reorder_items_plan`` /
``get_items_calendar``): ``/items/plan/*`` and ``/items/calendar`` are the SAME
backend handlers as the retired ``/tasks/plan/*`` and ``/tasks/calendar`` ones,
so the task-scoped duplicates were dropped. ``get_daily_plan`` stays — it is the
seeded today-read (auto-seeded server-side on the read path, independent of the
plan-mutation endpoints).

It reads ``GET /items/plan``, the kind-polymorphic feed. The task-scoped
``/tasks/plan`` it used to read runs the same seeder but then loads the plan's
id list through a Tasks-only lookup and drops the misses, so it silently
withheld every Habit, Chore, and Event on the plan.
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable

from mcp.server.mcpserver import Context, MCPServer

from ..client import DefernoClient, DefernoError


def register(
    mcp: MCPServer,
    get_client: Callable[..., Awaitable[DefernoClient]],
    format_error: Callable[[DefernoError], str],
) -> None:
    @mcp.tool()
    async def get_daily_plan(
        date: str | None = None,
        tz: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Return today's curated daily plan, as a kind-tagged JSON array.

        The plan auto-seeds from recurring items, carries forward unresolved
        items from yesterday, and includes any item of any kind with a due
        date falling on the target date in the user's timezone.

        Every row carries a ``kind`` discriminant — ``task`` | ``habit`` |
        ``chore`` | ``event`` — so read the row's kind before its fields; the
        four are not interchangeable.

        The three recurring kinds also carry ``today_occurrence``, the state
        of THIS DATE specifically. **Read done-ness from
        ``today_occurrence.status``, never from the row's own ``status``.** A
        recurring item's status is ``active``/``archived`` and can never be
        ``done``, and its ``complete_by`` jumps to the NEXT occurrence as soon
        as today is resolved — so both describe the series, not today.
        ``today_occurrence.status`` is one of:

        - ``scheduled`` / ``in_progress`` — open, still to do today
        - ``done_on_time`` / ``done_late`` — done today
        - ``dropped`` — deliberately refused for today; resolved, but NOT done

        A ``task`` row has no ``today_occurrence``: a Task is a single
        undated-or-once-dated item, so its own ``status`` is the answer.

        Parameters
        ----------
        date : optional YYYY-MM-DD. Defaults to today *in the user's
            timezone*. If no timezone is known, defaults to UTC.
        tz : optional IANA timezone (e.g. "America/Los_Angeles"). Supply
            if you know the user's local timezone — Claude Desktop /
            Claude Code typically have this in the system prompt as
            locale info. Once supplied for the first time, the backend
            persists it as the user's preference, so future calls don't
            need to repeat it.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                plan = await client.get_daily_plan(date, tz=tz)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(plan)
