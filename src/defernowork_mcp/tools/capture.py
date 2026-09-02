"""The behavioral-capture tool -- the single create front door (ADR-0003)."""

from __future__ import annotations

import json
from typing import Annotated, Any, Awaitable, Callable, Literal

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from ..capture import CaptureError, derive_create_payload
from ..client import DefernoClient, DefernoError
from ..constraints import RECURRENCE_END_DESC


def register(
    mcp: MCPServer,
    get_client: Callable[..., Awaitable[DefernoClient]],
    format_error: Callable[[DefernoError], str],
) -> None:
    @mcp.tool()
    async def capture_item(
        title: str,
        attend: bool,
        repeats: bool,
        obligation: Literal["need", "want"] | None = None,
        complete_by: str | None = None,
        time_of_day: str | None = None,
        recurrence: Annotated[
            dict[str, Any] | None, Field(description=RECURRENCE_END_DESC)
        ] = None,
        description: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Capture a new item by how it *behaves*; the server derives the kind.

        Answer three behavioral questions from world knowledge -- never naming a
        Deferno kind -- and the server deterministically picks Task / Chore /
        Habit / Event and builds the create payload:

        - ``attend`` -- do you *attend* it at a time (a meeting, appointment,
          class)? -> **Event**. Attendance wins over recurrence: a weekly
          stand-up is still an Event.
        - ``repeats`` -- does it recur on a schedule? A one-off -> **Task**.
        - ``obligation`` -- for a recurring, non-attended thing: does it **need**
          to happen (an obligation that carries forward if missed) -> **Chore**,
          or do you just **want** to at that cadence (an aspiration that lapses)
          -> **Habit**? Required when ``repeats`` and not ``attend``.

        ``complete_by`` is a full ISO-8601 datetime (an Event's day, else the
        deadline / series-start day) -- required for an Event and for a recurring
        Chore/Habit (its series start); optional only for a one-off Task. Supply
        it in the user's intended local day; the backend keys off its calendar
        date in the user's saved timezone. ``time_of_day``
        is ``HH:MM`` wall-clock (the Event start, else the deadline time).
        ``recurrence`` is the cadence (required for a recurring Chore/Habit); if
        its ``end`` is ``{type: on_date, date}``, that date must be on or after
        the series start (``complete_by``'s local calendar date) -- same-day is
        allowed.

        This is the single create front door. For a Task subtask under a parent,
        capture then ``move_item`` it under the parent; for a ``desire`` score or
        sequence chains, capture then ``update_item`` (parenting/desire/sequence
        apply only to Tasks). Advanced settings capture omits (an Event
        ``end_time``, a recurrence ``end`` bound) are a follow-up ``update_item``
        after capture.
        """
        try:
            kind, payload = derive_create_payload(
                title=title,
                attend=attend,
                repeats=repeats,
                obligation=obligation,
                complete_by=complete_by,
                time_of_day=time_of_day,
                recurrence=recurrence,
                description=description,
            )
        except CaptureError as exc:
            return f"capture_item: {exc}"

        async with (await get_client(ctx=ctx)) as client:
            creators = {
                "task": client.create_task,
                "chore": client.create_chore,
                "habit": client.create_habit,
                "event": client.create_event,
            }
            try:
                result = await creators[kind](payload)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)
