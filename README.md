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
| `start_auth`        | Begin browser-based login (returns a URL + session ID)      |
| `complete_auth`     | Exchange the browser code for a saved token                 |
| `logout`            | Invalidate session and remove saved credentials             |
| `whoami`            | Return the currently authenticated user                     |
| `list_tasks`        | List every task owned by the authenticated user             |
| `get_task`          | Fetch a single task by UUID                                 |
| `create_task`       | Create a new task (optionally nested under a parent)        |
| `update_task`       | Patch any mutable field (title, description, status, mood…) |
| `set_task_status`   | Convenience wrapper for `open`/`in-progress`/`done`/…       |
| `move_task`         | Reparent or reorder a task in the hierarchy                 |
| `split_task`        | Decompose a task into two child tasks                       |
| `fold_task`         | Insert a next-step task into the sibling chain              |
| `merge_task`        | Roll a parent's active children back into the parent        |
| `get_daily_plan`    | Today's curated daily plan (recurring + carried forward)    |
| `add_to_plan`       | Add a task to the daily plan by UUID                        |
| `remove_from_plan`  | Remove a task from the daily plan                           |
| `reorder_plan`      | Replace the daily plan ordering                             |
| `get_mood_history`  | Mood log for finished tasks                                 |

**Resources** (readable by MCP clients that index resources)

| URI                                | Content                        |
| ---------------------------------- | ------------------------------ |
| `defernowork://tasks`              | All tasks for the current user  |
| `defernowork://tasks/plan`         | Today's curated daily plan      |
| `defernowork://tasks/mood-history` | Mood log for finished tasks     |
| `defernowork://task/{task_id}`     | A single task by UUID           |

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

## Authenticate

Run the one-time auth command:

```bash
defernowork-mcp auth --base-url https://deferno.work
```

This opens a browser-based login flow:

1. A URL is printed — open it in your browser
2. Sign in (or approve if already signed in)
3. A short code is shown — paste it back into the terminal

Your token is saved to `~/.config/defernowork/credentials.json` and
loaded automatically on future runs. No env vars needed.

Alternatively, set `DEFERNO_TOKEN` as an environment variable to skip the
interactive flow (useful for CI or containers).

## Authentication flow

The auth flow works the same whether triggered from the CLI
(`defernowork-mcp auth`) or from within an agent (the `start_auth` /
`complete_auth` MCP tools). Three backend endpoints coordinate the
handshake:

```
MCP / CLI                  Backend                     Browser
  |                          |                           |
  |-- POST /auth/cli/init -->|                           |
  |<-- {session_id, url} ----|                           |
  |                          |                           |
  |  (user opens url)        |                           |
  |                          |<--- GET /cli-auth?s=...---|
  |                          |                           |
  |                          |   (user logs in if needed)|
  |                          |                           |
  |                          |<- POST /auth/cli/approve -|
  |                          |   {session_id}            |
  |                          |-- {code} ---------------->|
  |                          |   (browser shows code)    |
  |                          |                           |
  |  (user pastes code)      |                           |
  |                          |                           |
  |-- POST /auth/cli/verify->|                           |
  |   {session_id, code}     |                           |
  |<-- {token, user} --------|                           |
  |                          |                           |
  |  (token saved to disk)   |                           |
```

### Backend endpoints

| Endpoint | Auth | Request | Response |
| --- | --- | --- | --- |
| `POST /auth/cli/init` | none | `{}` | `{session_id: string, auth_url: string}` |
| `POST /auth/cli/approve` | Bearer | `{session_id: string}` | `{code: string}` |
| `POST /auth/cli/verify` | none | `{session_id: string, code: string}` | `{token: string, user: {id, username, …}}` |

**`cli/init`** creates a pending CLI session in Redis with a short TTL
(~10 minutes) and returns a URL the user should open in their browser.

**`cli/approve`** is called by the frontend after the user is logged in.
It creates a **new** backend session for the CLI (including the cached
DEK so encrypted task data remains accessible), generates a short
one-time code, and stores both in the CLI session record. The browser
session and CLI session are independent — logging out of one does not
affect the other.

**`cli/verify`** is called by the MCP server / CLI. It looks up the
CLI session, verifies the code, returns the session token and user info,
and deletes the CLI session record from Redis.

### Token resolution order

When the MCP server needs a token it checks, in order:

1. Per-request `Authorization: Bearer` header (HTTP transport only)
2. `DEFERNO_TOKEN` environment variable
3. Saved credentials at `~/.config/defernowork/credentials.json`

### Agent-driven flow

When an agent (Claude Code, Cursor, etc.) calls any tool and gets a 401,
the server instructions tell it to:

1. Call `start_auth` — returns `{auth_url, session_id}`
2. Show the URL to the user and ask them to sign in
3. Ask the user to paste the code shown in their browser
4. Call `complete_auth(session_id, code)` — saves credentials to disk

All subsequent tool calls work automatically, including across restarts.

### Where to implement

**Backend** (`Deferno/backend/src/main.rs` + `repository.rs`):
- Payload/response structs alongside the existing auth types
- `cli_init`, `cli_approve`, `cli_verify` handler functions
- `cli/init` and `cli/verify` as public routes; `cli/approve` behind `require_auth`
- CLI session CRUD in the repository layer (Redis key `cli_session:{id}`, TTL 10 min)

**Frontend** (`Deferno/webui/`):
- New page `src/pages/CliAuth.tsx` at route `/cli-auth?session=<id>`
- If not logged in: redirect to login, then back to `/cli-auth`
- If logged in: show "Approve this CLI login?" button
- On approve: call `POST /auth/cli/approve`, display the code

## Configure

Environment variables:

| Variable            | Default                 | Purpose                                        |
| ------------------- | ----------------------- | ---------------------------------------------- |
| `DEFERNO_BASE_URL`  | `http://127.0.0.1:3000` | URL of the Deferno backend HTTP API            |
| `DEFERNO_TOKEN`     | _(unset)_               | Pre-existing bearer token; skips browser login |
| `DEFERNO_LOG_LEVEL` | `WARNING`               | Python logging level                           |

## Client configuration snippets

### Claude Desktop / Claude Code

Add to your MCP client settings (`claude_desktop_config.json` on
Claude Desktop, or Claude Code's `mcpServers` config):

```json
{
  "mcpServers": {
    "deferno": {
      "command": "uvx",
      "args": ["deferno-mcp"],
      "env": {
        "DEFERNO_BASE_URL": "https://deferno.work"
      }
    }
  }
}
```

The agent will walk you through browser-based auth on first use.
If you prefer to skip the interactive flow, add `"DEFERNO_TOKEN": "..."`.

### Cursor / Windsurf / Zed

Same shape — these clients all consume the MCP `stdio` transport. Point
them at the `deferno-mcp` command and set `DEFERNO_BASE_URL`.

### VS Code Copilot agent mode

In `.vscode/mcp.json`:

```json
{
  "servers": {
    "deferno": {
      "command": "deferno-mcp",
      "env": { "DEFERNO_BASE_URL": "https://deferno.work" }
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
python -c "from defernowork_mcp.server import create_server; create_server()"
```

The server implementation is a single module (`src/defernowork_mcp/server.py`)
plus a thin async HTTP client (`src/defernowork_mcp/client.py`) and
credential storage (`src/defernowork_mcp/credentials.py`). Adding a new
tool is a matter of wrapping a new client method in an `@mcp.tool()`.
