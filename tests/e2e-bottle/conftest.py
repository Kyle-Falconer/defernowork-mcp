"""Bottle conftest. Session fixtures: env check, MCP registration with Claude Code.
Per-test fixtures: fresh BrowserContext, cleared Claude Code creds, FIFO drain.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext, sync_playwright

from helpers.claude_code import add_mcp_server, clear_credential_file


REQUIRED_ENV = [
    "ZITADEL_TEST_USER",
    "ZITADEL_TEST_PASSWORD",
    "ZITADEL_AUTH_URL",
    "MCP_SERVER_UNDER_TEST",
    "ANTHROPIC_API_KEY",
]


@pytest.fixture(scope="session", autouse=True)
def _check_env() -> None:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        pytest.skip(f"Bottle env not set: missing {missing}. See .env.e2e.example.")


@pytest.fixture(scope="session", autouse=True)
def _register_mcp(_check_env) -> None:
    add_mcp_server("deferno", os.environ["MCP_SERVER_UNDER_TEST"])


@pytest.fixture(scope="session")
def playwright_browser():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def browser_context(playwright_browser: Browser) -> BrowserContext:
    """Fresh BrowserContext per test — cookies/storage reset."""
    ctx = playwright_browser.new_context()
    yield ctx
    ctx.close()


@pytest.fixture
def fresh_creds() -> None:
    """Per-test: clear Claude Code's MCP credential file before AND after."""
    clear_credential_file()
    yield
    clear_credential_file()


@pytest.fixture
def artifacts_dir(tmp_path) -> Path:
    """Per-test artifact directory for traces, envelope dumps, FIFO captures."""
    d = tmp_path / "artifacts"
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture
def fifo_drain():
    """Drain the auth-url FIFO before yielding so a stale URL from a previous
    test never leaks. Reading with O_NONBLOCK drains without blocking when no
    writer is connected.
    """
    fifo = "/tmp/auth-url.fifo"
    if not os.path.exists(fifo):
        yield
        return
    fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        try:
            while os.read(fd, 4096):
                pass
        except BlockingIOError:
            pass
    finally:
        os.close(fd)
    yield
