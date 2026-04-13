# Deferno MCP Server

An [MCP](https://modelcontextprotocol.io) server that exposes the Deferno
task-manager backend to AI agents.

MCP is the open standard used by Claude Desktop / Claude Code, Cursor,
Windsurf, Zed, VS Code Copilot agents, Continue, OpenAI Agents, and others,
so this server works with any of them — you configure it once in your
client and every tool and resource below becomes available.

## What the agent can do

**Tools** (function calls)

| Tool                | Purpose                                                     |
| ------------------- | ----------------------------------------------------------- |
| `login`             | Authenticate with a username + password                     |
| `logout`            | Invalidate the stored session token                         |
| `register`          | Create a new Deferno user                                   |
| `whoami`            | Return the currently authenticated user                     |
| `list_tasks`        | List every task owned by the authenticated user             |
| `get_task`          | Fetch a single task by UUID                                 |
| `create_task`       | Create a new task (optionally nested under a parent)        |
| `update_task`       | Patch any mutable field (title, description, status, mood…) |
| `set_task_status`   | Convenience wrapper for `open`/`in-progress`/`done`/…       |
| `split_task`        | Decompose a task into two child tasks                       |
| `fold_task`         | Insert a next-step task into the sibling chain              |
| `merge_task`        | Roll a parent's active children back into the parent        |
| `get_daily_tasks`   | Today's prioritized tasks with urgency reasons              |
| `get_mood_history`  | Mood log for finished tasks                                 |

**Resources** (readable by MCP clients that index resources)

| URI                           | Content                        |
| ----------------------------- | ------------------------------ |
| `defernowork://tasks`         | All tasks for the current user |
| `defernowork://tasks/today`   | Today's prioritized tasks      |
| `defernowork://tasks/mood-history` | Mood log for finished tasks |
| `defernowork://task/{task_id}` | A single task by UUID         |

## Install

The easiest way is [`uvx`](https://docs.astral.sh/uv/) — it runs the package
in an isolated environment without a manual install step:

```bash
uvx deferno-mcp
```

Or install permanently:

```bash
pip install deferno-mcp
# or with uv:
uv pip install deferno-mcp
```

## Configure

Environment variables:

| Variable            | Default                   | Purpose                                                                       |
| ------------------- | ------------------------- | ----------------------------------------------------------------------------- |
| `DEFERNO_BASE_URL`  | `http://127.0.0.1:3000`   | URL of the Deferno backend HTTP API                                           |
| `DEFERNO_TOKEN`     | _(unset)_                 | Pre-existing bearer token; skips interactive login                            |
| `DEFERNO_USERNAME`  | _(unset)_                 | Auto-login on first authed tool call if no token is set                       |
| `DEFERNO_PASSWORD`  | _(unset)_                 | Paired with `DEFERNO_USERNAME`                                                |
| `DEFERNO_LOG_LEVEL` | `WARNING`                 | Python logging level                                                          |

If none of the auth env vars are set, the agent can still call the `login`
tool interactively.

## Client configuration snippets

### Claude Desktop / Claude Code

Add to your MCP client settings (`claude_desktop_config.json` on
Claude Desktop, or Claude Code's `mcpServers` config).

**Recommended — token auth (no password prompts):**

Get your token from the Deferno Settings page → "Copy API token", or from
browser dev tools: `localStorage.getItem("deferno_token")`.

```json
{
  "mcpServers": {
    "deferno": {
      "command": "uvx",
      "args": ["deferno-mcp"],
      "env": {
        "DEFERNO_BASE_URL": "https://deferno.work",
        "DEFERNO_TOKEN": "your-api-token-here"
      }
    }
  }
}
```

**Alternative — username/password auth:**

```json
{
  "mcpServers": {
    "deferno": {
      "command": "uvx",
      "args": ["deferno-mcp"],
      "env": {
        "DEFERNO_BASE_URL": "https://deferno.work",
        "DEFERNO_USERNAME": "your-user",
        "DEFERNO_PASSWORD": "your-password"
      }
    }
  }
}
```

### Cursor / Windsurf / Zed

Same shape — these clients all consume the MCP `stdio` transport. Point
them at the `deferno-mcp` command and set the same env vars.

### VS Code Copilot agent mode

In `.vscode/mcp.json`:

```json
{
  "servers": {
    "deferno": {
      "command": "deferno-mcp",
      "env": { "DEFERNO_BASE_URL": "http://127.0.0.1:3000" }
    }
  }
}
```

## Running the backend

The server talks to the Rust backend over HTTP. Start it first:

```bash
cd backend
cargo run
```

It listens on `:3000` and connects to Redis via `REDIS_URL`
(default `redis://127.0.0.1:6379/`).

## Development

Syntax / import sanity check:

```bash
python -c "from deferno_mcp.server import create_server; create_server()"
```

The server implementation is a single module (`src/deferno_mcp/server.py`)
plus a thin async HTTP client (`src/deferno_mcp/client.py`). Adding a new
tool is a matter of wrapping a new client method in an `@mcp.tool()`.
