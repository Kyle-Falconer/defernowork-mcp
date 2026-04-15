"""Audit logging for MCP tool invocations.

Logs to a Redis Stream (``mcp:audit``) with automatic trimming.
Each entry records who did what and when.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable

from mcp.server.fastmcp import Context

logger = logging.getLogger("defernowork-mcp")


def get_auth_user_id(ctx: Context | None) -> str:
    """Extract the user ID from the authenticated MCP context.

    In HTTP mode with OAuth, the MCP framework's auth middleware
    stores the AccessToken in the Starlette auth context.
    """
    if ctx is None:
        return ""
    try:
        # The auth middleware puts AuthenticatedUser on the request scope.
        # ctx.request_context may expose it, but the simplest approach is
        # to read from the access token stored in our Redis.
        # For now, return empty — we'll wire this up when we integrate
        # with the auth context middleware.
        return ""
    except Exception:
        return ""
