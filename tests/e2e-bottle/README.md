# MCP auth E2E bottle

End-to-end regression coverage for the deployed Deferno MCP's
OAuth-dance + tool-call surface, exercised against the live staging
stack (`app2.defernowork.com/mcp`, `auth2.defernowork.com`).

The bottle is a Dockerized test runner — there are no locally-built
services. See **[WIP.md](WIP.md)** for current implementation status
and any blockers awaiting operator decisions.

## Authoritative documents

- **Design (v4):** [`../../docs/superpowers/specs/2026-04-28-mcp-auth-e2e-bottle-design.md`](../../docs/superpowers/specs/2026-04-28-mcp-auth-e2e-bottle-design.md)
  — what the bottle tests, scenario list, isolation strategy.
- **Plan (v2):** [`../../docs/superpowers/plans/2026-04-28-mcp-auth-e2e-bottle-plan.md`](../../docs/superpowers/plans/2026-04-28-mcp-auth-e2e-bottle-plan.md)
  — phased implementation steps with checkboxes.
- **WIP status:** [`WIP.md`](WIP.md) — current commits, what's done,
  what's blocked, decisions required from the operator.

When the design and plan disagree with the WIP doc, the **WIP doc
wins** for current state; the design wins for assertion shapes; the
plan wins for the *order* of implementation tasks (but specific
mechanism details — file paths, CLI flag shapes — drift with Claude
Code versions and need re-verification per major bump).

## Layout

```
tests/e2e-bottle/
├── README.md            this file
├── WIP.md               current implementation state + blockers
├── conftest.py          pytest fixtures (env check, browser, creds)
├── test_self.py         pre-flight probes (run these first)
├── docker-compose.e2e.yml
├── pytest.ini
├── helpers/
│   ├── claude_code.py   `claude -p` subprocess wrapper + parser
│   └── browser_oauth.py Playwright Zitadel form driver
├── runner/
│   ├── Dockerfile
│   ├── capture-url.sh   BROWSER hook (FIFO writer)
│   └── requirements.txt
└── artifacts/           per-run traces, envelope dumps (gitignored)
```

## Running the bottle

### Prerequisites

- Docker.
- A no-MFA test user in staging Zitadel (`auth2.defernowork.com`).
- `.env.e2e` filled in. See `.env.e2e.example` for the schema.
  Must include a working `ANTHROPIC_API_KEY` with billing credit.

### Build the runner image

```bash
docker compose -f docker-compose.e2e.yml --env-file .env.e2e build e2e-runner
```

### Run pre-flight probes

```bash
docker compose -f docker-compose.e2e.yml --env-file .env.e2e \
  run --rm e2e-runner pytest /work/test_self.py -v
```

Expected: 6 passed. If any fail, fix that before running the
scenarios — the scenarios depend on every probe passing.

### Run the scenarios

```bash
docker compose -f docker-compose.e2e.yml --env-file .env.e2e \
  run --rm e2e-runner pytest /work -v
```

(Phase 6 scenarios are not yet implemented — see [WIP.md](WIP.md) for
the open architecture decision blocking them.)

## Pytest exclusion

The bottle is excluded from default `pytest` discovery via
`pyproject.toml`'s `norecursedirs` and the project-root `conftest.py`'s
`collect_ignore`. Running `pytest` from `defernowork-mcp/` will *not*
pick up bottle tests. The bottle is only run inside the runner
container, which `cd`s into `/work` (the bottle directory mounted
read-only).
