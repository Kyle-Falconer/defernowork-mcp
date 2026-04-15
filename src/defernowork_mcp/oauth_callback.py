"""Kanidm OAuth callback route.

After a user authenticates with Kanidm, Kanidm redirects here.
We exchange the code, obtain a Deferno session, and redirect the
user back to the original MCP client with an authorization code.

If a legacy Deferno account exists with the same username, we show
a password form so the user can prove ownership before linking.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .oauth_provider import LegacyAccountError

logger = logging.getLogger("defernowork-mcp")


async def kanidm_callback(request: Request) -> Response:
    """Handle Kanidm's redirect after user authentication."""
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
    except LegacyAccountError as exc:
        # Show a password form to link the legacy account
        return _legacy_link_page(exc.kanidm_subject, exc.username, kanidm_state)
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


async def link_legacy_account(request: Request) -> Response:
    """Handle the legacy account linking form submission."""
    from . import server as _server_mod

    provider = _server_mod._oauth_provider
    store = _server_mod._redis_store
    if provider is None or store is None:
        return Response("OAuth not configured", status_code=500)

    form = await request.form()
    kanidm_subject = form.get("kanidm_subject", "")
    username = form.get("username", "")
    password = form.get("password", "")
    nonce = form.get("nonce", "")

    if not all([kanidm_subject, username, password, nonce]):
        return Response("Missing fields", status_code=400)

    try:
        deferno_token = await provider.link_legacy_account(
            kanidm_subject=str(kanidm_subject),
            username=str(username),
            password=str(password),
        )
    except Exception as exc:
        logger.warning("Legacy link failed: %s", exc)
        return _legacy_link_page(
            str(kanidm_subject), str(username), str(nonce),
            error="Invalid password. Please try again.",
        )

    # Now complete the original OAuth flow.
    # Re-load the pending auth from the nonce (it was consumed, so we
    # need to have stored it again before showing the form).
    pending = await store.load_pending_auth(f"link:{nonce}")
    if pending is None:
        return Response(
            "Session expired. Please start the authentication again.",
            status_code=400,
        )

    from .redis_store import _generate_token
    import time

    mcp_code = _generate_token()
    await store.save_auth_code(
        mcp_code,
        data={
            "code": mcp_code,
            "client_id": pending["client_id"],
            "scopes": pending["scopes"] or [],
            "code_challenge": pending["code_challenge"],
            "redirect_uri": pending["redirect_uri"],
            "redirect_uri_provided_explicitly": pending["redirect_uri_provided_explicitly"],
            "resource": pending.get("resource"),
            "expires_at": time.time() + 300,
        },
        meta={
            "deferno_token": deferno_token,
            "user_id": kanidm_subject,
            "username": username,
        },
    )

    redirect_uri = pending["redirect_uri"]
    params: dict[str, str] = {"code": mcp_code}
    if pending.get("state"):
        params["state"] = pending["state"]
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        url=f"{redirect_uri}{separator}{urlencode(params)}",
        status_code=302,
    )


def _legacy_link_page(
    kanidm_subject: str,
    username: str,
    nonce: str,
    error: str = "",
) -> HTMLResponse:
    """Render a simple password form for legacy account linking."""
    error_html = f'<p style="color:#f44;margin-bottom:16px">{error}</p>' if error else ""
    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head><title>Link Account — Deferno</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #1a1a2e; color: #e0e0e0;
         display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }}
  .card {{ background: #16213e; padding: 2rem; border-radius: 12px; max-width: 400px; width: 100%; }}
  h2 {{ margin-top: 0; color: #fff; }}
  p {{ line-height: 1.5; color: #aaa; }}
  input {{ width: 100%; padding: 10px; border: 1px solid #334; border-radius: 6px;
           background: #0f3460; color: #fff; font-size: 16px; box-sizing: border-box; }}
  button {{ width: 100%; padding: 12px; border: none; border-radius: 6px; background: #1f6f50;
            color: #fff; font-size: 16px; cursor: pointer; margin-top: 12px; }}
  button:hover {{ background: #28a06a; }}
</style>
</head>
<body>
<div class="card">
  <h2>Link Your Account</h2>
  <p>A Deferno account for <strong>{username}</strong> already exists.
     Enter your Deferno password to link it with your Kanidm login.</p>
  {error_html}
  <form method="POST" action="/oauth/link-legacy">
    <input type="hidden" name="kanidm_subject" value="{kanidm_subject}">
    <input type="hidden" name="username" value="{username}">
    <input type="hidden" name="nonce" value="{nonce}">
    <input type="password" name="password" placeholder="Deferno password" required autofocus>
    <button type="submit">Link Account</button>
  </form>
</div>
</body>
</html>""")
