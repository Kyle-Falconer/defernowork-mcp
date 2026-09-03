"""Cross-kind item tools: kind-neutral mutate/delete, calendar + plan."""

from __future__ import annotations

import json
from typing import Annotated, Any, Awaitable, Callable

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field

from ..client import DefernoClient, DefernoError
from ..constraints import EVENT_END_TIME_DESC, RECURRENCE_END_DESC
from ..refs import (
    COMPACT_ITEM_CORE_FIELDS,
    COMPACT_ITEM_FIELDS,
    project,
    resolve_ref,
    resolve_ref_with_kind,
)

# Fields update_item accepts that apply to a Task only; the rest (title,
# description, complete_by, labels, recurrence) are shared across all kinds.
_TASK_ONLY_FIELDS = frozenset(
    {"status", "assignee", "productive", "desire",
     "recurring_scope", "recurrence_id", "recurring_type", "blocked_by"}
)


def register(
    mcp: MCPServer,
    get_client: Callable[..., Awaitable[DefernoClient]],
    format_error: Callable[[DefernoError], str],
    compact: Callable[[dict[str, Any]], dict[str, Any]],
    unset: object,
) -> None:
    @mcp.tool()
    async def update_item(
        ref: str,
        title: str | None = unset,
        description: str | None = unset,
        complete_by: str | None = unset,
        labels: list[str] | None = unset,
        recurrence: Annotated[
            dict[str, Any] | None, Field(description=RECURRENCE_END_DESC)
        ] = unset,
        status: str | None = unset,
        assignee: str | None = unset,
        productive: float | None = unset,
        desire: float | None = unset,
        recurring_scope: str | None = unset,
        recurrence_id: str | None = unset,
        recurring_type: str | None = unset,
        blocked_by: list[dict[str, Any]] | None = unset,
        end_time: Annotated[
            str | None, Field(description=EVENT_END_TIME_DESC)
        ] = unset,
        ctx: Context = None,
    ) -> str:
        """Patch mutable fields on any item (Task / Chore / Habit / Event).

        ``ref`` is any item ref (see the server instructions on identifiers); the
        kind is resolved from it and the call dispatches to the matching backend.
        Omitting a parameter leaves it unchanged; pass ``None`` to clear it (e.g.
        ``complete_by=None`` drops a Task deadline — a Chore's ``complete_by``
        cannot be cleared).

        Shared fields (all kinds): ``title``, ``description``, ``complete_by``,
        ``labels``, ``recurrence``. If ``recurrence`` carries an ``end`` of
        ``{type: on_date, date}``, that date must be on or after the series start
        (``complete_by``'s local calendar date); same-day is allowed.

        **Task-only**: ``status`` (one of ``open``, ``in-progress``,
        ``in-review``, ``done``, ``dropped``, ``pruned`` — the backend rejects
        completing a task with active children), ``assignee``, ``productive`` /
        ``desire`` (floats in [0, 1]), and the recurring-Task controls
        ``recurring_scope`` / ``recurrence_id`` / ``recurring_type``. For a
        recurring Task, changing ``title``/``description``/``labels``/
        ``complete_by`` requires ``recurring_scope`` — ``"this"`` (single
        instance), ``"following"`` (this and future), or ``"all"`` (series);
        ``"this"`` / ``"following"`` also need ``recurrence_id`` (the instance's
        ISO start). If scope is missing the call returns a message asking for it.

        ``blocked_by`` is the dependency edge — a list of
        ``{"item": <ref>, "occurrence"?: "YYYY-MM-DD"}`` entries. Each blocker is
        a Task (omit ``occurrence``) or a recurring occurrence (``occurrence``
        required). Three-state: omit to leave unchanged, pass ``[]`` or ``None``
        to clear all blockers, pass a list to replace the set. Each ``item`` is
        any item ref, resolved transparently.

        **Event-only**: ``end_time`` — when provided it must be on or after
        ``complete_by`` (the Event start), else the backend rejects it with a 400.

        To change a recurring Chore/Habit/Event *occurrence* (mark it done,
        skip, reschedule) use the occurrence tools, not this.
        """
        fields = {
            "title": title, "description": description,
            "complete_by": complete_by, "labels": labels,
            "recurrence": recurrence, "status": status,
            "assignee": assignee, "productive": productive,
            "desire": desire, "recurring_scope": recurring_scope,
            "recurrence_id": recurrence_id, "recurring_type": recurring_type,
            "blocked_by": blocked_by, "end_time": end_time,
        }
        provided = {k for k, v in fields.items() if v is not unset}
        async with (await get_client(ctx=ctx)) as client:
            try:
                uuid, kind = await resolve_ref_with_kind(client, ref)
                # Reject fields that don't apply to the resolved kind, before any
                # write (trust boundary) — mirrors capture_item's create path.
                if kind != "task":
                    bad = sorted(_TASK_ONLY_FIELDS & provided)
                    if bad:
                        return (
                            f"update_item: {', '.join(bad)} apply only to a Task "
                            f"(this is a {kind}); use the occurrence tools to act "
                            "on a recurring occurrence"
                        )
                if kind != "event" and "end_time" in provided:
                    return f"update_item: end_time applies only to an Event (this is a {kind})"

                # Resolve each blocker's `item` ref to a UUID, preserving its
                # occurrence (clear/no-op forms — None/[]/unset — pass through).
                if isinstance(blocked_by, list) and blocked_by:
                    if not all(isinstance(b, dict) and "item" in b for b in blocked_by):
                        return (
                            'update_item: each blocked_by entry must be '
                            '{"item": <ref>, "occurrence"?: "YYYY-MM-DD"}'
                        )
                    fields["blocked_by"] = [
                        {"item": await resolve_ref(client, b["item"]),
                         "occurrence": b.get("occurrence")}
                        for b in blocked_by
                    ]

                payload = compact(fields)

                if kind == "task":
                    # Recurring-Task scope guard: a deferno-field change with no
                    # scope on a series-backed task is ambiguous — ask first.
                    if recurring_scope is unset:
                        deferno_fields = {"title", "description", "labels", "complete_by"}
                        if any(k in payload for k in deferno_fields):
                            task_data = await client.get_task(uuid)
                            if task_data.get("series_id"):
                                return (
                                    "This is a recurring task. Please specify "
                                    "recurring_scope: 'this' (single instance), "
                                    "'following' (this and future events), or "
                                    "'all' (entire series). "
                                    "Ask the user which option they prefer."
                                )
                    result = await client.update_task(uuid, payload)
                elif kind == "chore":
                    result = await client.update_chore(uuid, payload)
                elif kind == "habit":
                    result = await client.update_habit(uuid, payload)
                elif kind == "event":
                    result = await client.update_event(uuid, payload)
                else:
                    return f"update_item: cannot update a {kind}"
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)

    @mcp.tool()
    async def delete_item(ref: str, ctx: Context = None) -> str:
        """Delete any item (Task / Chore / Habit / Event) by reference.

        ``ref`` is any item ref (see the server instructions). The kind is
        resolved from it and the call dispatches to the matching backend. Note
        the kinds differ: a **Task** is hard-deleted, while a **Chore / Habit /
        Event** is archived (soft-delete). Returns the resolved ``id`` and
        ``kind`` the deletion ran against.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                uuid, kind = await resolve_ref_with_kind(client, ref)
                if kind == "task":
                    await client.delete_task(uuid)
                elif kind == "chore":
                    await client.delete_chore(uuid)
                elif kind == "habit":
                    await client.delete_habit(uuid)
                elif kind == "event":
                    await client.delete_event(uuid)
                else:
                    return f"delete_item: cannot delete a {kind}"
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps({"deleted": True, "id": uuid, "kind": kind})

    @mcp.tool()
    async def get_item(
        item: str,
        full: bool = False,
        as_alias: bool = False,
        ctx: Context = None,
    ) -> str:
        """Fetch a single item (Task / Habit / Chore / Event) by any reference.

        ``item`` is any item ref, resolved transparently — see the server
        instructions on identifiers. The
        unambiguous GitHub form ``owner/repo#N`` auto-routes to by-alias; for an
        ambiguous external alias (e.g. ``ABC-223``) pass ``as_alias=true`` to
        force the by-alias lookup. (A bare ``#N`` always means a Deferno sequence
        here, never a GitHub issue.)

        Returns a Compact projection by default (includes ``description``); pass
        ``full=true`` for the complete record (history, comments, children, mood,
        attachments, …).
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                if as_alias:
                    # Explicit escape-hatch: skip classify_ref entirely and hit
                    # by-alias with the raw string (one call, returns the item).
                    record = await client.get_item_by_alias(item)
                else:
                    item_id = await resolve_ref(client, item)
                    record = await client.get_item(item_id)
            except DefernoError as exc:
                return format_error(exc)
        if full:
            return json.dumps(record)
        return json.dumps(project(record, COMPACT_ITEM_FIELDS))

    @mcp.tool()
    async def list_items(
        kind: str | None = None,
        status: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int | None = None,
        full: bool = False,
        window: str | None = None,
        ctx: Context = None,
    ) -> str:
        """List items of any kind (Task / Habit / Chore / Event), windowed.

        The canonical, bounded list view. Returns a Compact projection by default
        (see the server instructions on Compact reads); ``full=true`` returns
        full rows.

        Filters (composed into an OData ``$filter`` with ``and``):

        - ``kind`` -- one of ``"task"``, ``"habit"``, ``"chore"``, ``"event"``.
        - ``status`` -- the item status (e.g. ``"open"``, ``"done"``).
        - ``from_date`` / ``to_date`` -- ``YYYY-MM-DD``; filter on ``complete_by``
          widened to RFC3339 day boundaries (start-of-day for ``from_date``,
          end-of-day for ``to_date``).

        An unknown / unfilterable field returns a backend 400, surfaced clearly
        (not swallowed).

        - ``limit`` -- maps to OData ``$top``. The backend caps ``$top`` at 500
          by REJECTING larger values with a 400 (it does NOT clamp); the number
          is passed through verbatim.
        - ``full=true`` -- return every field on each row (drops the projection).
        - ``window="all"`` -- opt out of the default done-visibility window for
          full history (the default window applies only to the unfiltered call).

        Regardless of projection, the backend always injects ``ref``,
        ``org_slug``, ``type`` and ``sequence`` into every row.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                rows = await client.list_items(
                    kind=kind,
                    status=status,
                    from_date=from_date,
                    to_date=to_date,
                    limit=limit,
                    full=full,
                    window=window,
                )
            except DefernoError as exc:
                return format_error(exc)
        if full:
            return json.dumps(rows)
        return json.dumps([project(row, COMPACT_ITEM_CORE_FIELDS) for row in rows])

    @mcp.tool()
    async def search_items(
        query: str,
        status: str | None = None,
        label: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        parent_id: str | None = None,
        full: bool = False,
        ctx: Context = None,
    ) -> str:
        """Full-text search over items, returning a Compact projection.

        The compact, kind-neutral full-text search over items. Returns a Compact
        projection by default (same field set as ``list_items``; see the server
        instructions on Compact reads); ``full=true`` returns rows verbatim.

        Scope: **full-text search currently covers Tasks only.** This tool is
        backed by the Tasks search path (``GET /tasks/search``) because the
        backend has no kind-neutral ``/items/search`` endpoint today; a
        kind-neutral full-text search is a known **backend follow-on** (to be
        filed in the Deferno backend repo, out of scope for the MCP). Non-Task
        kinds (Habits / Chores / Events) are therefore not reached by ``query``
        yet -- use ``list_items`` to enumerate those.

        Args:
            query: Search query (min 2 characters). Searches title and description.
            status: Filter by status (open, in-progress, in-review, done, dropped).
            label: Filter by label tag.
            from_date: Filter items due on or after this ISO 8601 date.
            to_date: Filter items due on or before this ISO 8601 date.
            parent_id: Scope search to children of this item — any item ref.
            full: When ``true``, return every field on each row (no projection).
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                if parent_id is not None:
                    parent_id = await resolve_ref(client, parent_id)
                rows = await client.search_tasks(
                    query,
                    status=status,
                    label=label,
                    from_date=from_date,
                    to_date=to_date,
                    parent_id=parent_id,
                )
            except DefernoError as exc:
                return format_error(exc)
        if full:
            return json.dumps(rows)
        return json.dumps([project(row, COMPACT_ITEM_CORE_FIELDS) for row in rows])

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
    async def add_to_items_plan(
        task_id: str,
        date: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Add an item (any kind) to the daily plan.

        ``task_id`` accepts any item ref, so a ``ref`` from ``list_items`` works directly.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                task_id = await resolve_ref(client, task_id)
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
        """Remove an item from the daily plan.

        ``task_id`` accepts any item ref.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                task_id = await resolve_ref(client, task_id)
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
        """Replace the daily plan ordering with the given full list of IDs.

        Each element of ``task_ids`` accepts any item ref.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                task_ids = [await resolve_ref(client, t) for t in task_ids]
                result = await client.reorder_items_plan(task_ids, date=date)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)

    @mcp.tool()
    async def convert_item(
        item_id: str,
        to: str,
        complete_by: str | None = None,
        end_time: str | None = None,
        recurrence: dict | None = None,
        ctx: Context = None,
    ) -> str:
        """Convert an item to a different kind (Task / Chore / Habit / Event).

        ``item_id`` accepts any item ref.

        ``to`` is one of ``"task"``, ``"chore"``, ``"habit"``, ``"event"`` --
        this is the backend wire field name (``ConvertItemPayload.to``).
        ``complete_by`` (RFC3339) is required when ``to`` is Event/Chore/Habit;
        ``recurrence`` is required when ``to`` is Habit/Chore (and optional for
        Event); ``end_time`` is Event-only. Returns the updated item view --
        the backend uses 201 on a real conversion, 200 when ``to`` equals the
        current kind (idempotent).
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                item_id = await resolve_ref(client, item_id)
                resp = await client.convert_item(
                    item_id,
                    to,
                    complete_by=complete_by,
                    end_time=end_time,
                    recurrence=recurrence,
                )
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(resp)

    @mcp.tool()
    async def get_item_history(item_id: str, ctx: Context = None) -> str:
        """Return the change-history list for any item kind (Task/Habit/Chore/Event).

        ``item_id`` accepts any item ref.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                item_id = await resolve_ref(client, item_id)
                resp = await client.get_item_history(item_id)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(resp)

    @mcp.tool()
    async def move_item(
        item_id: str,
        new_parent_id: str | None = None,
        position: int | None = None,
        ctx: Context = None,
    ) -> str:
        """Move any item (Task / Chore / Habit / Event) to a new parent or reorder it.

        Kind-neutral reparent/reorder via ``/items/{id}/move`` — works for every
        kind, so it is also how you parent a Chore/Habit/Event created with
        ``capture_item``. ``item_id`` and ``new_parent_id`` each accept any item
        ref.
        ``new_parent_id=None`` detaches to a root (kept as-is, not resolved);
        ``position`` is the insertion index in the target's children (0 = first;
        omit to append).
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                item_id = await resolve_ref(client, item_id)
                if new_parent_id is not None:
                    new_parent_id = await resolve_ref(client, new_parent_id)
                result = await client.move_item(item_id, new_parent_id, position)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)
