"""Entry point for ``python -m defernowork_mcp`` and the ``defernowork-mcp`` script."""

from __future__ import annotations

import argparse

from .server import DEFAULT_BASE_URL
import asyncio
import sys


def _run_auth(argv: list[str]) -> None:
    """Interactive browser-based auth flow."""
    from .client import DefernoClient, DefernoError
    from .credentials import save_credentials

    parser = argparse.ArgumentParser(
        prog="defernowork-mcp auth",
        description="Authenticate with Deferno via browser login",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Deferno backend URL (default: {DEFAULT_BASE_URL})",
    )
    args = parser.parse_args(argv)
    base_url = args.base_url

    async def _auth() -> None:
        async with DefernoClient(base_url=base_url) as client:
            try:
                init = await client.cli_init()
            except DefernoError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                raise SystemExit(1) from exc

        auth_url = init["auth_url"]
        session_id = init["session_id"]

        print(f"\nOpen this URL in your browser to sign in:\n\n  {auth_url}\n")
        code = input("Paste the code shown after login: ").strip()
        if not code:
            print("No code entered.", file=sys.stderr)
            raise SystemExit(1)

        async with DefernoClient(base_url=base_url) as client:
            try:
                result = await client.cli_verify(session_id, code)
            except DefernoError as exc:
                print(f"Authentication failed: {exc}", file=sys.stderr)
                raise SystemExit(1) from exc

        token = result["token"]
        username = result.get("user", {}).get("username", "")
        save_credentials(token, username, base_url)
        print(f"\nAuthenticated as @{username}. Credentials saved.\n")

    asyncio.run(_auth())


def _run_serve(argv: list[str]) -> None:
    """Start the MCP server."""
    parser = argparse.ArgumentParser(
        prog="defernowork-mcp",
        description="Deferno MCP server",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport to use (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind (HTTP transport only, default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to listen on (HTTP transport only, default: 8080)",
    )
    args = parser.parse_args(argv)

    if args.transport == "http":
        from .server import main_http
        main_http(host=args.host, port=args.port)
    else:
        from .server import main as main_stdio
        main_stdio()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "auth":
        _run_auth(sys.argv[2:])
    else:
        _run_serve(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
