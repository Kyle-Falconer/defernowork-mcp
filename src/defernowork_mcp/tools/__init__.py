"""Tool registration modules for the Deferno MCP server."""

from .auth import register as register_auth
from .tasks import register as register_tasks
from .daily_plan import register as register_daily_plan

__all__ = ["register_auth", "register_tasks", "register_daily_plan"]
