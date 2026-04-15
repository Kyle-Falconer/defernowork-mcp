"""Kanidm OAuth callback route.

After a user authenticates with Kanidm, Kanidm redirects here.
We exchange the code, obtain a Deferno session, and redirect the
user back to the original MCP client with an authorization code.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

logger = logging.getLogger("defernowork-mcp")


async def kanidm_callback(request: Request) -> Response:
    """Handle Kanidm's redirect after user authentication.

    Query params from Kanidm:
      - ``code``: the Kanidm authorization code
      - ``state``: our nonce that maps to the pending auth in Redis
    """
    from . import server as _server_mod

    provider = _server_mod._oauth_provider
    if provider is None:
        return Response("OAuth not configured", status_code=500)

    kanidm_code = request.query_params.get("code")
    kanidm_state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        logger.error("Kanidm returned error: %s - %s",
                      error, request.query_params.get("error_description", ""))
        return Response(f"Authentication failed: {error}", status_code=400)

    if not kanidm_code or not kanidm_state:
        return Response("Missing code or state", status_code=400)

    try:
        mcp_code, redirect_uri, state = await provider.handle_kanidm_callback(
            kanidm_state=kanidm_state,
            kanidm_code=kanidm_code,
        )
    except ValueError as exc:
        logger.warning("Kanidm callback error: %s", exc)
        return Response(str(exc), status_code=400)
    except Exception:
        logger.exception("Kanidm callback failed")
        return Response("Internal error during authentication", status_code=500)

    # Redirect back to the MCP client with the authorization code
    params: dict[str, str] = {"code": mcp_code}
    if state:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        url=f"{redirect_uri}{separator}{urlencode(params)}",
        status_code=302,
    )
