"""Playwright helper that drives the Zitadel login from the captured authorize URL.

Records a Playwright trace, returns the callback redirect's status + query
params. Caller manages the BrowserContext lifecycle.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import BrowserContext


def _authorize_url_re() -> re.Pattern[str]:
    auth_base = os.environ.get("ZITADEL_AUTH_URL", "https://auth2.defernowork.com").rstrip("/")
    return re.compile(rf"^{re.escape(auth_base)}/oauth/v2/authorize\?")


@dataclass
class OAuthDanceResult:
    callback_status: int
    callback_query: dict[str, str]
    callback_body: str | None = None
    network_events: list[dict] = field(default_factory=list)


def complete_zitadel_login(
    context: BrowserContext,
    authorize_url: str,
    user: str,
    password: str,
    artifacts_dir: Path,
    timeout_ms: int = 30_000,
) -> OAuthDanceResult:
    """Drive the Zitadel form to completion. Returns the MCP callback's status."""
    if not _authorize_url_re().match(authorize_url):
        raise AssertionError(
            f"Authorize URL did not match expected pattern: {authorize_url!r}"
        )

    context.tracing.start(snapshots=True, screenshots=True, sources=False)

    callback_status: int | None = None
    callback_query: dict[str, str] = {}
    callback_body: str | None = None
    network_events: list[dict] = []

    page = context.new_page()

    def on_response(resp):
        nonlocal callback_status, callback_query, callback_body
        url = resp.url
        network_events.append({"url": url, "status": resp.status})
        if "/mcp/oauth/oidc-callback" in url:
            callback_status = resp.status
            qs = parse_qs(urlparse(url).query)
            callback_query = {k: v[0] for k, v in qs.items()}
            try:
                callback_body = resp.text()
            except Exception:
                callback_body = None

    page.on("response", on_response)
    page.goto(authorize_url)
    page.fill("input[name='loginName']", user)
    page.click("button[type='submit']")
    page.wait_for_selector("input[name='password']")
    page.fill("input[name='password']", password, force=True)
    page.click("button[type='submit']")

    deadline = time.monotonic() + (timeout_ms / 1000)
    while callback_status is None and time.monotonic() < deadline:
        page.wait_for_timeout(200)

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    context.tracing.stop(path=str(artifacts_dir / "trace.zip"))

    if callback_status is None:
        raise AssertionError(
            "Callback response was never observed within timeout. "
            "Check the Playwright trace for the form-submission outcome."
        )
    return OAuthDanceResult(
        callback_status=callback_status,
        callback_query=callback_query,
        callback_body=callback_body,
        network_events=network_events,
    )
