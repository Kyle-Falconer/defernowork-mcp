# MCP auth E2E bottle — work-in-progress status

**As of:** 2026-04-28
**Branch:** `main` (commits `8e4089f` through `62c06f1`)
**Architecture authority:** [../../docs/superpowers/specs/2026-04-28-mcp-auth-e2e-bottle-design.md](../../docs/superpowers/specs/2026-04-28-mcp-auth-e2e-bottle-design.md) (v4)
**Plan authority:** [../../docs/superpowers/plans/2026-04-28-mcp-auth-e2e-bottle-plan.md](../../docs/superpowers/plans/2026-04-28-mcp-auth-e2e-bottle-plan.md) (v2)

## Architecture (one-line)

The bottle is a Dockerized test runner only. It drives the deployed
staging MCP at `https://app2.defernowork.com/mcp` against the staging
Zitadel at `https://auth2.defernowork.com`. **No local services**:
no local backend, no local Zitadel, no local Redis, no
`INTERNAL_SHARED_SECRET`, no `ZITADEL_CLIENT_ID/SECRET`, no admin PAT.

`.env.e2e` (gitignored) has:

```
ZITADEL_TEST_USER=
ZITADEL_TEST_PASSWORD=
ZITADEL_AUTH_URL=https://auth2.defernowork.com
MCP_SERVER_UNDER_TEST=https://app2.defernowork.com/mcp
ANTHROPIC_API_KEY=        # required for claude -p; runner has no host creds
ANTHROPIC_ENVIRONMENT=    # passed through, not strictly required
ANTHROPIC_API_KEY_NAME=   # passed through, not strictly required
```

## Done & committed

Phases 1, 3, 4, 5 are complete on `main`:

| Phase | Commit | What |
|---|---|---|
| 1 | `8e4089f` | `MCP_DEBUG_OAUTH`-gated exception class in `oauth_callback.py` 500 body, with TDD tests |
| — | `8a20f64` | `.env.e2e.example` + `.gitignore` for the bottle |
| — | `7000d24` | Design v4 (rewrite for staging-e2e) |
| — | `d09573b` | Plan v2 (rewrite for staging-e2e) |
| 3.1 | `4fbf8b7` | Runner Dockerfile, capture-url.sh, requirements.txt |
| 3.0 | `0f39f45` | `.gitattributes` to keep shell scripts/Dockerfiles in LF |
| 3.2 | `86a559b` | `docker-compose.e2e.yml` (single runner service) |
| 3.4 | `665d70c` | pyproject pytest exclusion + hello-world `test_self.py` + bottle `pytest.ini` |
| 4.1 | `d5a1a06` | `helpers/claude_code.py` (subprocess + wrapper-string parser, 5 unit tests pass) |
| 4.2 | `5519de4` | `helpers/browser_oauth.py` (Playwright Zitadel login) |
| 5 | `cda05d2` | `conftest.py` + full `test_self.py` (6/6 pre-flight probes pass against staging) |
| 3.x | `c073475` | `claude mcp add --transport http` fix + `ANTHROPIC_API_KEY` requirement |
| 3.x | `b2cde15` | Pass `ANTHROPIC_ENVIRONMENT` + `ANTHROPIC_API_KEY_NAME` through to runner |
| 3.x | `62c06f1` | Runner runs as `pwuser` (non-root); FIFO 0666 |

Verified working:
- Image builds; `claude --version` and Python deps OK inside runner.
- `pytest test_self.py -v` inside runner: 6/6 pass — including
  `test_staging_zitadel_reachable` and `test_staging_mcp_reachable`,
  which prove the runner can reach both staging services and that
  the MCP returns `200` on `/.well-known/oauth-authorization-server/mcp`.
- Default host `pytest` discovery still excludes the bottle (161/161
  passing on regular suite, no regressions).

## Phase 2 spike — partially answered

The plan's Phase 2 spike has 3 questions. Answers gathered so far:

- **Envelope shape (outcome A vs. B):** **A confirmed.** Even error
  envelopes from `claude -p --output-format json` carry the assistant
  result in `envelope["result"]`. The default `EXTRACTOR =
  _extract_from_result_field` in `helpers/claude_code.py` is correct.
- **Wrapper-string survival:** not yet observed in practice (first
  successful tool call hasn't happened — see blocker below).
- **Credential file shape:** **changed since the plan was drafted.**
  Claude Code 2.0.30 stores MCP config at `~/.claude.json`, NOT
  `~/.claude/mcp_credentials.json`. Path navigation:
  `data["projects"][cwd]["mcpServers"][server_name]`. The bearer
  storage location is unknown until a successful auth flow has
  populated it. `helpers/claude_code.py:CREDENTIAL_FILE` still points
  at the plan's stale path and needs adjusting once the auth flow
  works (or the helper should be reworked entirely; see blocker).

## Phase 6 — BLOCKED (architecture decision needed)

**Symptom:** `claude -p` against the unauth'd staging MCP hangs
silently in non-interactive mode. No stdout, no stderr, no FIFO
write, no debug output. Eventually exits 124 (timeout) or 0 with
empty body.

### What was tried

1. **`claude -p ... --output-format json`** (plain): timeout 124, empty.
2. **`claude -p ... --output-format stream-json --verbose --include-partial-messages`**:
   produces stream events showing the model trying to call **Bash**
   (`{"command": "deferno whoami"}`), not the MCP tool. The MCP tool is
   not enumerated because `mcp_servers` shows
   `{"name":"deferno","status":"needs-auth"}`. Bash then hits a
   permission denial: `"This command requires approval"` (recorded in
   `permission_denials`).
3. **`--dangerously-skip-permissions`** (root): Claude Code refuses to
   run with this flag as root for security reasons. Fixed by adding
   `USER pwuser` + `ENV HOME=/home/pwuser` to the Dockerfile (commit
   `62c06f1`).
4. **`--dangerously-skip-permissions` as pwuser**: still hangs silently,
   exits 124. No output even with `--debug` to stderr.
5. **Probing host paths:** `claude mcp list` correctly shows
   `Status: ⚠ Needs authentication`. The MCP IS registered. Just no
   way to drive auth from `-p` mode.

### Hypothesis (CONFIRMED 2026-04-28 via documentation research)

Claude Code's non-interactive mode (`-p`) does not have a code path
for initiating the OAuth dance for an unauth'd MCP server. The
`BROWSER` env-var hook (the bottle's whole `BROWSER → FIFO →
Playwright` premise) is never invoked. `claude` interactive mode
*does* drive the OAuth dance for stdio-transport servers; HTTP-transport
MCP OAuth appears to be broken even in interactive mode (see
[anthropics/claude-code#36307](https://github.com/anthropics/claude-code/issues/36307)).

### Research outcome (2026-04-28) — items 1, 2, 5 ruled out

`claude-code-guide` agent surveyed Claude Code's CLI reference, MCP
docs, headless docs, and authentication docs. Findings:

- **No `claude mcp auth <server>` subcommand.** The complete `mcp`
  subcommand list is `add`, `add-json`, `add-from-claude-desktop`,
  `list`, `get`, `remove`, `serve`, `reset-project-choices`. Filing
  this as a feature request is GitHub issue
  [#36307](https://github.com/anthropics/claude-code/issues/36307).
- **No env var, no CLI flag** opts `-p` into the OAuth dance. The
  headless docs explicitly state *"Bare mode skips OAuth and keychain
  reads."*
- **`--input-format stream-json` is read-only observation**, not a
  command channel. No documented event accepts a bearer token or
  completes an OAuth dance. Item #2 is dead.
- **Only documented CI pattern for OAuth-protected MCPs:** static
  bearer at config time via
  `claude mcp add --transport http <name> <url> --header "Authorization: Bearer $TOKEN"`.
- **Interactive mode is also reportedly broken** for HTTP MCP OAuth
  (issue #36307 reports the same `Needs authentication` status with
  no browser ever opening). This kills item #5 (PTY/expect) too —
  even an interactive driver wouldn't reach a working code path.
- **Related issues:**
  [#11585](https://github.com/anthropics/claude-code/issues/11585) (MCP
  servers requiring OAuth don't expose tools),
  [#42628](https://github.com/anthropics/claude-code/issues/42628)
  (hosted Claude Code OAuth flow doesn't complete).

**Net:** items 1, 2, 5 from "things worth trying next" are ruled out
by primary-source evidence. Items 3 (hybrid) and 4 (drop Claude Code)
remain. Architecture decision required.

### MCP supports DCR — simplifies bottle-side OAuth client

Reading [oauth_provider.py:60-72](../../src/defernowork_mcp/oauth_provider.py#L60-L72)
confirms the MCP implements **RFC 7591 Dynamic Client Registration**.
That removes the original "open question" about the OAuth client setup:

- The bottle does **not** need to reuse Claude Code's `client_id`.
- The bottle does **not** need a one-time Zitadel admin action to
  pre-register a client.
- The bottle just calls `POST /register` on the MCP at runtime,
  hands it whatever `redirect_uri` it likes (e.g.
  `http://localhost:8765/callback`), and gets back a fresh
  `client_id`. The MCP brokers the upstream Zitadel dance from there.

The original option-(a) "race condition" against a host-side Claude
Code is also a non-issue — the runner is its own network namespace,
so a localhost listener on `127.0.0.1:8765` inside the container
cannot collide with anything on the host. Both options collapse: DCR
is the canonical, container-isolated answer.

### Path 1 (hybrid) ≡ Path 4 with extra ceremony

Once Claude Code is reduced to "carry a static `Authorization: Bearer`
header that the bottle minted via its own OAuth dance," CC becomes a
passthrough HTTP client. The bottle does all the protocol work either
way. **The bearer in `--header` IS an OAuth access token** (RFC 6750
is just how every OAuth-protected resource server is consumed; "OAuth
vs. bearer token" is not a real distinction — bearers are the
*output* of OAuth). The only thing CC adds under Path 1 vs. Path 4 is
narrow **model-drift coverage**: "does Claude actually invoke the MCP
tool when prompted, does the wrapper-string survive."

**What Path 1 still tests (== Path 4):**
- Full RFC 6749 + RFC 7591 + PKCE dance against staging MCP.
- Real Zitadel form submission via Playwright.
- The MCP's `oauth_provider.py` end-to-end (DCR, authorize, OIDC
  callback handling, token exchange, refresh, revocation).
- `oauth_callback.py:20`'s OIDC callback route.
- Tool-call protocol (`tools/call` JSON-RPC) over a bearer.
- Logout server-side invalidation (the bearer minted by the bottle's
  dance gets revoked by the MCP's `logout` tool).

**What Path 1 does NOT test (and neither does Path 4):**
- Claude Code's *own* OAuth integration with HTTP MCP servers — the
  thing currently broken upstream per #36307. Until Anthropic ships a
  fix, no architecture testable from `-p` exercises this.

### Three-way operator decision

| | Coverage | Complexity | CC-OAuth-fix-future |
|---|---|---|---|
| **Path 1** (CC + static `--header`) | OAuth dance + MCP tool surface + thin model drift | High — bottle does OAuth + plants header + drives `claude -p` + parses envelope | Browser plumbing stays in place; flip back when #36307 ships |
| **Path 4** (drop CC, httpx direct) | OAuth dance + MCP tool surface | Low — bottle does OAuth + JSON-RPC directly | Re-add CC layer later as a follow-up |
| **Pause** (wait for Anthropic #36307) | Nothing | Zero | Re-evaluate when Anthropic ships |

Path 1 is honest about what it covers (MCP-side OAuth + model drift).
Path 4 is honest about what it covers (MCP-side OAuth only). Pause is
honest that there's a real gap until #36307 lands. **None of the
three tests CC's OAuth-against-HTTP-MCP integration today**, because
that code path doesn't work in any mode.

### Operator preferences captured this session

- **"Keep the browser plumbing for now."** The Dockerfile's FIFO
  + `capture-url.sh` + the `BROWSER` env var stay in place under
  whatever path is chosen. They're dormant under Path 1/4, but
  ready for the day Anthropic fixes #36307. Do **not** rip them out.
- **"I wanted OAuth, not bearer tokens."** Resolved: the bearer
  ending up in `--header` (Path 1) or in `httpx` (Path 4) is an OAuth
  access token from a real OAuth dance the bottle drives. There's no
  non-OAuth shortcut in either path.
- **"You give up too easily" pushback on Path 4** still on file from a
  previous session, but stated *before* primary-source confirmation
  that CC's OAuth in `-p` is non-functional. Operator now sees Path 1
  ≡ Path 4 + ceremony given that constraint, and the choice is open.

### Implementation sketch (applies to BOTH Path 1 and Path 4)

The bottle's OAuth client is the same code under either path:

```
helpers/oauth_client.py (new)
  discover()              -> fetch /.well-known/oauth-authorization-server/mcp
  register()              -> POST /register (DCR), get client_id
  build_authorize_url()   -> with PKCE verifier + state
  start_callback_server() -> threaded http.server on 127.0.0.1:8765,
                             returns Future[code]
  exchange_code()         -> POST /token, get access_token + refresh_token
  refresh()               -> POST /token grant_type=refresh_token
  revoke()                -> POST /revoke
  run_full_dance(playwright_context) -> ties (1)..(5) together,
                                        returns OAuthToken
```

Under **Path 1**, after `run_full_dance` returns the access token, the
bottle invokes `claude mcp add --transport http deferno $URL --header
"Authorization: Bearer $TOKEN"` and runs `claude -p` for the tool call.

Under **Path 4**, the bottle invokes `tools/call` JSON-RPC directly
with `Authorization: Bearer $TOKEN`. No `claude -p` involvement; no
wrapper-string contract; assertions are on raw JSON-RPC results.

Scenario shapes under each path:

- **#1 happy path:** Path 1 = whoami via `claude -p` with header;
  Path 4 = `tools/call whoami` via httpx.
- **#2 whoami identity:** same shapes, assert on identity field.
- **#3 logout invalidates token:** dance → whoami(✓) → logout →
  whoami(✗). Under Path 1, the second whoami's failure surfaces as an
  error envelope or model give-up — assertion is "does not silently
  return whoami JSON." Under Path 4, the second whoami is a direct
  401 — assertion is exactly the spec's original 401.
- **#4 logout invalidates Zitadel session:** dance → logout → re-dance,
  Playwright asserts the login form is presented (no SSO short-circuit).
  This scenario is identical under Path 1 and Path 4 because the
  Zitadel session state is observed via the bottle's own Playwright,
  not via CC.

### Awaiting operator decision

Path 1, Path 4, or Pause. Then implementation proceeds.

## Hard constraints (don't repeat past mistakes)

- **Never mount `~/.claude/.credentials.json` (or any Claude Code
  subscription session) into the container.** Anthropic actively bans
  accounts that do this; it's a ToS violation. The runner uses
  `ANTHROPIC_API_KEY` only. (Saved as `feedback_no_credential_mount.md`
  in auto-memory.)
- The plan's spec is authoritative for assertion shapes and scenario
  semantics. The plan's *mechanism* details (e.g., credential file
  path) drift with Claude Code versions and need to be re-verified
  per major bump.
- The bottle requires `ANTHROPIC_API_KEY` with credit to run. The
  free-tier rate-limit dashboard does not bypass the credit-balance
  check.

## Personal settings change (host side)

I added a host-side permission rule to my Claude Code's
`.claude/settings.local.json` (gitignored, workspace level) so I can
run bottle commands without prompts:

```json
"Bash(docker compose -f docker-compose.e2e.yml *)"
```

This stays personal and isn't committed to the repo.

## Pickup checklist for next session

1. Read this doc, design v4, plan v2.
2. Check the operator's response to the Path 1 / Path 4 / Pause
   decision (under "Three-way operator decision" above).
   - If **Path 1** chosen: implement `helpers/oauth_client.py` per the
     "Implementation sketch" section, wire it into a per-test fixture
     in `conftest.py`, register MCP with CC via `--header` (drop the
     session-scoped `_register_mcp` fixture — registration becomes
     per-test because each test mints its own bearer). Update scenario
     #3 assertion to "does not silently return whoami JSON."
   - If **Path 4** chosen: implement `helpers/oauth_client.py` and a
     thin `helpers/mcp_jsonrpc.py` for `tools/call`. Drop
     `helpers/claude_code.py` from the runtime path (keep the unit
     tests for the wrapper parser; they may matter again later).
     Drop `BROWSER` plumbing from compose only — *keep* the Dockerfile
     bits per operator preference.
   - If **Pause** chosen: do nothing in code; subscribe to
     [claude-code#36307](https://github.com/anthropics/claude-code/issues/36307)
     and revisit when it ships.
3. **Do not** rip out the BROWSER hook + FIFO + `capture-url.sh` from
   the Dockerfile under any path. Operator wants them dormant-but-ready
   for the day #36307 ships.
4. The MCP supports DCR (RFC 7591); the bottle does not need any
   pre-coordination with staging Zitadel. `client_id` is minted at
   runtime via `POST /register` against the MCP. See `oauth_provider.py:60-72`.
