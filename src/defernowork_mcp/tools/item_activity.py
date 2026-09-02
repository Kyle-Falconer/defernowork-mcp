"""Kind-neutral item-level comment + attachment tools (issue #12).

These are the *kind-neutral* activity surface: comment on / attach to a **Task,
Chore, or Habit** by any reference form, hitting ``/items/{id}/...``. The caller
never addresses occurrences for those kinds — for recurring kinds the backend
routes the write to the current actionable occurrence and aggregates reads into
the item-level Activity timeline (see Deferno ADR
occurrence-comments-storage-vs-presentation).

**Events** are reached through the same tools via the optional occurrence
``date`` (ADR-0005 activity kind-merge): a ref that resolves to an Event with a
``date`` routes to the per-occurrence backend call
(``/events/{id}/occurrences/{date}/...``); every other case hits
``/items/{id}/...``. An Event ref **without** a ``date`` has no unambiguous
item-level target, so the backend rejects it with a 400 — pass the occurrence
``date``. (Edit/delete of an occurrence comment is not here — occurrence
comments aren't addressable by the generic ``/comments/{id}`` endpoints, so that
path stays in tools/event_occurrences.py.)

Status: the ``/items/{id}/comments`` path is live for Task and extends to
Chore/Habit with Deferno backend #266; the ``/items/{id}/attachments/*`` paths
land with #215. Until those ship, the Chore/Habit/attachment paths cannot be
verified end-to-end (the MCP wiring + ref resolution are exercised against the
mocked contract in tests/test_item_comments.py and tests/test_item_attachments.py).
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable

from mcp.server.mcpserver import Context, MCPServer

from ..client import DefernoClient, DefernoError
from ..refs import resolve_ref, resolve_ref_with_kind


def register(
    mcp: MCPServer,
    get_client: Callable[..., Awaitable[DefernoClient]],
    format_error: Callable[[DefernoError], str],
) -> None:
    @mcp.tool()
    async def post_item_comment(
        item_id: str,
        body: str,
        is_private: bool = False,
        date: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Append a comment to an item by reference (kind-neutral).

        Works for **Task, Chore, and Habit** directly. For an **Event**, pass
        the occurrence ``date`` (``YYYY-MM-DD``) and the comment is routed to
        that occurrence; an Event ref **without** a ``date`` is rejected by the
        backend with a 400 (no unambiguous item-level target).

        ``item_id`` accepts any item ref.

        For a recurring Chore/Habit the comment is routed server-side to the
        current actionable occurrence; the caller does not address occurrences.
        ``is_private`` keeps the comment visible only to its author. Returns the
        persisted comment (id + created_at).
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                uuid, kind = await resolve_ref_with_kind(client, item_id)
                if kind == "event" and date is not None:
                    comment = await client.post_event_occurrence_comment(
                        uuid, date, body, is_private
                    )
                else:
                    comment = await client.post_item_comment(uuid, body, is_private)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(comment)

    @mcp.tool()
    async def list_item_comments(item_id: str, ctx: Context = None) -> str:
        """List an item's comments by reference (kind-neutral).

        ``item_id`` accepts any item ref.

        Returns the aggregated item-level Activity timeline. For a recurring
        Chore/Habit, entries from every occurrence are folded in (each tagged
        with its occurrence date); for a Task it is simply the task's comments.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                item_id = await resolve_ref(client, item_id)
                comments = await client.list_item_comments(item_id)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(comments)

    @mcp.tool()
    async def presign_item_attachments(
        item_id: str,
        files: list[dict],
        date: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Batch-presign attachment upload URLs for an item (kind-neutral).

        Works for **Task, Chore, and Habit** directly. For an **Event**, pass
        the occurrence ``date`` (``YYYY-MM-DD``) to presign on that occurrence;
        an Event ref **without** a ``date`` is rejected by the backend with a
        400.

        ``item_id`` accepts any item ref.

        ``files`` is a list of ``{filename, content_type, size_bytes}`` records.
        The server enforces a 25 MB per-file cap and a blocked-MIME list.
        Returns ``{attachment_id, put_url, expires_at}`` records — PUT each blob
        to its url, then call ``commit_item_attachments``.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                uuid, kind = await resolve_ref_with_kind(client, item_id)
                if kind == "event" and date is not None:
                    resp = await client.presign_event_occurrence_attachments(
                        uuid, date, files
                    )
                else:
                    resp = await client.presign_item_attachments(uuid, files)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(resp)

    @mcp.tool()
    async def commit_item_attachments(
        item_id: str,
        intents: list[str] | None = None,
        urls: list[dict] | None = None,
        date: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Commit presigned intents and/or url-provider entries to an item.

        Works for **Task, Chore, and Habit** directly. For an **Event**, pass
        the occurrence ``date`` (``YYYY-MM-DD``) to commit on that occurrence;
        an Event ref **without** a ``date`` is rejected by the backend with a
        400.

        ``item_id`` accepts any item ref.

        ``intents`` is a list of attachment_ids from a prior presign call whose
        blobs have been PUT. ``urls`` is a list of ``{url, filename?}`` entries
        for the url provider (no upload). At least one must be non-empty.
        Returns the item's full attachments list post-commit.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                uuid, kind = await resolve_ref_with_kind(client, item_id)
                if kind == "event" and date is not None:
                    resp = await client.commit_event_occurrence_attachments(
                        uuid, date, intents, urls
                    )
                else:
                    resp = await client.commit_item_attachments(uuid, intents, urls)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(resp)

    @mcp.tool()
    async def list_item_attachments(
        item_id: str,
        date: str | None = None,
        ctx: Context = None,
    ) -> str:
        """List an item's attachments by reference (kind-neutral).

        Works for **Task, Chore, and Habit** directly. For an **Event**, pass
        the occurrence ``date`` (``YYYY-MM-DD``) to list that occurrence's
        attachments; an Event ref **without** a ``date`` is rejected by the
        backend with a 400.

        ``item_id`` accepts any item ref.

        Returns the AttachmentView shape
        ``{id, provider, filename, mime, size, created_at, created_by, url,
        caption, caption_updated_at, caption_updated_by}``. For ``provider=s3``
        records, ``url`` is a freshly-signed GET URL.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                uuid, kind = await resolve_ref_with_kind(client, item_id)
                if kind == "event" and date is not None:
                    resp = await client.list_event_occurrence_attachments(uuid, date)
                else:
                    resp = await client.list_item_attachments(uuid)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(resp)

    @mcp.tool()
    async def delete_item_attachment(
        item_id: str,
        att_id: str,
        date: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Delete a single attachment from an item (kind-neutral).

        Works for **Task, Chore, and Habit** directly. For an **Event**, pass
        the occurrence ``date`` (``YYYY-MM-DD``) to delete from that occurrence;
        an Event ref **without** a ``date`` is rejected by the backend with a
        400.

        ``item_id`` accepts any item ref. ``att_id`` is an attachment
        id (not an item reference) and is passed through unresolved.

        Returns ``{"ok": true}`` on success.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                uuid, kind = await resolve_ref_with_kind(client, item_id)
                if kind == "event" and date is not None:
                    await client.delete_event_occurrence_attachment(uuid, date, att_id)
                else:
                    await client.delete_item_attachment(uuid, att_id)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps({"ok": True})

    @mcp.tool()
    async def set_item_attachment_caption(
        item_id: str,
        att_id: str,
        caption: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Set or clear an item attachment's caption (kind-neutral).

        ``item_id`` accepts any item ref. ``att_id`` is an attachment
        id (not an item reference) and is passed through unresolved.

        Pass a string to set/change the caption (max 500 characters), or
        ``caption=None`` (the default) to clear it — an empty string ``""`` is
        rejected by the backend with a 400, so ``null`` is the only clear.
        Returns the updated AttachmentView.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                item_id = await resolve_ref(client, item_id)
                resp = await client.set_item_attachment_caption(
                    item_id, att_id, caption
                )
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(resp)
