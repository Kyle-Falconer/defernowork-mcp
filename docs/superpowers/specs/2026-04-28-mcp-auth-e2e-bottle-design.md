# MCP auth E2E bottle — design

> # ⛔ STOP — DO NOT USE THIS DOCUMENT FOR CURRENT WORK
>
> **THIS SPEC IS FROZEN AND DOES NOT REFLECT CURRENT REALITY.**
>
> The architecture described here assumes Claude Code's `claude -p`
> mode can drive an OAuth dance for an HTTP-transport MCP server.
> **It cannot.** That capability is broken upstream — see
> [anthropics/claude-code#36307](https://github.com/anthropics/claude-code/issues/36307).
> All scenario flows in this document depend on that broken capability.
>
> **The E2E bottle work is on hold pending an operator decision** and
> potentially an upstream fix from Anthropic.
>
> **DO NOT:**
> - Implement scenarios from this spec.
> - Treat this as the source of truth for "what the bottle does today."
> - Use this as a planning input for follow-up work in `defernowork-mcp`.
>
> **The current source of truth is** [`tests/e2e-bottle/WIP.md`](../../../tests/e2e-bottle/WIP.md)
> (especially the "Three-way operator decision" section). Read that
> first; only return to this spec if the operator explicitly directs
> you to **resurrect the E2E bottle feature**.
>
> When and if the bottle is resurrected, this spec needs a new version
> (v5+) that reflects whichever path the operator chose
> (Path 1 / Path 4 / Pause-and-wait-for-Anthropic). Until then, treat
> this file as historical.

---

**Status:** v4 — staging-end-to-end architecture (supersedes v1–v3) —
**SUPERSEDED IN PRACTICE** by the WIP doc's blocker analysis.
**Date:** 2026-04-28
**Repo affected:** `defernowork-mcp` (test harness only)

## Goal

Regression coverage for the deployed Deferno MCP's OAuth-dance + tool-call
surface, exercised end-to-end against the live staging stack. The bottle
catches drift in the OAuth flow and tool surface that unit tests cannot
see (model behavior in `claude -p`, Zitadel session handling, OIDC
redirect handling, real bearer-token issuance and consumption).

## Architecture

The bottle is a **test runner only** — there are no locally-built
services. Everything the bottle exercises is the real staging deployment:

```
┌─ bottle (test runner) ─────────┐         ┌─ staging ──────────────────┐
│  pytest + Playwright           │  HTTPS  │  auth2.defernowork.com     │
│  Claude Code (`claude -p`)     │ ──────► │   (Zitadel)                │
│                                │         │                            │
│  driving:                      │         │  app2.defernowork.com/mcp  │
│    1. login via Playwright     │ ──────► │   (the MCP under test)     │
│    2. tool calls via claude -p │         │                            │
│                                │         │  → Deferno backend         │
│                                │         │    (transitive only;       │
│                                │         │     not addressed by tests)│
└────────────────────────────────┘         └────────────────────────────┘
```

What the bottle owns:

- **The runner container.** A digest-pinned Playwright/Python image with
  Claude Code, Playwright, pytest installed at image-build time. The
  bottle is reproducible because the runner is reproducible.
- **The test harness** at `defernowork-mcp/tests/e2e-bottle/` — pytest
  scenarios, Playwright login helper, `claude -p` subprocess wrapper.

What the bottle does NOT own:

- No Zitadel deployment. We use staging at `auth2.defernowork.com`.
- No backend deployment. The staging MCP at
  `app2.defernowork.com/mcp` already has its backend wired up; the
  bottle never addresses the backend directly.
- No Redis. The staging MCP has its own Redis; the bottle does not
  touch it.
- No `INTERNAL_SHARED_SECRET`, no `ZITADEL_CLIENT_ID/SECRET`, no
  `ZITADEL_ADMIN_PAT`. Those live in staging's config, not in the
  bottle's `.env.e2e`.

## Configuration

`.env.e2e` (gitignored) has four values:

```
ZITADEL_TEST_USER=<login name of no-MFA test user>
ZITADEL_TEST_PASSWORD=<password>
ZITADEL_AUTH_URL=https://auth2.defernowork.com
MCP_SERVER_UNDER_TEST=https://app2.defernowork.com/mcp
```

The example (`tests/e2e-bottle/.env.e2e.example`) is checked in with
the URLs filled and the credential fields blank.

## Test driver flow (per scenario)

1. **Configure Claude Code** to know about the staging MCP. Either
   `claude mcp add deferno $MCP_SERVER_UNDER_TEST` once per test session,
   or pre-bake the config in the runner image.
2. **Run a `claude -p` prompt** that calls a tool, with a paired-marker
   wrapper-string contract (`<<<DEFERNO_E2E_BEGIN>>> ... <<<DEFERNO_E2E_END>>>`)
   that pins the JSON payload across model output drift.
3. **Claude Code initiates OAuth** because no bearer is yet cached for
   the staging MCP. Claude Code opens a browser via its `BROWSER` env
   hook; the bottle's hook script captures the authorize URL into a FIFO
   instead of opening anything.
4. **The test reads the URL** from the FIFO and drives Playwright
   against `auth2.defernowork.com`: fill loginName, fill password, submit.
5. **The OIDC redirect** lands at the staging MCP's
   `/mcp/oauth/oidc-callback`, which redirects back to Claude Code's
   registered callback. Claude Code stores the resulting bearer in its
   credential file.
6. **Claude Code completes the tool call** with the now-cached bearer.
   The tool's JSON response arrives wrapped in the contract markers.
7. **The test parses the wrapper** and asserts on the JSON.

## Scenarios (v1)

Four scenarios. Each one is a single pytest function with the
session-scoped runner fixtures plus a per-test `BrowserContext` for
isolation.

### 1. `test_happy_path` — full OAuth dance + first tool call

Run a `claude -p whoami` prompt against a freshly-launched browser
context with no credentials cached. Expected:

- Authorize URL is captured by the BROWSER hook within 30s.
- Playwright completes the Zitadel form submission.
- The MCP callback returns 302 (not 500).
- `claude -p` exits with code 0.
- The wrapper-string region parses to JSON containing the test user's
  identity.

This is the gating "if this fails, nothing else can pass" scenario.

### 2. `test_whoami_returns_user_identity` — tool call returns the right user

Same as #1 but with explicit assertion that the parsed JSON's
identity field matches `ZITADEL_TEST_USER`. This catches the case
where the token is good but the MCP is talking to the wrong backend
or the wrong user lookup.

(Replaces the original spec's "backend round-trip evidence" scenario.
That one required scraping backend access logs which the bottle can't
see in this architecture; asserting on the response value is the
strongest assertion available.)

### 3. `test_logout_invalidates_token_server_side`

After a successful login + whoami, call the MCP's `logout` tool. Then
attempt another `whoami`. Expected:

- Without re-login, the second whoami either errors with 401, or
  triggers a fresh OAuth dance (Claude Code observes the token was
  invalidated and re-authorizes).

The exact observable is whichever of those two Claude Code does — the
spike (Phase 2) records which.

### 4. `test_logout_invalidates_zitadel_session` (simplified)

After login, log out via the MCP `logout` tool, then in the **same
BrowserContext** re-attempt the OAuth dance. Expected:

- Zitadel does NOT reuse the previous session (no SSO short-circuit
  to instant redirect). The login form is presented again.

If Zitadel auto-redirected (i.e., the session WAS retained), logout did
not invalidate the upstream session — failure.

The spec's original scenario for this required an admin PAT to query
Zitadel sessions. The simplified form here doesn't need any admin
credentials; it observes the same property indirectly via "did I have
to log in again?" That is sufficient for v1.

## Scenarios deferred / dropped vs. original spec

| Original | Verdict |
|---|---|
| #5 token refresh | **Deferred.** Requires a forced-expiry mechanism. In the local-stack design the bottle mutated MCP's Redis; against staging that is not available. Could be revisited if staging gains a debug endpoint to age tokens, or by waiting for natural expiry (slow). |
| #6 missing `INTERNAL_SHARED_SECRET` failure-injection | **Dropped.** Cannot toggle staging's secret. |
| #7 malformed backend response failure-injection | **Dropped.** Cannot inject into the staging backend. |

The `MCP_DEBUG_OAUTH` gate added to `oauth_callback.py` (commit
`8e4089f`) is preserved — it's a small, env-gated debug surface that
costs nothing to keep and may be useful for future scenarios. It has
no consumer in v1.

## Test isolation (the F7 problem in the new architecture)

Sequential interference is much smaller here than in the original spec
because there is no local Redis to leak state across tests. The
remaining sources of cross-test contamination:

- **Claude Code's MCP credential file.** Cached bearer token; if test 1
  succeeds and test 2 starts with a populated cache, test 2 doesn't
  exercise the OAuth dance. Mitigation: per-test fixture deletes (or
  moves aside) the credential file before the test runs.
- **BrowserContext cookies.** Zitadel sets a session cookie. If two
  tests share a context, test 2 may auto-login. Mitigation: fresh
  Playwright `BrowserContext` per test.
- **Zitadel side: lingering active sessions for the test user.** The
  original spec mitigated this by terminating sessions via admin PAT.
  Without that PAT, lingering sessions are a real possibility — but
  Zitadel's own SSO-vs-fresh-login behavior is largely controlled by
  cookies, which the per-test BrowserContext resets. Tests that
  specifically check session reuse (#4) are the only ones sensitive to
  this; #4's assertion is "logout caused fresh login to be required,"
  which is robust to whether *some other* session existed.

The bottle does not provide perfect isolation — running it twice in
quick succession against the same test user CAN occasionally hit a
prior session. If that becomes a flake source, the mitigation is
either (a) per-run user namespacing (one test user per run) or (b)
adding admin-PAT-based session termination back. Neither is needed for
v1.

## Wrapper-string contract

To make tool-call assertions robust against `claude -p`'s envelope
shape and against the model's tendency to wrap JSON in conversational
prose, every test prompt asks the model to surround the tool's JSON
output with paired markers:

```
<<<DEFERNO_E2E_BEGIN>>>
<the tool's raw JSON>
<<<DEFERNO_E2E_END>>>
```

The test harness searches the entire envelope for exactly one such
pair and parses its inner content as JSON. If zero pairs are found
or more than one are found, the test raises `ModelFormatDeviation` —
that is the model's contract violation, not a product bug.

The exact envelope field where the markers land is determined by the
spike (Phase 2). The two known outcomes:

- **A:** assistant final message lives in the JSON envelope's `result`
  field. Default extractor reads that.
- **B:** content lives in streaming events. Extractor reads
  `--output-format stream-json` and concatenates `tool_result` events.

The spike picks one and the helper module flips a constant.

## Out of scope

- **Failure-injection** of staging components (covered above).
- **Mutating staging MCP's Redis** for forced-expiry tests.
- **Performance / load.** This is a correctness test, single-user,
  sequential.
- **CI integration.** v1 is operator-run locally. CI integration is a
  follow-up once the bottle is stable and we know its real flake rate.

## What "done" means for v1

- All four scenarios pass on a clean run from an operator's machine.
- A single failure produces an artifact (Playwright trace, Claude Code
  envelope JSON, captured FIFO content) sufficient to debug without
  rerunning.
- A README at `tests/e2e-bottle/README.md` walks an operator from a
  freshly-cloned repo to a green run.
