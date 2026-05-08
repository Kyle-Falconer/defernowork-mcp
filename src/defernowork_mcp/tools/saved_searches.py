"""Saved-search CRUD + reorder tools."""

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
    async def list_saved_searches(ctx: Context = None) -> str:
        """List the caller's saved searches in their explicit order."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                searches = await client.list_saved_searches()
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(searches)

    @mcp.tool()
    async def create_saved_search(
        name: str,
        query_string: str,
        ctx: Context = None,
    ) -> str:
        """Save a search. ``query_string`` is the same syntax as ``search_tasks``."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                search = await client.create_saved_search(name, query_string)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(search)

    @mcp.tool()
    async def update_saved_search(
        saved_search_id: str,
        name: str | None = None,
        query_string: str | None = None,
        ctx: Context = None,
    ) -> str:
        """Patch a saved search's name or query string."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                search = await client.update_saved_search(
                    saved_search_id, name=name, query_string=query_string
                )
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(search)

    @mcp.tool()
    async def delete_saved_search(
        saved_search_id: str,
        ctx: Context = None,
    ) -> str:
        """Delete a saved search."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                await client.delete_saved_search(saved_search_id)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps({"deleted": True, "saved_search_id": saved_search_id})

    @mcp.tool()
    async def reorder_saved_searches(
        ids: list[str],
        ctx: Context = None,
    ) -> str:
        """Replace the saved-search ordering with the given full list of IDs."""
        async with (await get_client(ctx=ctx)) as client:
            try:
                result = await client.reorder_saved_searches(ids)
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)
