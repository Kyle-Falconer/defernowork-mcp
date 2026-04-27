# MCP spec-driven test suite — design

**Status:** draft
**Date:** 2026-04-27
**Repo:** `defernowork-mcp`
**Source of truth for the API contract:** `Deferno/docs/architecture.md` and the
Rust handlers under `Deferno/backend/src/handlers/`. The MCP `README.md` is
descriptive, not authoritative.

---

## Why

Substantial backend changes have landed in the last few weeks (OIDC migration,
Habit/Chore/Event item kinds, `/items/:id/*` cross-kind routes, pinned tasks,
comments, recurring scope/type, batch ops, OAuth-provider standards work,
`MCP_PUBLIC_URL` rename) and the MCP server "stopped working." The current
test suite (`test_client.py`, `test_server.py`, `test_multi_user_auth.py`,
`test_oauth_flow.py`) is green on `main` and gates deploy via
`.github/workflows/release.yml`, yet drift is shipping. The tests cover the
MCP's internals well but do not encode the API contract, so backend shape
changes go undetected.

## API version contract

The backend wraps every JSON response in a `v0.1` envelope (see
`Deferno/backend/src/api_envelope.rs`):

```json
{ "version": "0.1", "data": <payload>, "error": null }
{ "version": "0.1", "data": null, "error": { "code": "...", "message": "..." } }
```

The webui's HTTP client (`Deferno/webui/src/api/client.ts`) declares
`SUPPORTED_API_VERSION = "0.1"` and rejects any response whose `version`
field doesn't match. **The MCP HTTP client does not currently do either —
it doesn't unwrap the envelope and doesn't validate the version.** This
spec treats fixing that as in-scope: the new contract tests assert the
client unwraps + validates, and the existing `client.py:_request` will
need to be updated to satisfy them. This is the test-first surfacing of
"why the MCP stopped working" that motivated this work.

The version field is the **drift contract**:

- **Within a version (currently `v0.1`):** changes must be additive
  (new optional fields, new endpoints, new error codes). Fixture
  semantics — "extra response keys allowed; missing required keys fail" —
  enforce this automatically.
- **Across versions (`v0.1` → `v0.2`):** changes may be breaking. Backend
  runs both versions concurrently during the transition window. MCP pins
  a `SUPPORTED_API_VERSION` constant (and a list of also-acceptable
  versions during cutovers). MCP rejects responses with an unsupported
  version with a clear error.

This eliminates the need for cross-repo CI gating to prevent drift. The
MCP repo's own CI is the enforcement point: contract fixtures live under
`tests/spec/<version>/...` and the MCP can only ship if its fixtures pass
against the version it claims to support. Backend changes within `v0.1`
are constrained to be MCP-compatible by the additive rule (enforced by
the webui's tests on the Deferno side); breaking changes go through a
version bump that the MCP opts into on its own schedule.

## Goal

Encode the backend's HTTP contract — and the MCP OAuth provider's RFC
contract — as test fixtures, and run them against the in-process MCP code
on every CI build. Any drift between either contract and the MCP fails
`pytest`, which gates the existing docker/deploy job. Pytest output is the
sole inventory of failures (no separate generated report).

### Success criteria

1. Every backend endpoint in `Deferno/docs/architecture.md` has a fixture
   under `tests/spec/v0.1/`. Endpoints the MCP does not yet expose have
   `client_method: null` and `mcp_tool: null` — the runner skips their
   request/response tests (pytest skip, not xfail) and `inventory.py`
   reports the absence in its coverage output.
2. Every fixture drives at least one request-shape test (the MCP speaks
   the documented HTTP) and one response-shape test (the MCP correctly
   surfaces the documented response from inside the v0.1 envelope).
3. The MCP HTTP client validates the `version` field on every response
   and unwraps the envelope before returning to callers. A response
   tagged with an unsupported version raises a clear error.
4. The MCP OAuth provider has fixture-driven tests for the RFCs it
   implements (RFC 9728, 8414, 7591, 7009) running in-process via
   `httpx.ASGITransport` — no network.
5. The existing live-staging OAuth integration test still exists but is
   gated behind `@pytest.mark.live` so default `pytest` skips it.
6. The default `pytest -v -m "not live"` invocation runs deterministically
   and gates deploy. A separate workflow runs the live tests on a schedule
   against staging.

## Non-goals

- Tests against a locally-launched real Rust backend, or fixtures captured
  by hitting a real backend. The fixture format is designed so that this
  could be layered on later (the captured response becomes a fixture
  example), but it is not part of this spec.
- Expanding the MCP tool surface to cover Habit/Chore/Event, `/items/...`
  cross-kind ops, comments, pinned tasks, or `/tasks/today`. The inventory
  surfaces these gaps; closing them is a separate design.
- Cross-repo automation that re-captures fixtures from the live backend
  whenever the Rust code changes.
- Refactoring the MCP source itself, with a single bounded exception:
  `client.py:_request` gains envelope unwrapping and version validation
  to satisfy `test_client_envelope_contract.py`. Any other source change
  is a separate spec.

## Architecture

```
defernowork-mcp/
  tests/
    spec/                                # fixture-as-spec, one JSON per backend operation
      v0.1/                              # version pin — fixtures describe inner `data` payload, envelope is implicit
        _envelope.json                   # the meta-spec for the v0.1 envelope itself
        auth/
          me_get.json
          me_patch.json
          tokens_list.json
          tokens_create.json
          tokens_delete.json
          tokens_rename.json
          connected_mcp.json
          oidc_login.json
          oidc_callback.json
          logout.json
        tasks/
          list.json
          create.json
          today.json
          plan_get.json
          plan_add.json
          plan_remove.json
          plan_reorder.json
          mood_history.json
          get.json
          patch.json
          split.json
          merge.json
          fold.json
          move.json
          comments_list.json
          comments_create.json
          pinned_get.json
          pinned_reorder.json
          pinned_label.json
        items/
          list.json
          get.json
          delete.json
          history.json
          comments_list.json
          comments_create.json
          split.json
          merge.json
          move.json
          pin.json
          convert.json
        internal/
          mcp_session.json
        admin/                           # mcp_tool=null for all of these
          users_list.json
          stats.json
      oauth/                             # MCP OAuth-provider RFC contracts (not envelope-wrapped)
        prm_metadata.json                #   RFC 9728
        as_metadata.json                 #   RFC 8414
        register.json                    #   RFC 7591
        authorize.json
        token.json
        revoke.json                      #   RFC 7009
    spec_runner.py                       # parametrize contract tests over fixtures
    endpoint_registry.py                 # hand-curated list of backend endpoints grouped by handler module
    test_client_envelope_contract.py     # v0.1 envelope unwrap + version validation (consumes _envelope.json)
    test_client_contract.py              # spec_runner -> DefernoClient
    test_tools_contract.py               # spec_runner -> registered MCP tools
    test_oauth_provider_contract.py      # spec_runner -> in-process OAuth provider via ASGITransport
    test_oauth_flow.py                   # KEEP — live staging, gated @pytest.mark.live
    test_redis_store.py                  # RENAMED from test_multi_user_auth.py
    test_helpers.py                      # _compact, _generate_token, stdio mode
    test_client_transport.py             # transport-level errors (timeout, connect, no-token)
    inventory.py                         # pre-test sanity: every architecture.md endpoint has a fixture
```

## Components

### `tests/spec/v0.1/_envelope.json` — the envelope meta-spec

```json
{
  "envelope_version": "0.1",
  "success_shape": {
    "version": "string",
    "data": "any",
    "error": "null"
  },
  "error_shape": {
    "version": "string",
    "data": "null",
    "error": {
      "code": "string",
      "message": "string",
      "_required": ["code", "message"]
    }
  },
  "version_validation": {
    "supported": ["0.1"],
    "behavior_on_mismatch": "raise DefernoError(status=502, message=~'unsupported API version')"
  }
}
```

`_envelope.json` is consumed by a single dedicated test
(`test_client_envelope_contract.py`) that verifies:

1. `client._request` returns the inner `data` payload, not the full envelope.
2. A backend response with a missing `version` field raises a clear error.
3. A backend response with `version: "9.9"` raises a clear error mentioning
   the unsupported version.
4. An error envelope (`{ version, data: null, error: { code, message } }`)
   raises `DefernoError` with `error.code` exposed on the exception.

All operation fixtures (below) describe the *inner data shape only*. The
runner wraps the example payload in the envelope before feeding it to
`respx`, and unwraps it before passing to shape comparators.

### `tests/spec/v0.1/<resource>/<op>.json` — fixture-as-spec

Single source of truth for one backend (or OAuth provider) operation.

```json
{
  "operation": "tasks.create",
  "method": "POST",
  "path_template": "/tasks",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["title"],
      "optional": [
        "description", "labels", "parent_id", "complete_by",
        "desire", "productive", "mood_start", "recurrence",
        "recurring_type", "next_task_id"
      ],
      "example": { "title": "Demo", "description": "Test" }
    }
  },
  "responses": [
    {
      "status": 201,
      "shape": {
        "id": "uuid",
        "title": "string",
        "status": "string",
        "actions": "array",
        "date_created": "string"
      },
      "example": {
        "id": "00000000-0000-0000-0000-000000000001",
        "title": "Demo",
        "status": "open",
        "actions": [{ "kind": "Created" }],
        "date_created": "2026-04-27T00:00:00Z"
      }
    },
    {
      "status": 400,
      "error_shape": { "code": "string", "message": "string" },
      "error_example": { "code": "validation_error", "message": "title must be non-empty" }
    },
    {
      "status": 401,
      "error_shape": { "code": "string", "message": "string" },
      "error_example": { "code": "unauthorized", "message": "missing or invalid token" }
    }
  ],
  "client_method": "create_task",
  "client_args_from_example": ["title", "description"],
  "mcp_tool": "create_task",
  "mcp_tool_args_from_example": ["title", "description"],
  "notes": "optional free-text — never asserted, only for human readers"
}
```

Fixture rules:

- `path_template` may contain `{id}` placeholders; the runner substitutes a
  fixed UUID for matching.
- `auth` ∈ `{"none", "bearer", "bearer-admin", "internal-shared-secret"}`.
  Drives header-presence assertions in the request-shape test.
- `request.body.required` keys MUST appear in the outgoing HTTP body when
  the client method is called with the matching arguments. `optional` keys
  MUST appear only when supplied.
- For 2xx responses: `shape` and `example` describe the *inner data
  payload* (what's inside the envelope's `data` field). The runner wraps
  in `{version, data, error: null}` before serving via `respx`, and
  unwraps before passing to the shape comparator.
- For non-2xx responses: `error_shape` and `error_example` describe the
  *inner error object* (what's inside the envelope's `error` field).
  The runner wraps in `{version, data: null, error: <error_example>}`
  before serving, and asserts `DefernoError` is raised with `error.code`
  and `error.message` exposed on the exception.
- All shape descriptors are recursive: leaf type names (`"string"`,
  `"number"`, `"boolean"`, `"uuid"`, `"datetime"`, `"array"`, `"object"`,
  `"null"`, or `"any"`) or nested objects. Extra keys in the actual
  response are allowed (the backend may add fields without breaking MCP);
  missing required keys fail.
- `client_method` names the `DefernoClient` method that wraps the endpoint.
  `null` if the MCP client does not yet implement it — the client-layer
  test is skipped for that fixture and the absence is reported by
  `inventory.py`.
- `client_args_from_example` lists which keys from `request.body.example`
  are passed to `client.<method>(**args)` as kwargs. Keys not listed are
  not passed.
- `mcp_tool` names the registered MCP tool that wraps the endpoint.
  `null` if the MCP does not yet expose it — the tool-layer test is
  skipped and absence is reported by `inventory.py`.
- `mcp_tool_args_from_example` is the analogue of `client_args_from_example`
  for the tool layer.

### `tests/spec_runner.py` — fixture-driven test factory

Exposes:

```python
SUPPORTED_API_VERSION = "0.1"  # mirrors webui's constant

def discover_backend_fixtures(version: str = SUPPORTED_API_VERSION) -> list[Fixture]:
    """Walks tests/spec/{version}/, excluding _envelope.json. Each fixture
    is treated as envelope-wrapped: the runner wraps the example before
    feeding to respx and unwraps before passing to comparators."""

def discover_oauth_fixtures() -> list[Fixture]:
    """Walks tests/spec/oauth/. These fixtures are NOT envelope-wrapped
    (the OAuth provider speaks raw OAuth/RFC payloads, not the v0.1 envelope)."""

def assert_request_matches_spec(
    fixture: Fixture,
    captured: respx.Route,
    args: dict,
) -> None: ...

def assert_response_matches_shape(
    fixture: Fixture,
    response_index: int,
    actual: object,
) -> None: ...
```

The recursive shape comparator is implemented here. It is the only place
"the spec" is interpreted, so spec semantics are testable in isolation.

### `tests/endpoint_registry.py` — hand-curated handler-source-of-truth

A flat Python list of every backend endpoint, grouped by the Rust handler
module it lives in (e.g. `handlers::tasks`, `handlers::items`,
`handlers::pinned`). Updated by hand alongside backend changes; the
discipline is "if you add a route in Rust, add the entry here in the
same MCP-side PR." `inventory.py` cross-checks three sources — the
architecture doc tables, this registry, and the on-disk fixtures — and
fails on any mismatch. Three-source consensus catches "doc drifted from
code" and "fixture drifted from doc" simultaneously.

### `tests/test_client_contract.py` — backend HTTP contract via `DefernoClient`

For each fixture with `client_method` set:

- **Request-shape test:** `respx`-mock the URL, call
  `client.<client_method>(**args_from_example)`, capture the request,
  call `assert_request_matches_spec`.
- **Response-shape test:** for each `responses[].example` whose status is in
  `{200, 201, 204}`, the runner wraps the example in
  `{"version": "0.1", "data": <example>, "error": null}`, mocks the URL
  to return that envelope, calls the client, and asserts the *unwrapped*
  return value matches the shape. For error statuses, the runner wraps
  in `{"version": "0.1", "data": null, "error": {"code": "...", "message": "..."}}`
  and asserts `DefernoError(status_code=..., code=...)` is raised with
  the documented `error.code`.

Both tests parametrize over fixtures via `pytest.mark.parametrize`.

### `tests/test_tools_contract.py` — MCP tool layer contract

For each fixture with `mcp_tool` set, instantiate the FastMCP server via
`create_server()`, look up the registered tool by name, and run the same
two assertions through the tool's invocation path. This catches arg
coercion bugs in `tools/*.py` that the client-layer tests would miss.

### `tests/test_oauth_provider_contract.py` — RFC contract for MCP OAuth provider

Mounts the FastMCP server's ASGI app via the same
`mcp.streamable_http_app()` call `server.py` already uses for HTTP
transport, then wraps it in `httpx.ASGITransport`. Runs the discovery /
register / authorize / token / revoke fixtures against it in-process.
OAuth fixtures use the same JSON shape as backend fixtures but the
runner does NOT wrap them in the v0.1 envelope — OAuth/RFC payloads are
not envelope-wrapped. The OAuth-provider fixtures use `path_template`
paths under `/.well-known/...` and `/mcp/...` so the runner targets the
right ASGI route. Verifies:

- `prm_metadata`: required JSON keys (`resource`, `authorization_servers`).
- `as_metadata`: `issuer`, `authorization_endpoint`, `token_endpoint`,
  `registration_endpoint`, `code_challenge_methods_supported` includes
  `S256`, `token_endpoint_auth_methods_supported` includes
  `client_secret_post` and excludes `client_secret_basic` (workaround
  documented in `test_oauth_flow.py`).
- `register`: `client_secret_post` returns `client_secret`; `none` does not.
- `token`: client_secret_post + correct/wrong/missing secret behavior;
  none + no secret behavior; missing/unknown client_id behavior — all
  with status assertions, not body content.
- `revoke`: RFC 7009 endpoint exists and returns 200 for both valid and
  unknown tokens.
- `authorize`: redirects to OIDC provider with required query params.

### `tests/inventory.py` — pre-test coverage gate

Cross-checks three sources and fails on any mismatch:

1. `Deferno/docs/architecture.md`'s endpoint tables (the documented contract).
2. `tests/endpoint_registry.py` (hand-curated listing per Rust handler module).
3. `tests/spec/<MCP_SUPPORTED_API_VERSION>/` (the on-disk fixtures).

A doc entry without a registry entry, a registry entry without a fixture,
or a fixture without a doc entry all fail the suite with a clear message
naming the missing item. Runs as a `pytest` test
(`test_every_endpoint_has_a_fixture`) so it gates CI like everything else.

The architecture doc lives in a sibling repo; the script reads it from a
configurable path (`ARCHITECTURE_DOC_PATH` env var, default
`../Deferno/docs/architecture.md`). When the path is absent (e.g. local
solo checkout), the test self-skips with a clear reason. In CI, the
workflow checks out the `Deferno` sibling repo at its default branch so
this test always runs. (See [CI integration](#ci-integration).)

## Migration of existing tests

Every existing test was read in full before classification — categorization
is based on what each test actually asserts, not on its name or signature.
The following table is the plan:

| Existing | Disposition | Rationale |
|---|---|---|
| `test_client.py` request-shape and response-shape tests (list_tasks, plan add/remove/reorder, get_daily_plan, move, batch) | Migrate to fixtures + delete from `test_client.py` | These are exactly the spec-driven tests in their final form. |
| `test_client.py` transport tests (timeout→504, connect→502, non-JSON 500, no-token→401) | Move to `test_client_transport.py` | Generic transport behavior; no per-endpoint spec applies. |
| `test_server.py` `_compact` tests | Move to `test_helpers.py` | Helper semantics, not HTTP contract. |
| `test_server.py` `test_create_server_returns_fastmcp`, `test_default_base_url_is_localhost` | Move to `test_helpers.py` | Module-level constants. |
| `test_multi_user_auth.py` `RedisStore` save/load/delete + multi-user isolation | Stay; rename file to `test_redis_store.py` | Internal storage layer for the OAuth provider; no public contract to drive. |
| `test_multi_user_auth.py` token generation tests | Move to `test_helpers.py` | Helper. |
| `test_multi_user_auth.py` `test_get_client_stdio_does_not_use_redis` | Move to `test_helpers.py` | Server-mode behavior. |
| `test_oauth_flow.py` (entire file) | Stay; mark `@pytest.mark.live` | Live-staging integration. RFC coverage is duplicated in-process by `test_oauth_provider_contract.py`. |
| **DISCOVERED GAP:** `client.py:_request` does not unwrap the v0.1 envelope and does not validate `version`. | New code in `client.py` to satisfy `test_client_envelope_contract.py`. | Implementation lives in this work because the test surfaces the bug; without the fix, every contract test fails on response-shape comparisons. |

No assertion in any existing test is dropped. Where a test moves, it moves
verbatim; where it migrates, the migration is one-to-one (one existing
assertion → one fixture-driven assertion or skipped entirely with a note
in the migration tracking table during execution).

## CI integration

The version-pinning model (see [API version contract](#api-version-contract))
keeps enforcement local to each repo and avoids cross-repo deadlock:

- **MCP CI is the enforcement point for the MCP→backend contract.** It
  runs the full fixture suite for the version the MCP advertises (today
  `v0.1`). Drift in any direction — the MCP no longer matches the
  documented `v0.1` shape, OR the documented `v0.1` shape changed in a
  breaking way — fails the MCP build and blocks deploy.
- **Deferno CI is the enforcement point for "the backend respects its
  own envelope."** It already has tests asserting handlers wrap in
  `Versioned<T>` and that the webui client validates the version. No
  change needed there from this spec.
- **Breaking changes go through a version bump.** Backend ships
  `v0.2` alongside `v0.1`. MCP keeps testing against `v0.1` until it
  opts in to `v0.2`, at which point its fixtures move from
  `tests/spec/v0.1/` to `tests/spec/v0.2/` and `SUPPORTED_API_VERSION`
  is bumped. Backend can drop `v0.1` once all consumers have moved.

Existing `.github/workflows/release.yml` runs `pytest -v` and gates
docker/deploy on `needs: test`. Concrete changes:

1. Default test command becomes `pytest -v -m "not live"`. The `live`
   marker is registered in `pyproject.toml`.
2. `tests/inventory.py` runs as part of the default suite. It needs
   `Deferno/docs/architecture.md` available and uses
   `ARCHITECTURE_DOC_PATH` (default `../Deferno/docs/architecture.md`).
   In CI the workflow checks out the sibling repo at a floating ref
   (default branch). When the path isn't present (e.g. local solo
   checkout), the inventory test self-skips with a clear reason rather
   than silently passing.
3. New `.github/workflows/live-tests.yml` runs `pytest -v -m live` against
   staging on a daily schedule and on `workflow_dispatch`. Failures
   notify but do not block deploy.

No cross-repo gating job. No tiered "advisory" or "hard" modes. The
version pin is the contract; the contract tests live in this repo and
gate this repo's deploy.

## Spec fixture format — recursive shape semantics

```
"shape": "string"        # leaf — value must be `str`
"shape": "number"        # leaf — `int` or `float`
"shape": "boolean"
"shape": "uuid"          # leaf — must parse as a UUID
"shape": "datetime"      # leaf — must parse as ISO 8601
"shape": "null"          # leaf — must be None
"shape": "any"           # leaf — wildcard
"shape": "array"         # leaf — list, items unconstrained
"shape": "object"        # leaf — dict, contents unconstrained
"shape": ["string"]      # array of strings
"shape": [{"id": "uuid"}] # array of objects with that shape
"shape": {"k": "string"} # object with key set; extra keys allowed
"shape": {"k": "string", "_required": ["k"]} # explicit required-key set
```

Default required-key behavior: every key declared at an object level is
required. The `_required` escape hatch lets a fixture declare optional
keys without listing them as required.

## Risk and mitigation

- **Architecture doc drift from Rust source.** Mitigation: `inventory.py`
  cross-checks three sources — `Deferno/docs/architecture.md` tables,
  `tests/endpoint_registry.py`, and the on-disk fixture tree (see the
  `endpoint_registry.py` component description). Three-source consensus
  catches both "doc drifted from code" and "fixture drifted from doc."
- **Markdown table parser brittleness.** Mitigation: parser uses a strict
  whitelist of expected table headers (`Method | Path | Auth | Description`
  and the simpler `Method | Path | Description`); any deviation fails
  loudly with a clear pointer to the offending line.
- **Fixture rot.** Mitigation: the runner asserts every fixture is reached
  by at least one parametrized test, so adding an unused fixture fails.
- **Migration drops coverage.** Mitigation: migration is performed
  test-by-test with a checklist that tracks every existing assertion and
  its destination. A pre-merge diff confirms no `assert` line is dropped
  silently.

## Out of scope

- Adding MCP tools for missing endpoints.
- Capturing fixtures from a live backend.
- Replacing `respx` with a different HTTP fake.
- Migrating `Deferno`'s own test suite to a similar format.
- Refactoring `client.py`, `server.py`, or `tools/*.py` beyond the
  envelope-unwrap + version-validation change required to satisfy
  `test_client_envelope_contract.py`. Any other refactor is a separate
  spec.

## Decisions

Recorded as standalone facts so future readers don't need the design
conversation that produced them. Each lists the rejected alternatives
where relevant:

- **Source of truth:** Rust backend API as documented in
  `Deferno/docs/architecture.md` and implemented in
  `Deferno/backend/src/handlers/`. The MCP `README.md` is descriptive,
  not authoritative.
- **Drift contract:** the backend's `v0.1` API envelope. Within a version,
  changes must be additive; breaking changes require a version bump and a
  concurrent transition window. MCP fixtures live under `tests/spec/<version>/`
  and the MCP advertises `SUPPORTED_API_VERSION` as a constant. This
  replaces a cross-repo CI orchestration alternative (one repo's CI gating
  on the other's contract tests), which was rejected because it created
  chicken-and-egg deadlocks on coordinated breaking changes — neither
  repo could merge first.
- **Test strategy:** spec-driven fixtures (one JSON file per backend
  operation) parametrize generic request-shape and response-shape tests.
  Rejected alternatives: hand-rolled per-endpoint assertions (no central
  spec; drift between spec and tests is invisible) and capture-from-live-
  backend fixtures (cross-repo automation cost not justified yet).
- **Test layers:** `DefernoClient` HTTP shape (catches backend drift) +
  registered MCP tools (catches arg coercion / serialization bugs in
  `tools/*.py`). Both layers parametrize over the same fixtures.
- **Auth coverage:** backend bearer auth (carried through fixtures) + MCP
  OAuth provider RFC contracts (RFC 9728, 8414, 7591, 7009) tested
  in-process via `httpx.ASGITransport` against `mcp.streamable_http_app()`.
- **Inventory deliverable:** raw `pytest -v` output. No separately
  generated artifact — fewer things to keep in sync.
- **Existing tests:** migrated fully where they fit a per-endpoint spec;
  helpers and internal-storage tests move to `test_helpers.py`,
  `test_client_transport.py`, or `test_redis_store.py`. No assertion
  is dropped; migration tracking confirms one-to-one coverage during
  execution.
- **Live OAuth flow test:** kept, gated behind `@pytest.mark.live` so
  the default `pytest` run is deterministic and gates deploy.

## Open questions

None at design-doc time.
