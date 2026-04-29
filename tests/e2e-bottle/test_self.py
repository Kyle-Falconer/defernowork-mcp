"""Bottle self-test — pre-flight probes. If these fail, no scenario can pass.

Run with:
    docker compose -f docker-compose.e2e.yml --env-file .env.e2e \\
      run --rm e2e-runner pytest test_self.py -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
from urllib.parse import urlparse

import httpx


def test_pytest_runs_in_runner():
    assert 1 + 1 == 2


def test_claude_code_installed():
    assert shutil.which("claude") is not None, "Claude Code CLI not on PATH"
    out = subprocess.run(["claude", "--version"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_capture_url_hook_present():
    hook = os.environ.get("BROWSER")
    assert hook == "/usr/local/bin/capture-url.sh", \
        f"BROWSER env var must point at capture-url.sh; got {hook!r}"
    assert os.path.exists(hook), f"BROWSER hook missing: {hook}"
    assert os.access(hook, os.X_OK), f"BROWSER hook not executable: {hook}"


def test_fifo_present():
    assert os.path.exists("/tmp/auth-url.fifo"), \
        "FIFO /tmp/auth-url.fifo missing — runner image step `mkfifo` failed?"


def test_staging_zitadel_reachable():
    url = os.environ["ZITADEL_AUTH_URL"].rstrip("/") + "/.well-known/openid-configuration"
    resp = httpx.get(url, timeout=10)
    assert resp.status_code == 200, f"Zitadel discovery returned {resp.status_code}"
    body = resp.json()
    assert "issuer" in body, f"Discovery missing 'issuer': {body}"


def test_staging_mcp_reachable():
    """The MCP's well-known OAuth metadata is unauthenticated and proves it's up."""
    base = os.environ["MCP_SERVER_UNDER_TEST"].rstrip("/")
    parsed = urlparse(base)
    well_known = f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-authorization-server/mcp"
    resp = httpx.get(well_known, timeout=10)
    assert resp.status_code == 200, \
        f"MCP well-known returned {resp.status_code} for {well_known}"
