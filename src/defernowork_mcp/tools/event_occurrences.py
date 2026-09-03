"""MCP tools for Event-occurrence comment EDIT / DELETE (the gate fallback).

Only the occurrence-comment *edit* and *delete* operations live here. Posting a
new occurrence comment, and every occurrence *attachment* op, moved to the
kind-neutral item-level tools (tools/item_activity.py), which gained an optional
occurrence ``date`` and route an Event ref + ``date`` to the per-occurrence
backend call.

Edit/delete could not move the same way: a comment created on an Event
occurrence (``POST /events/{id}/occurrences/{date}/comment``) is pushed onto an
embedded ``Occurrence.comment`` Vec and is **not** written to the
``embedded_comment:<id>`` index, so the generic ``PATCH`` / ``DELETE
/comments/{id}`` handlers (the ``update_comment`` / ``delete_comment`` tools)
**404** on it. These two per-occurrence tools, addressed by ``event_id`` +
``date``, are therefore the only edit/delete path for an occurrence comment
(ADR-0005's documented fallback).
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable

from mcp.server.mcpserver import Context, MCPServer

from ..client import DefernoClient, DefernoError
from ..refs import resolve_ref


def register(
    mcp: MCPServer,
    get_client: Callable[..., Awaitable[DefernoClient]],
    format_error: Callable[[DefernoError], str],
) -> None:
    @mcp.tool()
    async def patch_event_occurrence_comment(
        event_id: str,
        date: str,
        body: str | None = None,
        is_private: bool | None = None,
        ctx: Context = None,
    ) -> str:
        """Edit the latest comment on an event occurrence (date).

        ``event_id`` accepts any item ref.

        This is the only edit path for an occurrence comment: occurrence
        comments live on the embedded ``Occurrence.comment`` list and are not
        addressable by the generic ``update_comment`` (``PATCH /comments/{id}``)
        endpoint, which 404s on them.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                event_id = await resolve_ref(client, event_id)
                resp = await client.patch_event_occurrence_comment(
                    event_id, date, body, is_private
                )
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(resp)

    @mcp.tool()
    async def delete_event_occurrence_comment(
        event_id: str,
        date: str,
        ctx: Context = None,
    ) -> str:
        """Soft-delete the latest comment on an event occurrence (date).

        ``event_id`` accepts any item ref.

        This is the only delete path for an occurrence comment: occurrence
        comments live on the embedded ``Occurrence.comment`` list and are not
        addressable by the generic ``delete_comment`` (``DELETE /comments/{id}``)
        endpoint, which 404s on them.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                event_id = await resolve_ref(client, event_id)
                await client.delete_event_occurrence_comment(event_id, date)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps({"ok": True})
