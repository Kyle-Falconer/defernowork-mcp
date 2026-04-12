"""Deferno MCP server package."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("defernowork-mcp")
except PackageNotFoundError:
    __version__ = "0.0.0"
