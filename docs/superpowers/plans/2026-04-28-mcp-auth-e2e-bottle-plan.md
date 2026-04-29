# MCP auth E2E bottle — implementation plan (v2)

> # ⛔ STOP — DO NOT EXECUTE THIS PLAN
>
> **THIS PLAN IS FROZEN. DO NOT EXECUTE THE REMAINING PHASES.**
>
> Phases 0, 1, 3, 4, 5 of this plan landed on `main`. **Phase 6
> (scenarios) is permanently blocked under this plan's architecture**
> because it relies on `claude -p` driving an OAuth dance — a
> capability that does not exist in Claude Code today
> (see [anthropics/claude-code#36307](https://github.com/anthropics/claude-code/issues/36307)).
>
> **The E2E bottle work is on hold pending an operator decision.**
>
> **DO NOT:**
> - Continue executing Phase 6 or Phase 7 of this plan.
> - Take this plan as input to `superpowers:executing-plans` or
>   `superpowers:subagent-driven-development`.
> - Treat the spec this plan references as a viable target.
>
> **The current source of truth is** [`tests/e2e-bottle/WIP.md`](../../../tests/e2e-bottle/WIP.md)
> (especially the "Three-way operator decision" section). Read that
> first; only return to this plan if the operator explicitly directs
> you to **resurrect the E2E bottle feature**, and even then a new
> plan (v3+) reflecting the chosen path (Path 1 / Path 4 / Pause)
> will need to be written before any further code is shipped.

---

> **For agentic workers (HISTORICAL — see warning above):**
> REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans`. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Build the staging-end-to-end test bottle described in
`docs/superpowers/specs/2026-04-28-mcp-auth-e2e-bottle-design.md` (v4).
The bottle is a Dockerized test runner that drives the deployed staging
MCP at `app2.defernowork.com/mcp` through Claude Code, with Playwright
handling the Zitadel login form at `auth2.defernowork.com`.

**Architecture:** No local services. One container service in compose
(the runner). Tests live at `defernowork-mcp/tests/e2e-bottle/`. Pytest
default discovery excludes the bottle so regular `pytest` runs are
unaffected.

**Tech Stack:** Python 3.11+ (pytest, pytest-asyncio, httpx,
playwright/python), Node 20 + `@anthropic-ai/claude-code`
(version-pinned), Docker Compose.

**Spec authority:** When in doubt about an assertion shape, defer to
the spec.

---

## Phase status snapshot

| Phase | Status |
|---|---|
| 0 — Operator preconditions | Mostly done (`.env.e2e` filled in by operator) |
| 1 — `MCP_DEBUG_OAUTH` gate | ✅ shipped on `main` at commit `8e4089f` |
| 2 — Spike: `claude -p` against staging MCP | Pending |
| 3 — Bottle infrastructure | Pending |
| 4 — Helpers | Pending |
| 5 — Conftest + test_self.py (full) | Pending |
| 6 — Scenarios (4 tests) | Pending |
| 7 — README + smoke run | Pending |

---

## Phase 0 — Operator preconditions

### Task 0.1: Confirm test user and credentials

**Files:** none (operator action against `auth2.defernowork.com`).

- [ ] **Step 1:** Confirm a test user exists in `auth2` with no MFA and
  no password-change-on-login flag. Record the username and password.
- [ ] **Step 2:** Confirm the user can complete a manual login at
  `https://auth2.defernowork.com/oauth/v2/authorize?client_id=<staging-mcp-client>&...`
  (i.e., that the user can actually log in via the staging OAuth flow).
- [ ] **Step 3:** Fill `defernowork-mcp/tests/e2e-bottle/.env.e2e`
  (gitignored) with `ZITADEL_TEST_USER`, `ZITADEL_TEST_PASSWORD`,
  `ZITADEL_AUTH_URL=https://auth2.defernowork.com`, and
  `MCP_SERVER_UNDER_TEST=https://app2.defernowork.com/mcp`.

**No commit.** This phase is external setup.

---

## Phase 1 — `MCP_DEBUG_OAUTH` gate

✅ **Shipped on `main`** at commit `8e4089f`. Source edit at
`src/defernowork_mcp/oauth_callback.py:48-54`; tests at
`tests/test_oauth_callback_debug_format.py`. The gate is preserved
even though v1 has no consumer scenario for it (scenarios #6/#7 from
the original spec were dropped).

No further action.

---

## Phase 2 — Spike: `claude -p` against staging MCP

The spike answers two questions before any helper code lands:

1. **Where in `claude -p`'s envelope does the assistant's final message
   live?** (Outcome A: `result` field. Outcome B: streaming events.)
2. **Do the wrapper-string markers survive verbatim** through the model?
   (Outcome A/B: yes; outcome C: model wraps JSON in prose — bottle
   premise broken, escalate.)

Plus one secondary question:

3. **What is the path and JSON shape of Claude Code's MCP credential
   file** after a successful login? Helpers need this to clear/move it
   between tests.

### Task 2.1: Run the spike, record results

**Files:**
- Create: `defernowork-mcp/docs/superpowers/spikes/2026-04-28-bottle-spike-results.md`
- Create: `tmp/spike-claude-p/` (scratch; gitignored)

- [ ] **Step 1:** Install `@anthropic-ai/claude-code` at the version
  the runner image will pin. Use the latest stable for now; bump cadence
  is documented in Phase 7's README.
- [ ] **Step 2:** `claude mcp add deferno-staging https://app2.defernowork.com/mcp`
  (or whatever subcommand the installed Claude Code version uses).
- [ ] **Step 3:** Run a wrapper-marker prompt against the staging MCP:

```bash
claude -p 'Call the deferno-staging whoami tool. Output its raw JSON
response wrapped EXACTLY between the markers shown, on their own lines,
with no commentary before, between, or after:

<<<DEFERNO_E2E_BEGIN>>>
<the tool'"'"'s raw JSON, no surrounding prose>
<<<DEFERNO_E2E_END>>>' --output-format json > tmp/spike-claude-p/run1.json
```

This will trigger the OAuth dance the first time. Complete it manually
in the browser the first time.

- [ ] **Step 4:** Inspect `run1.json`. Identify which envelope field
  the assistant's final message lives in. Identify whether the markers
  and tool JSON survived verbatim. Re-run 3–5 times to check for
  variance.

- [ ] **Step 5:** After step 4 succeeds, find Claude Code's MCP
  credential file. Likely paths:
  `~/.claude/mcp_credentials.json`,
  `~/Library/Application Support/Claude/...`,
  or a per-server subdirectory under `~/.claude/`. Open it; identify the
  JSON shape (where the bearer lives).

- [ ] **Step 6:** Record outcomes in `spike-results.md`:
  - Section "envelope shape": A or B.
  - Section "wrapper survival": yes / no / drift example.
  - Section "credential file": path, JSON path to the bearer
    (e.g., `data["mcpServers"]["deferno-staging"]["bearer"]`).

- [ ] **Step 7:** Commit.

```bash
cd defernowork-mcp
git add docs/superpowers/spikes/2026-04-28-bottle-spike-results.md
git commit -m "docs: bottle spike results (claude -p envelope + credential file)"
```

If outcome C (model refuses or wraps in prose) — **stop**. Escalate to
the operator with the captured envelope. Phases 4+ depend on A or B.

---

## Phase 3 — Bottle infrastructure

### Task 3.1: `Dockerfile.e2e-runner`

**Files:**
- Create: `defernowork-mcp/tests/e2e-bottle/runner/Dockerfile`
- Create: `defernowork-mcp/tests/e2e-bottle/runner/capture-url.sh`
- Create: `defernowork-mcp/tests/e2e-bottle/runner/requirements.txt`

- [ ] **Step 1:** Identify the digest of the current
  `mcr.microsoft.com/playwright/python:v1.<latest>-jammy`:

```bash
docker pull mcr.microsoft.com/playwright/python:v1.47.0-jammy
docker inspect --format='{{index .RepoDigests 0}}' \
    mcr.microsoft.com/playwright/python:v1.47.0-jammy
```

- [ ] **Step 2:** Write `runner/Dockerfile`:

```dockerfile
FROM mcr.microsoft.com/playwright/python@sha256:<paste-digest>

# Node 20 (for @anthropic-ai/claude-code)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y nodejs

ARG CLAUDE_CODE_VERSION=1.0.0
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY capture-url.sh /usr/local/bin/capture-url.sh
RUN chmod +x /usr/local/bin/capture-url.sh

# FIFO for the BROWSER hook. Pre-create at image-build time so it
# exists at container start. Do NOT add tmpfs:[/tmp] to the runner
# service — it would shadow this FIFO.
RUN mkfifo /tmp/auth-url.fifo || true

WORKDIR /work
CMD ["pytest", "/work", "-v"]
```

- [ ] **Step 3:** Write `runner/capture-url.sh`:

```bash
#!/bin/sh
# capture-url.sh — Claude Code's BROWSER hook. Writes the URL it would
# have opened into a FIFO that the test fixture reads.
echo "$1" > /tmp/auth-url.fifo
exit 0
```

- [ ] **Step 4:** Write `runner/requirements.txt`:

```
pytest>=8
pytest-timeout>=2
pytest-asyncio>=0.23
playwright>=1.47
httpx>=0.27
```

- [ ] **Step 5:** Commit (build verification happens in Task 3.3).

```bash
cd defernowork-mcp
git add tests/e2e-bottle/runner/
git commit -m "feat(e2e-bottle): runner Dockerfile + capture-url.sh + requirements"
```

### Task 3.2: `docker-compose.e2e.yml`

**Files:**
- Create: `defernowork-mcp/tests/e2e-bottle/docker-compose.e2e.yml`

- [ ] **Step 1:** Single-service compose. No network plumbing needed
  (the runner reaches staging via the host's DNS and outbound
  network).

```yaml
services:
  e2e-runner:
    build:
      context: ./runner
      dockerfile: Dockerfile
    environment:
      ZITADEL_TEST_USER: ${ZITADEL_TEST_USER}
      ZITADEL_TEST_PASSWORD: ${ZITADEL_TEST_PASSWORD}
      ZITADEL_AUTH_URL: ${ZITADEL_AUTH_URL}
      MCP_SERVER_UNDER_TEST: ${MCP_SERVER_UNDER_TEST}
      BROWSER: /usr/local/bin/capture-url.sh
    volumes:
      - ../e2e-bottle:/work:ro
      - ./artifacts:/artifacts
    profiles: ["runner"]
```

- [ ] **Step 2:** Validate compose syntax:

```bash
cd defernowork-mcp/tests/e2e-bottle
docker compose -f docker-compose.e2e.yml --env-file .env.e2e config > /dev/null
```

- [ ] **Step 3:** Commit.

```bash
cd defernowork-mcp
git add tests/e2e-bottle/docker-compose.e2e.yml
git commit -m "feat(e2e-bottle): docker-compose with single runner service"
```

### Task 3.3: Build and smoke-test the runner image

**Files:** none (shell verification only).

- [ ] **Step 1:** Build:

```bash
cd defernowork-mcp/tests/e2e-bottle
docker compose -f docker-compose.e2e.yml --env-file .env.e2e build e2e-runner
```

- [ ] **Step 2:** Smoke-test installed binaries:

```bash
docker compose -f docker-compose.e2e.yml --env-file .env.e2e \
  run --rm e2e-runner \
  bash -c 'claude --version && python -c "import pytest, playwright, httpx; print(\"ok\")"'
```

Expected: Claude Code version line + `ok`.

If this fails, fix the Dockerfile in Task 3.1 and re-run before
moving on.

### Task 3.4: Pyproject pytest exclusion + hello-world `test_self.py`

**Files:**
- Modify: `defernowork-mcp/pyproject.toml`
- Modify: `defernowork-mcp/tests/conftest.py`
- Create: `defernowork-mcp/tests/e2e-bottle/__init__.py` (empty)
- Create: `defernowork-mcp/tests/e2e-bottle/test_self.py`

- [ ] **Step 1:** Add to `[tool.pytest.ini_options]` in `pyproject.toml`:

```toml
norecursedirs = ["tests/e2e-bottle", ".git", ".venv"]
```

- [ ] **Step 2:** Append to `tests/conftest.py`:

```python
collect_ignore = ["e2e-bottle"]
```

- [ ] **Step 3:** Create `tests/e2e-bottle/__init__.py` empty.

- [ ] **Step 4:** Create `tests/e2e-bottle/test_self.py` hello-world:

```python
"""Bottle self-test — pre-flight probes that gate scenario execution.

Phase 5 fills this out. This stub confirms pytest runs inside the runner.
"""

def test_pytest_runs_in_runner():
    assert 1 + 1 == 2
```

- [ ] **Step 5:** Verify exclusion from default discovery:

```bash
cd defernowork-mcp
pytest --collect-only 2>&1 | grep -c e2e-bottle
```

Expected: `0`.

- [ ] **Step 6:** Run hello-world from inside the runner:

```bash
cd defernowork-mcp/tests/e2e-bottle
docker compose -f docker-compose.e2e.yml --env-file .env.e2e \
  run --rm e2e-runner pytest /work/test_self.py -v
```

Expected: 1 passed.

- [ ] **Step 7:** Commit.

```bash
cd defernowork-mcp
git add pyproject.toml tests/conftest.py \
        tests/e2e-bottle/__init__.py tests/e2e-bottle/test_self.py
git commit -m "feat(e2e-bottle): pytest exclusion + hello-world test_self.py"
```

---

## Phase 4 — Helpers

### Task 4.1: `helpers/claude_code.py` (subprocess + wrapper-string parser + bearer capture)

**Files:**
- Create: `defernowork-mcp/tests/e2e-bottle/helpers/__init__.py` (empty)
- Create: `defernowork-mcp/tests/e2e-bottle/helpers/claude_code.py`
- Create: `defernowork-mcp/tests/e2e-bottle/helpers/test_claude_code_parser.py`

- [ ] **Step 1: Write the parser unit test FIRST (TDD).**

```python
# helpers/test_claude_code_parser.py
import pytest
from helpers.claude_code import parse_wrapper, ModelFormatDeviation


def test_parse_wrapper_extracts_json_between_markers():
    raw = """\
some preamble
<<<DEFERNO_E2E_BEGIN>>>
{"identity": "alice@example.com"}
<<<DEFERNO_E2E_END>>>
trailing
"""
    assert parse_wrapper(raw) == {"identity": "alice@example.com"}


def test_parse_wrapper_handles_multiline_json():
    raw = """\
<<<DEFERNO_E2E_BEGIN>>>
{
  "identity": "bob",
  "scopes": ["read", "write"]
}
<<<DEFERNO_E2E_END>>>
"""
    assert parse_wrapper(raw) == {"identity": "bob", "scopes": ["read", "write"]}


def test_parse_wrapper_rejects_no_match():
    with pytest.raises(ModelFormatDeviation):
        parse_wrapper("Hello, I cannot help with that.")


def test_parse_wrapper_rejects_multiple_pairs():
    raw = """\
<<<DEFERNO_E2E_BEGIN>>>{"a":1}<<<DEFERNO_E2E_END>>>
<<<DEFERNO_E2E_BEGIN>>>{"b":2}<<<DEFERNO_E2E_END>>>
"""
    with pytest.raises(ModelFormatDeviation):
        parse_wrapper(raw)


def test_parse_wrapper_rejects_unparseable_json():
    raw = "<<<DEFERNO_E2E_BEGIN>>>{not json}<<<DEFERNO_E2E_END>>>"
    with pytest.raises(ModelFormatDeviation):
        parse_wrapper(raw)
```

- [ ] **Step 2: Run, confirm fails (module not yet implemented).**

- [ ] **Step 3: Implement `helpers/claude_code.py`.**

```python
"""Subprocess wrapper around `claude -p`, with the wrapper-string contract.

Spike (Phase 2) picks ONE of two extractor strategies:
  - Outcome A: assistant final message in envelope.result. Default.
  - Outcome B: stream-json events. Set EXTRACTOR = _extract_from_streaming_events
    and OUTPUT_FORMAT = "stream-json".
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


# From Phase 2 spike — adjust if Claude Code's credential file differs
CREDENTIAL_FILE = Path.home() / ".claude" / "mcp_credentials.json"

WRAPPER_RE = re.compile(
    r"<<<DEFERNO_E2E_BEGIN>>>\s*(.*?)\s*<<<DEFERNO_E2E_END>>>",
    re.DOTALL,
)


class ModelFormatDeviation(Exception):
    """Claude returned output that did not match the wrapper-string contract."""


@dataclass
class ToolCallResult:
    raw_envelope: Any
    parsed: dict[str, Any]


def parse_wrapper(text: str) -> dict[str, Any]:
    matches = WRAPPER_RE.findall(text)
    if len(matches) != 1:
        raise ModelFormatDeviation(
            f"Expected exactly one wrapper pair, got {len(matches)}. Raw: {text[:500]!r}"
        )
    try:
        return json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise ModelFormatDeviation(
            f"Wrapper contents did not parse as JSON: {exc}. Contents: {matches[0][:500]!r}"
        ) from exc


def _extract_from_result_field(envelope_text: str) -> tuple[Any, str]:
    """Spike outcome A: final message in envelope['result']."""
    envelope = json.loads(envelope_text)
    return envelope, envelope.get("result", "")


def _extract_from_streaming_events(envelope_text: str) -> tuple[Any, str]:
    """Spike outcome B: stream-json output; concatenate text events."""
    events = []
    for line in envelope_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    chunks: list[str] = []
    for ev in events:
        if (ev.get("type") == "content_block_delta"
                and ev.get("delta", {}).get("type") == "text_delta"):
            chunks.append(ev["delta"]["text"])
        elif ev.get("type") == "tool_result" and isinstance(ev.get("content"), str):
            chunks.append(ev["content"])
    return events, "".join(chunks)


# Set per Phase 2 spike outcome
EXTRACTOR: Callable[[str], tuple[Any, str]] = _extract_from_result_field
OUTPUT_FORMAT = "json"  # change to "stream-json" if extractor changes


def add_mcp_server(name: str, url: str) -> None:
    subprocess.run(["claude", "mcp", "add", name, url], check=True)


def run_prompt(prompt: str, timeout: int = 120) -> ToolCallResult:
    completed = subprocess.run(
        ["claude", "-p", prompt, "--output-format", OUTPUT_FORMAT],
        capture_output=True, text=True, timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"claude -p exited {completed.returncode}: {completed.stderr}")

    envelope, assistant_text = EXTRACTOR(completed.stdout)
    return ToolCallResult(raw_envelope=envelope, parsed=parse_wrapper(assistant_text))


def clear_credential_file() -> None:
    """Per-test fixture calls this to force a fresh OAuth dance."""
    if CREDENTIAL_FILE.exists():
        CREDENTIAL_FILE.unlink()


def credential_file_has_bearer(server_name: str = "deferno") -> bool:
    """Probe used by test_self.py to verify the spike's credential path is valid."""
    if not CREDENTIAL_FILE.exists():
        return False
    data = json.loads(CREDENTIAL_FILE.read_text())
    # Adjust per spike outcome
    return bool(data.get("mcpServers", {}).get(server_name, {}).get("bearer"))


WRAPPER_PROMPT_TEMPLATE = """\
{user_prompt}

Output the tool's raw JSON response wrapped EXACTLY between the markers
shown, on their own lines, with no commentary before, between, or after:

<<<DEFERNO_E2E_BEGIN>>>
<the tool's raw JSON, no surrounding prose>
<<<DEFERNO_E2E_END>>>
"""


def whoami_prompt(server_name: str = "deferno") -> str:
    return WRAPPER_PROMPT_TEMPLATE.format(
        user_prompt=f"Call the {server_name} whoami tool."
    )


def logout_prompt(server_name: str = "deferno") -> str:
    return WRAPPER_PROMPT_TEMPLATE.format(
        user_prompt=f"Call the {server_name} logout tool."
    )
```

- [ ] **Step 4: Run the parser tests, confirm 5 pass.**

```bash
cd defernowork-mcp/tests/e2e-bottle
PYTHONPATH=. pytest helpers/test_claude_code_parser.py -v
```

- [ ] **Step 5: Commit.**

```bash
cd defernowork-mcp
git add tests/e2e-bottle/helpers/__init__.py \
        tests/e2e-bottle/helpers/claude_code.py \
        tests/e2e-bottle/helpers/test_claude_code_parser.py
git commit -m "feat(e2e-bottle): claude_code helper (subprocess + wrapper parser)"
```

### Task 4.2: `helpers/browser_oauth.py`

**Files:**
- Create: `defernowork-mcp/tests/e2e-bottle/helpers/browser_oauth.py`

- [ ] **Step 1: Implement.** The helper accepts a `BrowserContext`
  (caller-managed) and an authorize URL captured from the FIFO; it
  fills login form on `auth2.defernowork.com` and waits for the MCP's
  callback to fire. Returns the captured callback URL + status.

```python
"""Playwright helper that drives the Zitadel login from the captured authorize URL.

Records a Playwright trace, returns the callback redirect's status + query
params. Caller manages the BrowserContext lifecycle.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import BrowserContext


def _authorize_url_re() -> re.Pattern[str]:
    auth_base = os.environ.get("ZITADEL_AUTH_URL", "https://auth2.defernowork.com").rstrip("/")
    return re.compile(rf"^{re.escape(auth_base)}/oauth/v2/authorize\?")


@dataclass
class OAuthDanceResult:
    callback_status: int
    callback_query: dict[str, str]
    callback_body: str | None = None
    network_events: list[dict] = field(default_factory=list)


def complete_zitadel_login(
    context: BrowserContext,
    authorize_url: str,
    user: str,
    password: str,
    artifacts_dir: Path,
    timeout_ms: int = 30_000,
) -> OAuthDanceResult:
    """Drive the Zitadel form to completion. Returns the MCP callback's status."""
    if not _authorize_url_re().match(authorize_url):
        raise AssertionError(f"Authorize URL did not match expected pattern: {authorize_url!r}")

    context.tracing.start(snapshots=True, screenshots=True, sources=False)

    callback_status: int | None = None
    callback_query: dict[str, str] = {}
    callback_body: str | None = None
    network_events: list[dict] = []

    page = context.new_page()

    def on_response(resp):
        nonlocal callback_status, callback_query, callback_body
        url = resp.url
        network_events.append({"url": url, "status": resp.status})
        if "/mcp/oauth/oidc-callback" in url:
            callback_status = resp.status
            qs = parse_qs(urlparse(url).query)
            callback_query = {k: v[0] for k, v in qs.items()}
            try:
                callback_body = resp.text()
            except Exception:
                callback_body = None

    page.on("response", on_response)
    page.goto(authorize_url)
    page.fill("input[name='loginName']", user)
    page.click("button[type='submit']")
    page.wait_for_selector("input[name='password']")
    page.fill("input[name='password']", password, force=True)
    page.click("button[type='submit']")

    deadline = time.monotonic() + (timeout_ms / 1000)
    while callback_status is None and time.monotonic() < deadline:
        page.wait_for_timeout(200)

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    context.tracing.stop(path=str(artifacts_dir / "trace.zip"))

    if callback_status is None:
        raise AssertionError(
            "Callback response was never observed within timeout. "
            "Check the Playwright trace for the form-submission outcome."
        )
    return OAuthDanceResult(
        callback_status=callback_status,
        callback_query=callback_query,
        callback_body=callback_body,
        network_events=network_events,
    )
```

- [ ] **Step 2: Commit.**

```bash
cd defernowork-mcp
git add tests/e2e-bottle/helpers/browser_oauth.py
git commit -m "feat(e2e-bottle): browser_oauth helper (Zitadel login via Playwright)"
```

---

## Phase 5 — Conftest + full `test_self.py`

### Task 5.1: `conftest.py` with session and per-test fixtures

**Files:**
- Create: `defernowork-mcp/tests/e2e-bottle/conftest.py`

- [ ] **Step 1: Implement.**

```python
"""Bottle conftest. Session fixtures: env, runner-side MCP registration.
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
    """Drain the auth-url FIFO before yielding so a stale URL from a
    previous test never leaks. Reading from a FIFO with O_NONBLOCK
    drains without blocking when no writer is connected."""
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
```

- [ ] **Step 2: Commit.**

```bash
cd defernowork-mcp
git add tests/e2e-bottle/conftest.py
git commit -m "feat(e2e-bottle): conftest (session env check + per-test isolation)"
```

### Task 5.2: Full `test_self.py` pre-flight probes

**Files:**
- Modify: `defernowork-mcp/tests/e2e-bottle/test_self.py`

- [ ] **Step 1: Replace the hello-world with real probes.**

```python
"""Bottle self-test — pre-flight probes. If these fail, no scenario can pass.

Run with:
    docker compose -f docker-compose.e2e.yml --env-file .env.e2e \
      run --rm e2e-runner pytest /work/test_self.py -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
from urllib.parse import urlparse

import httpx
import pytest


def test_pytest_runs_in_runner():
    assert 1 + 1 == 2


def test_claude_code_installed():
    assert shutil.which("claude") is not None, "Claude Code CLI not on PATH"
    out = subprocess.run(["claude", "--version"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_capture_url_hook_present():
    hook = os.environ.get("BROWSER")
    assert hook == "/usr/local/bin/capture-url.sh", \
        f"BROWSER env var must point at capture-url.sh; got {hook!r}"
    assert os.path.exists(hook), f"BROWSER hook missing: {hook}"
    assert os.access(hook, os.X_OK), f"BROWSER hook not executable: {hook}"


def test_fifo_present():
    assert os.path.exists("/tmp/auth-url.fifo"), \
        "FIFO /tmp/auth-url.fifo missing — runner image step `mkfifo` failed?"


def test_staging_zitadel_reachable():
    url = os.environ["ZITADEL_AUTH_URL"].rstrip("/") + "/.well-known/openid-configuration"
    resp = httpx.get(url, timeout=10)
    assert resp.status_code == 200, f"Zitadel discovery returned {resp.status_code}"
    body = resp.json()
    assert "issuer" in body, f"Discovery missing 'issuer': {body}"


def test_staging_mcp_reachable():
    """The MCP's well-known OAuth metadata is unauthenticated and proves it's up."""
    base = os.environ["MCP_SERVER_UNDER_TEST"].rstrip("/")
    parsed = urlparse(base)
    well_known = f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-authorization-server/mcp"
    resp = httpx.get(well_known, timeout=10)
    assert resp.status_code == 200, \
        f"MCP well-known returned {resp.status_code} for {well_known}"
```

- [ ] **Step 2: Run.**

```bash
cd defernowork-mcp/tests/e2e-bottle
docker compose -f docker-compose.e2e.yml --env-file .env.e2e \
  run --rm e2e-runner pytest /work/test_self.py -v
```

Expected: 6 passed.

- [ ] **Step 3: Commit.**

```bash
cd defernowork-mcp
git add tests/e2e-bottle/test_self.py
git commit -m "feat(e2e-bottle): full test_self.py pre-flight probes"
```

---

## Phase 6 — Scenarios

The four v1 scenarios in `test_auth_e2e.py`. Each test follows the
same shape: use `fresh_creds`, `browser_context`, `fifo_drain`,
`artifacts_dir`. Run `claude -p` in a background thread (or block,
depending on spike outcome), drive the login via Playwright, assert.

### Task 6.1: `test_auth_e2e.py` skeleton with scenario #1

**Files:**
- Create: `defernowork-mcp/tests/e2e-bottle/test_auth_e2e.py`

- [ ] **Step 1: Implement scenario #1 (`test_happy_path`).** The
  asynchrony is the key tricky bit: `claude -p` blocks until the OAuth
  dance completes, but the BROWSER hook fires partway through and the
  test must drive Playwright in parallel. Pattern: run `claude -p` in
  a thread; main thread reads the FIFO and runs Playwright; thread
  finishes when the bearer is cached and the tool returns.

```python
"""End-to-end auth + tool-call scenarios against staging."""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from helpers.browser_oauth import complete_zitadel_login
from helpers.claude_code import (
    ToolCallResult,
    run_prompt,
    whoami_prompt,
)


def _read_authorize_url_from_fifo(timeout_s: float = 30) -> str:
    """Block-read the FIFO until we get a URL. capture-url.sh writes once per OAuth dance."""
    import os as _os
    fd = _os.open("/tmp/auth-url.fifo", _os.O_RDONLY)
    try:
        chunks: list[bytes] = []
        while True:
            data = _os.read(fd, 4096)
            if not data:
                break
            chunks.append(data)
            if b"\n" in data:
                break
        return b"".join(chunks).decode().strip()
    finally:
        _os.close(fd)


def _run_dance_in_parallel(
    prompt: str,
    browser_context,
    artifacts_dir: Path,
) -> ToolCallResult:
    """Start `claude -p` in a thread; drive Playwright login from the FIFO; return tool result."""
    user = os.environ["ZITADEL_TEST_USER"]
    password = os.environ["ZITADEL_TEST_PASSWORD"]

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_prompt, prompt, 180)
        authorize_url = _read_authorize_url_from_fifo()
        complete_zitadel_login(
            context=browser_context,
            authorize_url=authorize_url,
            user=user,
            password=password,
            artifacts_dir=artifacts_dir,
        )
        return future.result(timeout=120)


def test_happy_path(fresh_creds, browser_context, fifo_drain, artifacts_dir):
    """Scenario #1: full OAuth dance + first tool call returns wrapped JSON."""
    result = _run_dance_in_parallel(
        whoami_prompt(),
        browser_context,
        artifacts_dir,
    )
    assert result.parsed, f"Wrapper parsed empty: {result.raw_envelope!r}"
```

- [ ] **Step 2: Run scenario #1 from inside the runner.**

```bash
cd defernowork-mcp/tests/e2e-bottle
docker compose -f docker-compose.e2e.yml --env-file .env.e2e \
  run --rm e2e-runner pytest /work/test_auth_e2e.py::test_happy_path -v
```

Expected: 1 passed. Iterate on the parallel-dance plumbing here — it
is the single most fragile mechanism in the bottle. Before declaring
this task done, run the test 3 times in succession (different test
runs, each fresh) and confirm 3 passes.

- [ ] **Step 3: Commit.**

```bash
cd defernowork-mcp
git add tests/e2e-bottle/test_auth_e2e.py
git commit -m "feat(e2e-bottle): scenario #1 happy_path"
```

### Task 6.2: Scenario #2 — `test_whoami_returns_user_identity`

**Files:**
- Modify: `defernowork-mcp/tests/e2e-bottle/test_auth_e2e.py`

- [ ] **Step 1: Append.**

```python
def test_whoami_returns_user_identity(fresh_creds, browser_context, fifo_drain, artifacts_dir):
    """Scenario #2: whoami JSON contains identity matching ZITADEL_TEST_USER."""
    result = _run_dance_in_parallel(
        whoami_prompt(),
        browser_context,
        artifacts_dir,
    )
    expected_user = os.environ["ZITADEL_TEST_USER"]
    identity = result.parsed.get("identity") or result.parsed.get("user")
    assert identity, f"whoami JSON missing identity field: {result.parsed!r}"
    assert expected_user in str(identity), \
        f"whoami identity {identity!r} did not contain expected user {expected_user!r}"
```

The exact field name (`identity` vs. `user` vs. `email`) depends on
the MCP's whoami tool shape. The assertion checks both common shapes
and asserts that *some* identity-ish field includes the expected user.
Tighten this in a follow-up once Phase 2's spike has the canonical
shape.

- [ ] **Step 2: Run, commit.**

```bash
docker compose -f docker-compose.e2e.yml --env-file .env.e2e \
  run --rm e2e-runner pytest /work/test_auth_e2e.py::test_whoami_returns_user_identity -v
git add tests/e2e-bottle/test_auth_e2e.py
git commit -m "feat(e2e-bottle): scenario #2 whoami returns user identity"
```

### Task 6.3: Scenario #3 — `test_logout_invalidates_token_server_side`

**Files:**
- Modify: `defernowork-mcp/tests/e2e-bottle/test_auth_e2e.py`

- [ ] **Step 1: Append.**

```python
from helpers.claude_code import logout_prompt


def test_logout_invalidates_token_server_side(
    fresh_creds, browser_context, fifo_drain, artifacts_dir,
):
    """Scenario #3: after logout, the cached bearer no longer authorizes whoami."""
    # Step 1: log in + first whoami
    _run_dance_in_parallel(whoami_prompt(), browser_context, artifacts_dir)

    # Step 2: logout via tool. Should NOT trigger another OAuth dance — token
    # is still server-valid at this point. Run inline.
    logout_result = run_prompt(logout_prompt(), timeout=60)
    assert logout_result.parsed is not None  # tool returned SOMETHING parseable

    # Step 3: whoami again. Either:
    #   (a) Claude Code observes 401 and triggers a new OAuth dance (test
    #       times out on the FIFO because we're not driving login here), OR
    #   (b) the call errors directly.
    # Both prove logout invalidated the server-side token. The expectation
    # is that the second whoami does NOT silently succeed with the cached
    # bearer.
    from helpers.claude_code import run_prompt as _rp
    try:
        result2 = _rp(whoami_prompt(), timeout=20)
        # If it returned without re-auth, the bearer was still accepted —
        # logout did not invalidate. Fail.
        pytest.fail(
            f"Expected post-logout whoami to fail or trigger re-auth; "
            f"got {result2.parsed!r}"
        )
    except (RuntimeError, TimeoutError):
        pass  # expected: re-auth needed or call errored
```

- [ ] **Step 2: Run, commit.**

```bash
git add tests/e2e-bottle/test_auth_e2e.py
git commit -m "feat(e2e-bottle): scenario #3 logout invalidates token server-side"
```

### Task 6.4: Scenario #4 — `test_logout_invalidates_zitadel_session`

**Files:**
- Modify: `defernowork-mcp/tests/e2e-bottle/test_auth_e2e.py`

- [ ] **Step 1: Append.**

```python
def test_logout_invalidates_zitadel_session(
    fresh_creds, browser_context, fifo_drain, artifacts_dir,
):
    """Scenario #4: after logout, the same BrowserContext does NOT auto-SSO into Zitadel."""
    _run_dance_in_parallel(whoami_prompt(), browser_context, artifacts_dir)
    run_prompt(logout_prompt(), timeout=60)

    # Re-attempt the OAuth dance in the SAME BrowserContext.
    # If Zitadel preserves SSO, page.goto(authorize_url) skips the form.
    # We assert that the login form IS presented (i.e., NOT instantly
    # redirected to the callback).
    user = os.environ["ZITADEL_TEST_USER"]
    password = os.environ["ZITADEL_TEST_PASSWORD"]

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_prompt, whoami_prompt(), 180)
        authorize_url = _read_authorize_url_from_fifo()
        page = browser_context.new_page()
        page.goto(authorize_url)
        # The login form must be visible — if it's not, Zitadel auto-SSO'd,
        # which means logout did not invalidate the upstream session.
        page.wait_for_selector("input[name='loginName']", timeout=10_000)
        # Now complete the form so the future doesn't hang
        page.fill("input[name='loginName']", user)
        page.click("button[type='submit']")
        page.wait_for_selector("input[name='password']")
        page.fill("input[name='password']", password, force=True)
        page.click("button[type='submit']")
        # Drain the future so the test completes
        future.result(timeout=120)
```

- [ ] **Step 2: Run, commit.**

```bash
git add tests/e2e-bottle/test_auth_e2e.py
git commit -m "feat(e2e-bottle): scenario #4 logout invalidates Zitadel session"
```

---

## Phase 7 — README + smoke run

### Task 7.1: `tests/e2e-bottle/README.md`

**Files:**
- Create: `defernowork-mcp/tests/e2e-bottle/README.md`

- [ ] **Step 1:** Write README covering:
  - What the bottle tests (one paragraph).
  - Prerequisites: Docker, a no-MFA test user in `auth2`, populated
    `.env.e2e`.
  - How to run: build, then `docker compose -f docker-compose.e2e.yml
    --env-file .env.e2e run --rm e2e-runner pytest /work -v`.
  - How to debug a failure: where artifacts land, how to read the
    Playwright trace, how to inspect the FIFO.
  - Bump cadence for `CLAUDE_CODE_VERSION` in the Dockerfile.

- [ ] **Step 2:** Commit.

```bash
cd defernowork-mcp
git add tests/e2e-bottle/README.md
git commit -m "docs(e2e-bottle): operator README"
```

### Task 7.2: Full smoke run

**Files:** none.

- [ ] **Step 1:** Run all four scenarios + test_self.py end-to-end:

```bash
cd defernowork-mcp/tests/e2e-bottle
docker compose -f docker-compose.e2e.yml --env-file .env.e2e \
  run --rm e2e-runner pytest /work -v
```

Expected: 6 + 4 = 10 passed.

- [ ] **Step 2:** If anything is flaky (intermittent fail on a clean
  re-run), capture the artifacts and open an issue. Do NOT mark v1
  done with known flaky scenarios.

- [ ] **Step 3:** Final commit (if any iteration was needed):

```bash
git add ... && git commit -m "fix(e2e-bottle): <whatever>"
```

---

## Done criteria

- [ ] All scenarios pass on a clean run (Phase 7 Task 7.2).
- [ ] `pytest` (regular suite) still passes — bottle stays excluded
      from default discovery.
- [ ] README is operator-runnable from a freshly-cloned repo.
- [ ] Phase 1's `MCP_DEBUG_OAUTH` gate remains in place (already
      shipped at `8e4089f`).
