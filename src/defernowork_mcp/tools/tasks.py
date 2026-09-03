"""Task CRUD + tree operation tools."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from mcp.server.mcpserver import Context, MCPServer

from ..client import DefernoClient, DefernoError
from ..refs import resolve_ref

_UNSET = object()


def register(
    mcp: MCPServer,
    get_client: Callable[..., Awaitable[DefernoClient]],
    format_error: Callable[[DefernoError], str],
    compact: Callable[[dict[str, Any]], dict[str, Any]],
    unset: object,
) -> None:
    @mcp.tool()
    async def promote_task(
        task_id: str,
        target_org_id: str,
        ctx: Context = None,
    ) -> str:
        """Promote a personal-org task into a target org.

        Moves the task from the caller's personal org into ``target_org_id``,
        re-encrypting it under the target org's data-encryption key. The
        caller must own the task in their personal org AND be a member of
        ``target_org_id``. Returns JSON ``null`` on success (the backend
        returns no body).
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                await client.promote_task(task_id, target_org_id)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(None)

    @mcp.tool()
    async def batch_tasks(
        operations: list[dict[str, Any]],
        ctx: Context = None,
    ) -> str:
        """Execute multiple task operations atomically in a single call.

        ``operations`` is a list of operation objects. Each must have an
        ``op`` field (``"update"`` or ``"move"``) and a ``task_id``.

        Update operations accept the Task mutable fields (``title``,
        ``description``, ``status``, ``labels``, etc.) at the top level alongside
        ``op`` and ``task_id``. (This is Tasks-only batch; for a single item of
        any kind use ``update_item``.)

        Move operations accept ``new_parent_id`` (null for root) and an
        optional ``position`` (insertion index).

        Both ``task_id`` and ``new_parent_id`` in each operation are any item
        ref; a ``new_parent_id`` of ``null`` (detach to root) is left as-is.

        All operations succeed or none do (all-or-nothing).  On success
        returns ``{"tasks": [...]}``, the list of all modified tasks.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                for op in operations:
                    if op.get("task_id") is not None:
                        op["task_id"] = await resolve_ref(client, op["task_id"])
                    if op.get("new_parent_id") is not None:
                        op["new_parent_id"] = await resolve_ref(
                            client, op["new_parent_id"]
                        )
                result = await client.batch(operations)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)
