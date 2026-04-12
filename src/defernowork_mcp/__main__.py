"""Entry point for ``python -m defernowork_mcp`` and the ``defernowork-mcp`` script."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
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

    args = parser.parse_args()

    if args.transport == "http":
        from .server import main_http
        main_http(host=args.host, port=args.port)
    else:
        from .server import main as main_stdio
        main_stdio()


if __name__ == "__main__":
    sys.exit(main())
