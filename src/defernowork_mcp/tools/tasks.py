"""Task CRUD + tree operation tools."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import Context, FastMCP

from ..client import DefernoClient, DefernoError

_UNSET = object()


def register(
    mcp: FastMCP,
    get_client: Callable[..., Awaitable[DefernoClient]],
    format_error: Callable[[DefernoError], str],
    compact: Callable[[dict[str, Any]], dict[str, Any]],
    unset: object,
) -> None:
    @mcp.tool()
    async def list_tasks(ctx: Context = None) -> str:
        """List every task owned by the authenticated user.

        Returns a JSON array of task objects. Use ``get_task`` for full
        detail on a specific task by id.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                tasks = await client.list_tasks()
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(tasks)

    @mcp.tool()
    async def get_task(task_id: str, ctx: Context = None) -> str:
        """Fetch a single task by id (UUID)."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                task = await client.get_task(task_id)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(task)

    @mcp.tool()
    async def create_task(
        title: str,
        description: str,
        labels: list[str] | None = None,
        parent_id: str | None = None,
        assignee: str | None = None,
        complete_by: str | None = None,
        productive: float | None = None,
        desire: float | None = None,
        recurrence: dict[str, Any] | None = None,
        ctx: Context = None,
    ) -> str:
        """Create a new task.

        ``complete_by`` must be an ISO-8601 UTC timestamp.
        ``parent_id`` attaches the new task as a child of an existing task.
        ``productive`` and ``desire`` are floats in [0, 1] representing how
        productive this task feels and how much the user wants to do it.
        ``recurrence`` sets a repeat schedule. Use ``{"type": "daily"}``,
        ``{"type": "every_n_days", "n": 3}``, or
        ``{"type": "weekly", "days": ["Mon", "Wed", "Fri"]}``.
        """
        payload = compact(
            {
                "title": title,
                "description": description,
                "labels": labels,
                "parent_id": parent_id,
                "assignee": assignee,
                "complete_by": complete_by,
                "productive": productive,
                "desire": desire,
                "recurrence": recurrence,
            }
        )
        async with (await get_client(ctx=ctx)) as client:
            try:
                task = await client.create_task(payload)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(task)

    @mcp.tool()
    async def update_task(
        task_id: str,
        title: str | None = unset,
        description: str | None = unset,
        status: str | None = unset,
        labels: list[str] | None = unset,
        assignee: str | None = unset,
        complete_by: str | None = unset,
        productive: float | None = unset,
        desire: float | None = unset,
        recurrence: dict[str, Any] | None = unset,
        recurring_scope: str | None = unset,
        recurrence_id: str | None = unset,
        ctx: Context = None,
    ) -> str:
        """Patch mutable fields on a task.

        ``status`` must be one of ``open``, ``in-progress``, ``in-review``,
        ``done``, ``dropped``, ``pruned``. The backend rejects completing a
        task while any of its children are still active.

        Pass ``None`` explicitly to clear a field (e.g. ``complete_by=None``
        removes the deadline). Omitting a parameter leaves it unchanged.

        ``recurrence`` sets or clears a repeat schedule (see ``create_task``).

        For recurring tasks, if you change title, description, labels, or
        complete_by, you MUST also provide ``recurring_scope``:
        ``"this"`` (single instance), ``"following"`` (this and future),
        or ``"all"`` (entire series). ``"this"`` and ``"following"`` also
        require ``recurrence_id`` (the ISO start time of the instance).
        If the task is recurring and scope is missing, the call will fail
        with a message asking you to specify the scope — ask the user
        which option they prefer.
        """
        payload = compact(
            {
                "title": title,
                "description": description,
                "status": status,
                "labels": labels,
                "assignee": assignee,
                "complete_by": complete_by,
                "productive": productive,
                "desire": desire,
                "recurrence": recurrence,
                "recurring_scope": recurring_scope,
                "recurrence_id": recurrence_id,
            }
        )
        async with (await get_client(ctx=ctx)) as client:
            try:
                # Check if this is a recurring task needing a scope.
                if recurring_scope is unset:
                    deferno_fields = {"title", "description", "labels", "complete_by"}
                    has_deferno_changes = any(k in payload for k in deferno_fields)
                    if has_deferno_changes:
                        task_data = await client.get_task(task_id)
                        if task_data.get("series_id"):
                            return (
                                "This is a recurring task. Please specify "
                                "recurring_scope: 'this' (single instance), "
                                "'following' (this and future events), or "
                                "'all' (entire series). "
                                "Ask the user which option they prefer."
                            )

                task = await client.update_task(task_id, payload)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(task)

    @mcp.tool()
    async def set_task_status(task_id: str, status: str, ctx: Context = None) -> str:
        """Convenience wrapper around ``update_task`` for status changes.

        Accepts ``open``, ``in-progress``, ``in-review``, ``done``, ``dropped``, ``pruned``.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                task = await client.update_task(task_id, {"status": status})
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(task)

    @mcp.tool()
    async def move_task(
        task_id: str,
        new_parent_id: str | None = None,
        position: int | None = None,
        ctx: Context = None,
    ) -> str:
        """Move a task to a different parent or reorder within its current parent.

        ``new_parent_id=None`` detaches the task to root level.
        ``position`` is the insertion index in the target's children list
        (0 = first). Omit to append at end.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                task = await client.move_task(task_id, new_parent_id, position)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(task)

    @mcp.tool()
    async def split_task(
        task_id: str,
        first_title: str,
        first_description: str,
        second_title: str,
        second_description: str,
        ctx: Context = None,
    ) -> str:
        """Decompose a task into two child tasks while preserving the parent.

        Returns the updated parent and both new children.
        """
        payload = {
            "first_title": first_title,
            "first_description": first_description,
            "second_title": second_title,
            "second_description": second_description,
        }
        async with (await get_client(ctx=ctx)) as client:
            try:
                result = await client.split_task(task_id, payload)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)

    @mcp.tool()
    async def fold_task(
        task_id: str,
        title: str,
        description: str,
        labels: list[str] | None = None,
        desire: float | None = None,
        productive: float | None = None,
        complete_by: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Insert a new next-step task directly after ``task_id`` in the sequence.

        Preserves any existing downstream chain. Returns the original task
        and the newly created next task.
        """
        payload = compact(
            {
                "title": title,
                "description": description,
                "labels": labels,
                "desire": desire,
                "productive": productive,
                "complete_by": complete_by,
            }
        )
        async with (await get_client(ctx=ctx)) as client:
            try:
                result = await client.fold_task(task_id, payload)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)

    @mcp.tool()
    async def merge_task(task_id: str, ctx: Context = None) -> str:
        """Roll the active children of a task back into the parent.

        Child content is appended to the parent description; the children are
        marked as ``pruned`` but remain recoverable. Pass the id of any
        child whose parent should receive the merge.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                result = await client.merge_task(task_id)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)

    @mcp.tool()
    async def batch(
        operations: list[dict[str, Any]],
        ctx: Context = None,
    ) -> str:
        """Execute multiple task operations atomically in a single call.

        ``operations`` is a list of operation objects. Each must have an
        ``op`` field (``"update"`` or ``"move"``) and a ``task_id``.

        Update operations accept the same fields as ``update_task``
        (``title``, ``description``, ``status``, ``labels``, etc.) at the
        top level alongside ``op`` and ``task_id``.

        Move operations accept ``new_parent_id`` (UUID or null for root)
        and an optional ``position`` (insertion index).

        All operations succeed or none do (all-or-nothing).  On success
        returns ``{"tasks": [...]}``, the list of all modified tasks.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                result = await client.batch(operations)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)

    @mcp.tool()
    async def get_mood_history(ctx: Context = None) -> str:
        """Return the user's historical mood-per-task log for finished tasks."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                history = await client.mood_history()
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(history)

    @mcp.tool()
    async def export_data(ctx: Context = None) -> str:
        """Export all user data as JSON.

        Returns a complete backup of all tasks (with full history, mood
        vectors, recurrence rules), root ordering, and daily plans.
        The export can be imported via the Deferno web UI settings page.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                result = await client.export_data()
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)
