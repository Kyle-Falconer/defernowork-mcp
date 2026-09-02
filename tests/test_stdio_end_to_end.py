"""End-to-end proof that the server boots over stdio and answers a real client.

Everything else in this suite builds the server in-process and reaches past the
transport. This test does not. It spawns ``python -m defernowork_mcp`` as its
own process, speaks MCP over its stdin and stdout with the SDK's own client,
and reads the answers.

That makes it the check that the transport wiring holds. A rename that leaves
the package unimportable, or a transport argument the server no longer accepts,
fails here and nowhere else.

A stub HTTP backend stands in for Deferno, because the server subprocess cannot
see this process's respx mocks.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

WHOAMI = {"id": "user-1", "username": "kyle"}


class _StubBackend(BaseHTTPRequestHandler):
    """Answer ``GET /api/auth/me`` and nothing else."""

    def do_GET(self):  # noqa: N802 — the name is BaseHTTPRequestHandler's
        if self.path != "/api/auth/me":
            self.send_error(404)
            return
        body = json.dumps({"version": "0.2", "data": WHOAMI, "error": None}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        """Keep the handler off stderr, where it would mix with the server's log."""


@pytest.fixture
def backend_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubBackend)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def stdio_params(backend_url):
    """Launch parameters for the server as a separate process.

    ``DEFERNO_TOKEN`` keeps the server from reading the developer's own saved
    credentials, and points it at the stub instead of a real backend.
    """
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "defernowork_mcp"],
        env={
            "DEFERNO_BASE_URL": backend_url,
            "DEFERNO_TOKEN": "stub-token",
            "PATH": "/usr/bin:/bin",
        },
    )


async def test_stdio_server_lists_tools_and_answers_a_tool_call(stdio_params):
    async with stdio_client(stdio_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            listing = await session.list_tools()
            names = {tool.name for tool in listing.tools}
            assert "whoami" in names
            assert "capture_item" in names
            assert "get_daily_plan" in names

            result = await session.call_tool("whoami")
            assert not result.is_error
            assert json.loads(result.content[0].text) == WHOAMI


async def test_stdio_server_lists_the_bounded_resources(stdio_params):
    """The two concrete resources survive the transport, and the template does too."""
    async with stdio_client(stdio_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            resources = await session.list_resources()
            uris = {str(resource.uri) for resource in resources.resources}
            assert "defernowork://tasks/plan" in uris
            assert "defernowork://tasks/mood-history" in uris

            templates = await session.list_resource_templates()
            patterns = {t.uri_template for t in templates.resource_templates}
            assert "defernowork://item/{ref}" in patterns
