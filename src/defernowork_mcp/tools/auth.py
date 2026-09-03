"""Authentication tools: whoami."""

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
    async def whoami(ctx: Context = None) -> str:
        """Return the currently authenticated Deferno user.

        Call this first to confirm that the Authorization header is valid
        before issuing task operations.
        """
        async with (await get_client(ctx=ctx)) as client:
            try:
                result = await client.whoami()
            except DefernoError as exc:
                return format_error(exc)
        return json.dumps(result)
