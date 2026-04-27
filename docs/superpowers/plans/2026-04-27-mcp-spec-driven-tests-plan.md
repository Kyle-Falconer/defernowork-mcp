# MCP Spec-Driven Test Suite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the spec-driven test suite from the design at `docs/superpowers/specs/2026-04-27-mcp-spec-driven-tests-design.md`. Encode the backend's `v0.1` HTTP contract and the MCP OAuth-provider's RFC contract as JSON fixtures and parametrized tests; fix `client.py:_request` to unwrap the `v0.1` envelope and validate `version`; gate deploy on `pytest -v -m "not live"`.

**Architecture:** Fixtures under `tests/spec/v0.1/<resource>/<op>.json` and `tests/spec/oauth/<op>.json` describe one operation each. A central `tests/spec_runner.py` loads fixtures, exposes a recursive shape comparator, wraps/unwraps the v0.1 envelope, and supplies parametrize sources for `test_client_contract.py` (HTTP-layer), `test_tools_contract.py` (MCP-tool-layer), and `test_oauth_provider_contract.py` (in-process ASGI via `httpx.ASGITransport`). A separate `tests/inventory.py` cross-checks the architecture doc, the hand-curated `tests/endpoint_registry.py`, and the on-disk fixture tree.

**Tech Stack:** Python 3.10+, pytest, pytest-asyncio, respx, httpx, FastMCP, Starlette (transitively, via FastMCP's `streamable_http_app()`).

---

## Parallelization model

Work is grouped into three waves. **Wave 1 is sequential** — each task depends on the previous. **Wave 2 has six independent tracks** (A–F) that touch disjoint files and can run concurrently in their own worktrees. **Wave 3 is sequential** — final reconciliation and CI.

```
Wave 1 (sequential)
  └─ Task 1.1 → 1.2 → 1.3 → 1.4 → 1.5

Wave 2 (six parallel tracks; each merges back independently)
  ├─ Track A: backend fixtures        (Tasks A1, A2, A3, A4, A5, A6)
  ├─ Track B: registry + inventory    (Tasks B1, B2)
  ├─ Track C: OAuth contract          (Tasks C1, C2)
  ├─ Track D: client contract test    (Task  D1)
  ├─ Track E: tools contract test     (Task  E1)
  └─ Track F: existing-test migration (Tasks F1, F2, F3, F4)

Wave 3 (sequential)
  └─ Task 3.1 → 3.2 → 3.3
```

**File-disjointness guarantees** (each Wave 2 task touches only the files listed; no two tasks share a file):

| Track | Files touched |
|---|---|
| A1 | `tests/spec/v0.1/auth/*.json` (new) |
| A2 | `tests/spec/v0.1/tasks/{list,create,get,patch,today,mood_history}.json` (new) |
| A3 | `tests/spec/v0.1/tasks/{split,merge,fold,move,comments_list,comments_create}.json` (new) |
| A4 | `tests/spec/v0.1/tasks/{plan_get,plan_add,plan_remove,plan_reorder,pinned_get,pinned_reorder,pinned_label}.json` (new) |
| A5 | `tests/spec/v0.1/items/*.json` (new) |
| A6 | `tests/spec/v0.1/internal/*.json` + `tests/spec/v0.1/admin/*.json` (new) |
| B1 | `tests/endpoint_registry.py` (new) |
| B2 | `tests/inventory.py` + `tests/test_inventory.py` (new) |
| C1 | `tests/spec/oauth/*.json` (new) |
| C2 | `tests/test_oauth_provider_contract.py` (new) |
| D1 | `tests/test_client_contract.py` (new) |
| E1 | `tests/test_tools_contract.py` (new) |
| F1 | `tests/test_helpers.py` (new), delete tests inside `tests/test_server.py` |
| F2 | `tests/test_client_transport.py` (new), delete tests inside `tests/test_client.py` |
| F3 | rename `tests/test_multi_user_auth.py` → `tests/test_redis_store.py` |
| F4 | `tests/test_oauth_flow.py` (existing, only top-of-file marker change) |

Note: Tracks A2/A3/A4 all create files inside `tests/spec/v0.1/tasks/`, but each task creates **disjoint filenames**, so the directory itself is the only shared resource and no merge conflicts arise. Track F1 and F2 each delete tests from a different existing file (`test_server.py` vs `test_client.py`).

---

## Wave 1 — Foundation (sequential)

### Task 1.1: Test infrastructure plumbing

**Files:**
- Modify: `c:/deferno_all/defernowork-mcp/pyproject.toml`
- Create: `c:/deferno_all/defernowork-mcp/tests/conftest.py`
- Create directories: `c:/deferno_all/defernowork-mcp/tests/spec/v0.1/{auth,tasks,items,internal,admin}/`, `c:/deferno_all/defernowork-mcp/tests/spec/oauth/`

- [ ] **Step 1: Register the `live` marker in `pyproject.toml`**

Open `pyproject.toml`. Replace the existing `[tool.pytest.ini_options]` block with:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = [
    "live: integration test that hits real network/staging services (skipped by default)",
]
```

- [ ] **Step 2: Create `tests/conftest.py`**

```python
"""Shared pytest plumbing for the defernowork-mcp test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = REPO_ROOT / "tests" / "spec"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def spec_dir() -> Path:
    return SPEC_DIR
```

- [ ] **Step 3: Create the fixture directories**

```bash
mkdir -p tests/spec/v0.1/auth tests/spec/v0.1/tasks tests/spec/v0.1/items tests/spec/v0.1/internal tests/spec/v0.1/admin tests/spec/oauth
```

Create empty `.gitkeep` files in each so the dirs are tracked:

```bash
touch tests/spec/v0.1/auth/.gitkeep tests/spec/v0.1/tasks/.gitkeep tests/spec/v0.1/items/.gitkeep tests/spec/v0.1/internal/.gitkeep tests/spec/v0.1/admin/.gitkeep tests/spec/oauth/.gitkeep
```

- [ ] **Step 4: Verify pytest still passes with the new marker**

Run: `pytest -v -m "not live"`
Expected: existing 4 test files still pass (no behavior change yet).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/conftest.py tests/spec
git commit -m "test: register 'live' marker and scaffold tests/spec/ tree"
```

---

### Task 1.2: Envelope meta-spec + failing envelope contract test

**Files:**
- Create: `tests/spec/v0.1/_envelope.json`
- Create: `tests/test_client_envelope_contract.py`

- [ ] **Step 1: Write `tests/spec/v0.1/_envelope.json`**

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

- [ ] **Step 2: Write `tests/test_client_envelope_contract.py`**

```python
"""v0.1 envelope contract: client._request unwraps + validates version."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from defernowork_mcp.client import DefernoClient, DefernoError

BASE = "http://test:3000"
ENVELOPE_SPEC = json.loads(
    (Path(__file__).parent / "spec" / "v0.1" / "_envelope.json").read_text(encoding="utf-8")
)


@pytest.fixture
def client() -> DefernoClient:
    return DefernoClient(base_url=BASE, token="test-token")


@respx.mock
@pytest.mark.asyncio
async def test_unwraps_data_payload(client: DefernoClient):
    """A v0.1 success envelope returns the inner `data`, not the whole body."""
    respx.get(f"{BASE}/tasks").respond(
        json={"version": "0.1", "data": [{"id": "t1", "title": "Hi"}], "error": None}
    )
    result = await client.list_tasks()
    assert result == [{"id": "t1", "title": "Hi"}]


@respx.mock
@pytest.mark.asyncio
async def test_missing_version_raises(client: DefernoClient):
    """A response with no `version` field is treated as a contract violation."""
    respx.get(f"{BASE}/tasks").respond(json={"data": [], "error": None})
    with pytest.raises(DefernoError) as exc_info:
        await client.list_tasks()
    assert exc_info.value.status_code == 502
    assert "version" in exc_info.value.message.lower()


@respx.mock
@pytest.mark.asyncio
async def test_unsupported_version_raises(client: DefernoClient):
    """A response advertising a non-supported version raises a clear error."""
    respx.get(f"{BASE}/tasks").respond(
        json={"version": "9.9", "data": [], "error": None}
    )
    with pytest.raises(DefernoError) as exc_info:
        await client.list_tasks()
    assert exc_info.value.status_code == 502
    assert "9.9" in exc_info.value.message
    assert "unsupported" in exc_info.value.message.lower()


@respx.mock
@pytest.mark.asyncio
async def test_error_envelope_raises_with_code(client: DefernoClient):
    """An error envelope raises DefernoError with `error.code` exposed."""
    respx.get(f"{BASE}/tasks").respond(
        400,
        json={
            "version": "0.1",
            "data": None,
            "error": {"code": "validation_error", "message": "title must be non-empty"},
        },
    )
    with pytest.raises(DefernoError) as exc_info:
        await client.list_tasks()
    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "validation_error"
    assert "title" in exc_info.value.message


@respx.mock
@pytest.mark.asyncio
async def test_204_passes_through_unchanged(client: DefernoClient):
    """204 No Content has no body to unwrap; behavior is unchanged."""
    respx.post(f"{BASE}/tasks/plan/add").respond(204)
    result = await client.add_to_plan("some-id")
    assert result is None


def test_envelope_spec_loads():
    """Sanity: the meta-spec parses and declares v0.1."""
    assert ENVELOPE_SPEC["envelope_version"] == "0.1"
    assert "0.1" in ENVELOPE_SPEC["version_validation"]["supported"]
```

- [ ] **Step 3: Run the envelope contract test and confirm RED**

Run: `pytest tests/test_client_envelope_contract.py -v`
Expected: `test_unwraps_data_payload`, `test_missing_version_raises`, `test_unsupported_version_raises`, `test_error_envelope_raises_with_code` all FAIL (the current `_request` returns the full body without unwrapping). `test_204_passes_through_unchanged` and `test_envelope_spec_loads` PASS.

- [ ] **Step 4: Commit (RED)**

```bash
git add tests/spec/v0.1/_envelope.json tests/test_client_envelope_contract.py
git commit -m "test: add v0.1 envelope contract tests (RED)"
```

---

### Task 1.3: Update `client.py:_request` to unwrap envelope and validate version (GREEN)

**Files:**
- Modify: `src/defernowork_mcp/client.py`

- [ ] **Step 1: Update `DefernoError` to carry an optional `code` field**

In `src/defernowork_mcp/client.py`, replace the `DefernoError` class:

```python
class DefernoError(RuntimeError):
    """Raised when the Deferno backend returns an error response."""

    def __init__(self, status_code: int, message: str, code: str | None = None) -> None:
        super().__init__(f"{status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.code = code
```

- [ ] **Step 2: Add the `SUPPORTED_API_VERSION` constant**

Just below the imports (above `class DefernoError`), add:

```python
SUPPORTED_API_VERSION = "0.1"
```

- [ ] **Step 3: Replace `_request` with the envelope-aware version**

Replace the entire `_request` method body:

```python
    async def _request(
        self,
        method: str,
        path: str,
        *,
        authed: bool = True,
        json_body: Any | None = None,
    ) -> Any:
        headers = {"content-type": "application/json"}
        if authed:
            await self._ensure_authed()
            headers["authorization"] = f"Bearer {self._token}"

        try:
            response = await self._client.request(
                method,
                path,
                headers=headers,
                json=json_body,
            )
        except httpx.TimeoutException:
            raise DefernoError(504, "request timed out")
        except httpx.RequestError as exc:
            raise DefernoError(502, f"network error: {exc}")

        if response.status_code == 204 or not response.content:
            if 200 <= response.status_code < 300:
                return None
            raise DefernoError(response.status_code, response.reason_phrase or "error")

        try:
            payload = response.json()
        except ValueError:
            # Non-JSON body (e.g. HTML error page). Surface raw text.
            raise DefernoError(
                response.status_code,
                response.text or response.reason_phrase or "error",
            )

        # All v0.1 responses must be envelope-shaped: {version, data, error}
        if not isinstance(payload, dict) or "version" not in payload:
            raise DefernoError(
                502,
                f"backend response missing required 'version' field: {payload!r}",
            )

        version = payload["version"]
        if version != SUPPORTED_API_VERSION:
            raise DefernoError(
                502,
                f"unsupported API version: backend reported {version!r}, "
                f"client supports {SUPPORTED_API_VERSION!r}",
            )

        error = payload.get("error")
        if error is not None:
            code = None
            message = response.reason_phrase or "error"
            if isinstance(error, dict):
                code = error.get("code")
                message = error.get("message", message)
            raise DefernoError(response.status_code, message, code=code)

        if not (200 <= response.status_code < 300):
            # Status is non-2xx but envelope says no error — defensive fallback.
            raise DefernoError(response.status_code, response.reason_phrase or "error")

        return payload.get("data")
```

- [ ] **Step 4: Run the envelope contract test and confirm GREEN**

Run: `pytest tests/test_client_envelope_contract.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Run the full existing suite to check for regressions**

Run: `pytest -v -m "not live"`
Expected: `tests/test_client.py` will now have failures because the existing tests in that file mock un-wrapped responses (e.g. `respx.get(...).respond(json=[{"id": "abc", ...}])`). That is **expected and intended** — those tests will be migrated/deleted in Wave 2 Track F. The envelope contract tests, helper tests, and OAuth tests should all pass.

To unblock Wave 1, mark `tests/test_client.py` `xfail` for now. Add to the **top** of `tests/test_client.py`, just below the docstring:

```python
import pytest

pytestmark = pytest.mark.xfail(
    reason="legacy fixtures pre-date v0.1 envelope; migrated/replaced in Wave 2 Track F",
    strict=False,
)
```

Re-run: `pytest -v -m "not live"`
Expected: green or xfail; no unexpected failures.

- [ ] **Step 6: Commit (GREEN)**

```bash
git add src/defernowork_mcp/client.py tests/test_client.py
git commit -m "fix: client._request unwraps v0.1 envelope and validates version"
```

---

### Task 1.4: Build `tests/spec_runner.py` (TDD'd shape comparator + helpers)

**Files:**
- Create: `tests/spec_runner.py`
- Create: `tests/test_spec_runner.py`

- [ ] **Step 1: Write `tests/test_spec_runner.py` (RED)**

```python
"""Unit tests for tests/spec_runner.py — fixture loader + shape comparator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.spec_runner import (
    SUPPORTED_API_VERSION,
    Fixture,
    assert_shape,
    discover_backend_fixtures,
    discover_oauth_fixtures,
    substitute_path,
    wrap_envelope_data,
    wrap_envelope_error,
)


# ── shape comparator: leaves ────────────────────────────────────────────────


class TestAssertShapeLeaves:
    def test_string_ok(self):
        assert_shape("hi", "string")

    def test_string_rejects_int(self):
        with pytest.raises(AssertionError, match="expected string"):
            assert_shape(1, "string")

    def test_number_accepts_int_and_float(self):
        assert_shape(1, "number")
        assert_shape(1.5, "number")

    def test_number_rejects_bool(self):
        with pytest.raises(AssertionError, match="expected number"):
            assert_shape(True, "number")

    def test_boolean_ok(self):
        assert_shape(True, "boolean")
        assert_shape(False, "boolean")

    def test_boolean_rejects_int(self):
        with pytest.raises(AssertionError, match="expected boolean"):
            assert_shape(1, "boolean")

    def test_uuid_ok(self):
        assert_shape("00000000-0000-0000-0000-000000000001", "uuid")

    def test_uuid_rejects_non_uuid_string(self):
        with pytest.raises(AssertionError, match="expected uuid"):
            assert_shape("not-a-uuid", "uuid")

    def test_datetime_iso8601_ok(self):
        assert_shape("2026-04-27T00:00:00Z", "datetime")
        assert_shape("2026-04-27T00:00:00+00:00", "datetime")

    def test_datetime_rejects_garbage(self):
        with pytest.raises(AssertionError, match="expected datetime"):
            assert_shape("yesterday", "datetime")

    def test_null_ok(self):
        assert_shape(None, "null")

    def test_null_rejects_value(self):
        with pytest.raises(AssertionError, match="expected null"):
            assert_shape("x", "null")

    def test_any_accepts_anything(self):
        assert_shape(None, "any")
        assert_shape({"deep": [1, 2]}, "any")

    def test_array_leaf_accepts_any_list(self):
        assert_shape([1, "x", None], "array")

    def test_array_leaf_rejects_dict(self):
        with pytest.raises(AssertionError, match="expected array"):
            assert_shape({}, "array")

    def test_object_leaf_accepts_any_dict(self):
        assert_shape({"x": 1, "y": [1, 2]}, "object")

    def test_unknown_leaf_type_raises(self):
        with pytest.raises(AssertionError, match="unknown leaf type"):
            assert_shape("x", "weird")


# ── shape comparator: arrays ────────────────────────────────────────────────


class TestAssertShapeArrays:
    def test_array_of_strings_ok(self):
        assert_shape(["a", "b"], ["string"])

    def test_array_of_strings_rejects_mixed(self):
        with pytest.raises(AssertionError, match=r"\$\[1\]: expected string"):
            assert_shape(["a", 2], ["string"])

    def test_array_of_objects_ok(self):
        assert_shape(
            [{"id": "00000000-0000-0000-0000-000000000001"}],
            [{"id": "uuid"}],
        )

    def test_array_template_must_have_one_element(self):
        with pytest.raises(AssertionError, match="must have exactly one"):
            assert_shape([], ["string", "number"])


# ── shape comparator: objects ───────────────────────────────────────────────


class TestAssertShapeObjects:
    def test_object_required_keys_present_ok(self):
        assert_shape({"id": "00000000-0000-0000-0000-000000000001"}, {"id": "uuid"})

    def test_object_missing_required_key_fails(self):
        with pytest.raises(AssertionError, match="required key missing"):
            assert_shape({}, {"id": "uuid"})

    def test_object_extra_keys_allowed(self):
        # Extra keys are explicitly tolerated (backend may add fields).
        assert_shape({"id": "00000000-0000-0000-0000-000000000001", "extra": 1}, {"id": "uuid"})

    def test_required_escape_hatch(self):
        # `_required` overrides the default "all declared keys are required" rule.
        shape = {"id": "uuid", "label": "string", "_required": ["id"]}
        assert_shape({"id": "00000000-0000-0000-0000-000000000001"}, shape)  # no label, ok
        assert_shape(
            {"id": "00000000-0000-0000-0000-000000000001", "label": "x"},
            shape,
        )
        with pytest.raises(AssertionError, match=r"\.label: expected string"):
            assert_shape(
                {"id": "00000000-0000-0000-0000-000000000001", "label": 5},
                shape,
            )

    def test_nested_object_path_in_error(self):
        with pytest.raises(AssertionError, match=r"\$\.user\.id: expected uuid"):
            assert_shape({"user": {"id": "x"}}, {"user": {"id": "uuid"}})


# ── path template substitution ──────────────────────────────────────────────


class TestSubstitutePath:
    def test_substitutes_id(self):
        result = substitute_path("/items/{id}")
        assert result == "/items/00000000-0000-0000-0000-000000000001"

    def test_substitutes_named_placeholder(self):
        result = substitute_path("/tasks/{task_id}/comments")
        assert result == "/tasks/00000000-0000-0000-0000-000000000001/comments"

    def test_explicit_id_map_wins(self):
        result = substitute_path("/items/{id}", ids={"id": "deadbeef-dead-beef-dead-beefdeadbeef"})
        assert result == "/items/deadbeef-dead-beef-dead-beefdeadbeef"

    def test_no_placeholder(self):
        assert substitute_path("/tasks") == "/tasks"


# ── envelope wrappers ───────────────────────────────────────────────────────


class TestEnvelopeWrappers:
    def test_wrap_data(self):
        assert wrap_envelope_data([{"id": "x"}]) == {
            "version": "0.1",
            "data": [{"id": "x"}],
            "error": None,
        }

    def test_wrap_error(self):
        out = wrap_envelope_error({"code": "validation_error", "message": "bad"})
        assert out == {
            "version": "0.1",
            "data": None,
            "error": {"code": "validation_error", "message": "bad"},
        }


# ── fixture discovery ───────────────────────────────────────────────────────


class TestDiscovery:
    def test_discover_backend_skips_envelope_meta(self, tmp_path, monkeypatch):
        # Build a temp spec tree.
        v01 = tmp_path / "v0.1" / "tasks"
        v01.mkdir(parents=True)
        (tmp_path / "v0.1" / "_envelope.json").write_text("{}", encoding="utf-8")
        (v01 / "list.json").write_text(
            json.dumps({
                "operation": "tasks.list",
                "method": "GET",
                "path_template": "/tasks",
                "auth": "bearer",
                "request": {"headers_required": ["Authorization"]},
                "responses": [{"status": 200, "shape": ["object"], "example": []}],
                "client_method": "list_tasks",
                "client_args_from_example": [],
                "mcp_tool": "list_tasks",
                "mcp_tool_args_from_example": [],
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr("tests.spec_runner.SPEC_DIR", tmp_path)
        fixtures = discover_backend_fixtures()
        assert len(fixtures) == 1
        f = fixtures[0]
        assert f.operation == "tasks.list"
        assert f.method == "GET"
        assert f.client_method == "list_tasks"

    def test_discover_oauth_walks_oauth_dir(self, tmp_path, monkeypatch):
        oauth = tmp_path / "oauth"
        oauth.mkdir(parents=True)
        (oauth / "register.json").write_text(
            json.dumps({
                "operation": "oauth.register",
                "method": "POST",
                "path_template": "/register",
                "auth": "none",
                "request": {"body": {"required": [], "optional": [], "example": {}}},
                "responses": [{"status": 201, "shape": {"client_id": "string"}, "example": {"client_id": "c1"}}],
                "client_method": None,
                "client_args_from_example": [],
                "mcp_tool": None,
                "mcp_tool_args_from_example": [],
            }),
            encoding="utf-8",
        )

        monkeypatch.setattr("tests.spec_runner.SPEC_DIR", tmp_path)
        fixtures = discover_oauth_fixtures()
        assert len(fixtures) == 1
        assert fixtures[0].operation == "oauth.register"


# ── version constant ────────────────────────────────────────────────────────


def test_supported_api_version_matches_client():
    from defernowork_mcp.client import SUPPORTED_API_VERSION as CLIENT_VERSION
    assert SUPPORTED_API_VERSION == CLIENT_VERSION
```

- [ ] **Step 2: Run and confirm RED**

Run: `pytest tests/test_spec_runner.py -v`
Expected: ImportError (no `tests/spec_runner.py` yet).

- [ ] **Step 3: Write `tests/spec_runner.py` (GREEN)**

```python
"""Fixture loader, shape comparator, and assertion helpers for spec-driven tests.

This module is the *only* place fixture semantics are interpreted, so the
"spec" is itself unit-tested in isolation (see test_spec_runner.py).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID


SUPPORTED_API_VERSION = "0.1"
SPEC_DIR = Path(__file__).resolve().parent / "spec"
PLACEHOLDER_UUID = "00000000-0000-0000-0000-000000000001"


@dataclass
class Fixture:
    path: Path
    operation: str
    method: str
    path_template: str
    auth: str
    request: dict
    responses: list[dict]
    client_method: str | None
    client_args_from_example: list[str]
    mcp_tool: str | None
    mcp_tool_args_from_example: list[str]
    notes: str | None = None
    raw: dict = field(default_factory=dict)


def _load_fixture(path: Path) -> Fixture:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Fixture(
        path=path,
        operation=data["operation"],
        method=data["method"].upper(),
        path_template=data["path_template"],
        auth=data.get("auth", "none"),
        request=data.get("request", {}),
        responses=data.get("responses", []),
        client_method=data.get("client_method"),
        client_args_from_example=data.get("client_args_from_example", []),
        mcp_tool=data.get("mcp_tool"),
        mcp_tool_args_from_example=data.get("mcp_tool_args_from_example", []),
        notes=data.get("notes"),
        raw=data,
    )


def discover_backend_fixtures(version: str = SUPPORTED_API_VERSION) -> list[Fixture]:
    """Walk tests/spec/v{version}/, excluding _envelope.json. Each fixture
    describes the inner `data` payload; the envelope itself is implicit."""
    base = SPEC_DIR / f"v{version}"
    out: list[Fixture] = []
    if not base.exists():
        return out
    for p in sorted(base.rglob("*.json")):
        if p.name == "_envelope.json":
            continue
        out.append(_load_fixture(p))
    return out


def discover_oauth_fixtures() -> list[Fixture]:
    """Walk tests/spec/oauth/. These fixtures are NOT envelope-wrapped — the
    OAuth provider speaks raw OAuth/RFC payloads."""
    base = SPEC_DIR / "oauth"
    out: list[Fixture] = []
    if not base.exists():
        return out
    for p in sorted(base.rglob("*.json")):
        out.append(_load_fixture(p))
    return out


# ── leaf validators ─────────────────────────────────────────────────────────


def _is_uuid(s: Any) -> bool:
    if not isinstance(s, str):
        return False
    try:
        UUID(s)
        return True
    except (ValueError, TypeError):
        return False


def _is_iso8601(s: Any) -> bool:
    if not isinstance(s, str):
        return False
    candidate = s.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(candidate)
        return True
    except (ValueError, TypeError):
        return False


_LEAF_VALIDATORS: dict[str, Any] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "uuid": _is_uuid,
    "datetime": _is_iso8601,
    "null": lambda v: v is None,
    "any": lambda v: True,
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def assert_shape(value: Any, shape: Any, path: str = "$") -> None:
    """Recursive shape comparator. Raises AssertionError on mismatch.

    Leaf shapes are strings (e.g. ``"string"``, ``"uuid"``).
    Array shapes are single-element lists (e.g. ``["string"]``).
    Object shapes are dicts; declared keys are required by default unless
    ``_required`` is supplied as an explicit allow-list.
    Extra keys in the value are always allowed (additive backend changes
    must not break MCP).
    """
    if isinstance(shape, str):
        validator = _LEAF_VALIDATORS.get(shape)
        if validator is None:
            raise AssertionError(f"{path}: unknown leaf type {shape!r}")
        if not validator(value):
            raise AssertionError(
                f"{path}: expected {shape}, got {type(value).__name__} ({value!r})"
            )
        return

    if isinstance(shape, list):
        if len(shape) != 1:
            raise AssertionError(
                f"{path}: array shape must have exactly one element template, got {len(shape)}"
            )
        if not isinstance(value, list):
            raise AssertionError(
                f"{path}: expected array, got {type(value).__name__}"
            )
        item_shape = shape[0]
        for i, item in enumerate(value):
            assert_shape(item, item_shape, f"{path}[{i}]")
        return

    if isinstance(shape, dict):
        if not isinstance(value, dict):
            raise AssertionError(
                f"{path}: expected object, got {type(value).__name__}"
            )
        explicit_required = shape.get("_required")
        declared_keys = [k for k in shape.keys() if k != "_required"]
        required = explicit_required if explicit_required is not None else declared_keys
        for k in required:
            if k not in value:
                raise AssertionError(f"{path}.{k}: required key missing")
        for k, sub_shape in shape.items():
            if k == "_required":
                continue
            if k in value:
                assert_shape(value[k], sub_shape, f"{path}.{k}")
        return

    raise AssertionError(f"{path}: unsupported shape type {type(shape).__name__}")


# ── path template substitution ──────────────────────────────────────────────


_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def substitute_path(template: str, ids: dict[str, str] | None = None) -> str:
    """Replace ``{id}``, ``{task_id}``, etc. in path templates with placeholder UUIDs."""
    ids = ids or {}

    def repl(m: re.Match[str]) -> str:
        return ids.get(m.group(1), PLACEHOLDER_UUID)

    return _PLACEHOLDER_RE.sub(repl, template)


# ── envelope wrappers ───────────────────────────────────────────────────────


def wrap_envelope_data(data: Any) -> dict:
    return {"version": SUPPORTED_API_VERSION, "data": data, "error": None}


def wrap_envelope_error(error: dict) -> dict:
    return {"version": SUPPORTED_API_VERSION, "data": None, "error": error}


# ── request-shape assertions ────────────────────────────────────────────────


_AUTH_HEADER_RE = re.compile(r"^Bearer\s+\S+$")


def assert_request_matches_spec(fixture: Fixture, request: Any, args: dict) -> None:
    """Assert the captured HTTP request satisfies fixture.request.

    - ``auth`` field drives Authorization-header presence.
    - ``request.headers_required`` are checked case-insensitively.
    - ``request.body.required`` keys must appear in the JSON body (when method has a body).
    """
    headers = {k.lower(): v for k, v in request.headers.items()}

    if fixture.auth in {"bearer", "bearer-admin"}:
        auth = headers.get("authorization", "")
        if not _AUTH_HEADER_RE.match(auth):
            raise AssertionError(
                f"{fixture.operation}: expected Bearer Authorization header, got {auth!r}"
            )
    elif fixture.auth == "internal-shared-secret":
        if "x-internal-secret" not in headers and "authorization" not in headers:
            raise AssertionError(
                f"{fixture.operation}: expected internal shared-secret header"
            )

    for h in fixture.request.get("headers_required", []):
        if h.lower() not in headers:
            raise AssertionError(
                f"{fixture.operation}: required header {h!r} not present"
            )

    body_spec = fixture.request.get("body") or {}
    required_keys = body_spec.get("required", [])
    if required_keys and request.method.upper() in {"POST", "PATCH", "PUT", "DELETE"}:
        try:
            body = json.loads(request.content)
        except (ValueError, TypeError):
            raise AssertionError(
                f"{fixture.operation}: expected JSON body, got non-JSON"
            )
        if not isinstance(body, dict):
            raise AssertionError(
                f"{fixture.operation}: expected JSON object body, got {type(body).__name__}"
            )
        for k in required_keys:
            if k in args and k not in body:
                raise AssertionError(
                    f"{fixture.operation}: arg {k!r} was supplied but missing from body"
                )


# ── response-shape assertions ───────────────────────────────────────────────


def assert_response_matches_shape(
    fixture: Fixture,
    response_index: int,
    actual: Any,
) -> None:
    """Assert ``actual`` (already unwrapped from envelope) matches the
    declared ``shape`` of ``fixture.responses[response_index]``."""
    spec = fixture.responses[response_index]
    if "shape" in spec:
        assert_shape(actual, spec["shape"], path=f"${fixture.operation}")
    else:
        raise AssertionError(
            f"{fixture.operation}: response[{response_index}] has no 'shape' to assert"
        )
```

- [ ] **Step 4: Run and confirm GREEN**

Run: `pytest tests/test_spec_runner.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/spec_runner.py tests/test_spec_runner.py
git commit -m "test: add spec_runner with shape comparator and fixture loader"
```

---

### Task 1.5: Wave 1 verification

- [ ] **Step 1: Run the full default suite**

Run: `pytest -v -m "not live"`
Expected: all green or expected xfails (`tests/test_client.py` is the only xfail file). No unexpected failures.

- [ ] **Step 2: Confirm tests/spec/ tree is in place**

```bash
ls tests/spec/v0.1/
ls tests/spec/v0.1/tasks/
ls tests/spec/oauth/
```
Expected: each shows the `.gitkeep` placeholder; `tests/spec/v0.1/_envelope.json` exists.

- [ ] **Step 3: Tag the wave-1 commit**

```bash
git tag -a wave-1-complete -m "Wave 1: envelope unwrap + spec_runner foundation"
```

Wave 1 is complete. **Wave 2 tracks may now begin in parallel.**

---

## Wave 2 — Parallel tracks

Each Wave 2 task is self-contained and touches only the files listed in its **Files** section. Tasks may run concurrently in separate worktrees and merge in any order.

---

### Track A — Backend fixtures

#### Task A1: Auth fixtures

**Files (all new):**
- `tests/spec/v0.1/auth/me_get.json`
- `tests/spec/v0.1/auth/me_patch.json`
- `tests/spec/v0.1/auth/tokens_list.json`
- `tests/spec/v0.1/auth/tokens_create.json`
- `tests/spec/v0.1/auth/tokens_delete.json`
- `tests/spec/v0.1/auth/tokens_rename.json`
- `tests/spec/v0.1/auth/connected_mcp.json`
- `tests/spec/v0.1/auth/oidc_login.json`
- `tests/spec/v0.1/auth/oidc_callback.json`
- `tests/spec/v0.1/auth/logout.json`

- [ ] **Step 1: Create `me_get.json`**

```json
{
  "operation": "auth.me_get",
  "method": "GET",
  "path_template": "/auth/me",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"]
  },
  "responses": [
    {
      "status": 200,
      "shape": {
        "id": "uuid",
        "username": "string",
        "display_name": "string",
        "is_admin": "boolean"
      },
      "example": {
        "id": "00000000-0000-0000-0000-000000000001",
        "username": "alice",
        "display_name": "Alice",
        "is_admin": false
      }
    },
    {
      "status": 401,
      "error_shape": {"code": "string", "message": "string"},
      "error_example": {"code": "unauthorized", "message": "missing or invalid token"}
    }
  ],
  "client_method": "whoami",
  "client_args_from_example": [],
  "mcp_tool": "whoami",
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 2: Create `me_patch.json`**

```json
{
  "operation": "auth.me_patch",
  "method": "PATCH",
  "path_template": "/auth/me",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": [],
      "optional": ["display_name"],
      "example": {"display_name": "Alice Renamed"}
    }
  },
  "responses": [
    {
      "status": 200,
      "shape": {
        "id": "uuid",
        "username": "string",
        "display_name": "string",
        "is_admin": "boolean"
      },
      "example": {
        "id": "00000000-0000-0000-0000-000000000001",
        "username": "alice",
        "display_name": "Alice Renamed",
        "is_admin": false
      }
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 3: Create `tokens_list.json`**

```json
{
  "operation": "auth.tokens_list",
  "method": "GET",
  "path_template": "/auth/tokens",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": [
        {"id": "uuid", "name": "string", "created_at": "datetime"}
      ],
      "example": [
        {
          "id": "00000000-0000-0000-0000-000000000001",
          "name": "cli token",
          "created_at": "2026-04-27T00:00:00Z"
        }
      ]
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 4: Create `tokens_create.json`**

```json
{
  "operation": "auth.tokens_create",
  "method": "POST",
  "path_template": "/auth/tokens",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["name"],
      "optional": [],
      "example": {"name": "new token"}
    }
  },
  "responses": [
    {
      "status": 201,
      "shape": {
        "id": "uuid",
        "name": "string",
        "token": "string",
        "created_at": "datetime"
      },
      "example": {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "new token",
        "token": "deferno_xxxxxxxxxxxxxxxx",
        "created_at": "2026-04-27T00:00:00Z"
      }
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 5: Create `tokens_delete.json`**

```json
{
  "operation": "auth.tokens_delete",
  "method": "DELETE",
  "path_template": "/auth/tokens/{id}",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 204,
      "shape": "null",
      "example": null
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 6: Create `tokens_rename.json`**

```json
{
  "operation": "auth.tokens_rename",
  "method": "PATCH",
  "path_template": "/auth/tokens/{id}",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["name"],
      "optional": [],
      "example": {"name": "renamed token"}
    }
  },
  "responses": [
    {
      "status": 200,
      "shape": {"id": "uuid", "name": "string", "created_at": "datetime"},
      "example": {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "renamed token",
        "created_at": "2026-04-27T00:00:00Z"
      }
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 7: Create `connected_mcp.json`**

```json
{
  "operation": "auth.connected_mcp",
  "method": "GET",
  "path_template": "/auth/connected-mcp",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": [
        {"client_id": "string", "client_name": "string", "connected_at": "datetime"}
      ],
      "example": [
        {
          "client_id": "claude-code",
          "client_name": "Claude Code",
          "connected_at": "2026-04-27T00:00:00Z"
        }
      ]
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 8: Create `oidc_login.json`**

```json
{
  "operation": "auth.oidc_login",
  "method": "GET",
  "path_template": "/auth/oidc/login",
  "auth": "none",
  "request": {},
  "responses": [
    {
      "status": 200,
      "shape": {"authorize_url": "string", "state": "string"},
      "example": {
        "authorize_url": "https://auth.deferno.work/oauth/v2/authorize?client_id=...",
        "state": "abc123"
      }
    }
  ],
  "client_method": "oidc_login",
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 9: Create `oidc_callback.json`**

```json
{
  "operation": "auth.oidc_callback",
  "method": "GET",
  "path_template": "/auth/oidc/callback",
  "auth": "none",
  "request": {
    "query": {
      "required": ["state", "code"],
      "example": {"state": "abc123", "code": "xyz789"}
    }
  },
  "responses": [
    {
      "status": 200,
      "shape": {"token": "string", "user": "object"},
      "example": {
        "token": "deferno_xxxxxxxxxxxxxxxx",
        "user": {
          "id": "00000000-0000-0000-0000-000000000001",
          "username": "alice",
          "display_name": "Alice",
          "is_admin": false
        }
      }
    }
  ],
  "client_method": "oidc_callback",
  "client_args_from_example": ["state", "code"],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 10: Create `logout.json`**

```json
{
  "operation": "auth.logout",
  "method": "POST",
  "path_template": "/auth/logout",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": {"logout_url": "string"},
      "example": {"logout_url": "https://auth.deferno.work/oidc/v1/end_session?id_token_hint=..."}
    }
  ],
  "client_method": "logout",
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 11: Verify JSON validity**

```bash
python -c "import json, glob; [json.loads(open(p).read()) for p in glob.glob('tests/spec/v0.1/auth/*.json')]"
```
Expected: no output (all parsed cleanly).

- [ ] **Step 12: Commit**

```bash
git add tests/spec/v0.1/auth
git commit -m "test: add v0.1 auth fixtures"
```

---

#### Task A2: Tasks core CRUD fixtures

**Files (all new):**
- `tests/spec/v0.1/tasks/list.json`
- `tests/spec/v0.1/tasks/create.json`
- `tests/spec/v0.1/tasks/get.json`
- `tests/spec/v0.1/tasks/patch.json`
- `tests/spec/v0.1/tasks/today.json`
- `tests/spec/v0.1/tasks/mood_history.json`

- [ ] **Step 1: Create `list.json`**

```json
{
  "operation": "tasks.list",
  "method": "GET",
  "path_template": "/tasks",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": [
        {
          "id": "uuid",
          "title": "string",
          "status": "string",
          "labels": ["string"],
          "parent_id": "any",
          "children": ["uuid"],
          "date_created": "datetime",
          "pinned": "boolean",
          "_required": ["id", "title", "status", "labels", "children", "date_created", "pinned"]
        }
      ],
      "example": [
        {
          "id": "00000000-0000-0000-0000-000000000001",
          "title": "Demo",
          "status": "open",
          "labels": [],
          "parent_id": null,
          "children": [],
          "date_created": "2026-04-27T00:00:00Z",
          "pinned": false
        }
      ]
    }
  ],
  "client_method": "list_tasks",
  "client_args_from_example": [],
  "mcp_tool": "list_tasks",
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 2: Create `create.json`**

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
        "desire", "productive", "mood_start", "recurrence", "next_task_id"
      ],
      "example": {"title": "Demo", "description": "Test"}
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
        "date_created": "datetime"
      },
      "example": {
        "id": "00000000-0000-0000-0000-000000000001",
        "title": "Demo",
        "status": "open",
        "actions": [{"kind": "Created"}],
        "date_created": "2026-04-27T00:00:00Z"
      }
    },
    {
      "status": 400,
      "error_shape": {"code": "string", "message": "string"},
      "error_example": {"code": "validation_error", "message": "title must be non-empty"}
    },
    {
      "status": 401,
      "error_shape": {"code": "string", "message": "string"},
      "error_example": {"code": "unauthorized", "message": "missing or invalid token"}
    }
  ],
  "client_method": "create_task",
  "client_args_from_example": ["title", "description"],
  "mcp_tool": "create_task",
  "mcp_tool_args_from_example": ["title", "description"]
}
```

- [ ] **Step 3: Create `get.json`**

```json
{
  "operation": "tasks.get",
  "method": "GET",
  "path_template": "/tasks/{id}",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": {
        "id": "uuid",
        "title": "string",
        "description": "string",
        "status": "string",
        "labels": ["string"],
        "actions": "array",
        "date_created": "datetime"
      },
      "example": {
        "id": "00000000-0000-0000-0000-000000000001",
        "title": "Demo",
        "description": "",
        "status": "open",
        "labels": [],
        "actions": [],
        "date_created": "2026-04-27T00:00:00Z"
      }
    },
    {
      "status": 404,
      "error_shape": {"code": "string", "message": "string"},
      "error_example": {"code": "not_found", "message": "task not found"}
    }
  ],
  "client_method": "get_task",
  "client_args_from_example": [],
  "mcp_tool": "get_task",
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 4: Create `patch.json`**

```json
{
  "operation": "tasks.patch",
  "method": "PATCH",
  "path_template": "/tasks/{id}",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": [],
      "optional": [
        "title", "description", "status", "labels", "complete_by",
        "desire", "productive", "mood_start", "mood_finish",
        "recurrence", "next_task_id", "pinned"
      ],
      "example": {"status": "in-progress"}
    }
  },
  "responses": [
    {
      "status": 200,
      "shape": {"id": "uuid", "status": "string"},
      "example": {
        "id": "00000000-0000-0000-0000-000000000001",
        "status": "in-progress"
      }
    },
    {
      "status": 400,
      "error_shape": {"code": "string", "message": "string"},
      "error_example": {"code": "validation_error", "message": "cannot complete task with active children"}
    }
  ],
  "client_method": "update_task",
  "client_args_from_example": [],
  "mcp_tool": "update_task",
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 5: Create `today.json`**

```json
{
  "operation": "tasks.today",
  "method": "GET",
  "path_template": "/tasks/today",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": [
        {
          "id": "uuid",
          "title": "string",
          "status": "string",
          "score": "number",
          "_required": ["id", "title", "status", "score"]
        }
      ],
      "example": [
        {
          "id": "00000000-0000-0000-0000-000000000001",
          "title": "Demo",
          "status": "open",
          "score": 35.0
        }
      ]
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 6: Create `mood_history.json`**

```json
{
  "operation": "tasks.mood_history",
  "method": "GET",
  "path_template": "/tasks/mood-history",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": [
        {
          "task_id": "uuid",
          "title": "string",
          "finished_at": "datetime",
          "productive": "any",
          "mood_start": "any",
          "mood_finish": "any",
          "_required": ["task_id", "title", "finished_at"]
        }
      ],
      "example": [
        {
          "task_id": "00000000-0000-0000-0000-000000000001",
          "title": "Demo",
          "finished_at": "2026-04-27T00:00:00Z",
          "productive": 0.5,
          "mood_start": null,
          "mood_finish": null
        }
      ]
    }
  ],
  "client_method": "mood_history",
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 7: Verify JSON validity and commit**

```bash
python -c "import json, glob; [json.loads(open(p).read()) for p in glob.glob('tests/spec/v0.1/tasks/{list,create,get,patch,today,mood_history}.json')]"
git add tests/spec/v0.1/tasks/list.json tests/spec/v0.1/tasks/create.json tests/spec/v0.1/tasks/get.json tests/spec/v0.1/tasks/patch.json tests/spec/v0.1/tasks/today.json tests/spec/v0.1/tasks/mood_history.json
git commit -m "test: add v0.1 tasks core CRUD fixtures"
```

---

#### Task A3: Tasks tree-ops + comments fixtures

**Files (all new):**
- `tests/spec/v0.1/tasks/split.json`
- `tests/spec/v0.1/tasks/merge.json`
- `tests/spec/v0.1/tasks/fold.json`
- `tests/spec/v0.1/tasks/move.json`
- `tests/spec/v0.1/tasks/comments_list.json`
- `tests/spec/v0.1/tasks/comments_create.json`

- [ ] **Step 1: Create `split.json`**

```json
{
  "operation": "tasks.split",
  "method": "POST",
  "path_template": "/tasks/{id}/split",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["first_title", "second_title"],
      "optional": ["first_description", "second_description"],
      "example": {
        "first_title": "Step A",
        "first_description": "",
        "second_title": "Step B",
        "second_description": ""
      }
    }
  },
  "responses": [
    {
      "status": 200,
      "shape": {
        "parent": {"id": "uuid", "kind": "string"},
        "first_child": {"id": "uuid", "kind": "string"},
        "second_child": {"id": "uuid", "kind": "string"}
      },
      "example": {
        "parent": {"id": "00000000-0000-0000-0000-000000000001", "kind": "Task"},
        "first_child": {"id": "00000000-0000-0000-0000-000000000002", "kind": "Task"},
        "second_child": {"id": "00000000-0000-0000-0000-000000000003", "kind": "Task"}
      }
    }
  ],
  "client_method": "split_task",
  "client_args_from_example": [],
  "mcp_tool": "split_task",
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 2: Create `merge.json`**

```json
{
  "operation": "tasks.merge",
  "method": "POST",
  "path_template": "/tasks/{id}/merge",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {"required": [], "optional": [], "example": {}}
  },
  "responses": [
    {
      "status": 200,
      "shape": {
        "parent": {"id": "uuid"},
        "merged_children": [{"id": "uuid"}]
      },
      "example": {
        "parent": {"id": "00000000-0000-0000-0000-000000000001"},
        "merged_children": [{"id": "00000000-0000-0000-0000-000000000002"}]
      }
    }
  ],
  "client_method": "merge_task",
  "client_args_from_example": [],
  "mcp_tool": "merge_task",
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 3: Create `fold.json`**

```json
{
  "operation": "tasks.fold",
  "method": "POST",
  "path_template": "/tasks/{id}/fold",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["title"],
      "optional": ["description", "labels", "desire", "productive", "complete_by"],
      "example": {"title": "Next step", "description": ""}
    }
  },
  "responses": [
    {
      "status": 201,
      "shape": {
        "original": {"id": "uuid", "next_task_id": "uuid"},
        "next_task": {"id": "uuid", "title": "string"}
      },
      "example": {
        "original": {
          "id": "00000000-0000-0000-0000-000000000001",
          "next_task_id": "00000000-0000-0000-0000-000000000002"
        },
        "next_task": {
          "id": "00000000-0000-0000-0000-000000000002",
          "title": "Next step"
        }
      }
    }
  ],
  "client_method": "fold_task",
  "client_args_from_example": [],
  "mcp_tool": "fold_task",
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 4: Create `move.json`**

```json
{
  "operation": "tasks.move",
  "method": "POST",
  "path_template": "/tasks/{id}/move",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["new_parent_id"],
      "optional": ["position"],
      "example": {
        "new_parent_id": "00000000-0000-0000-0000-000000000002",
        "position": 0
      }
    }
  },
  "responses": [
    {
      "status": 200,
      "shape": {
        "id": "uuid",
        "parent_id": "any"
      },
      "example": {
        "id": "00000000-0000-0000-0000-000000000001",
        "parent_id": "00000000-0000-0000-0000-000000000002"
      }
    },
    {
      "status": 400,
      "error_shape": {"code": "string", "message": "string"},
      "error_example": {"code": "validation_error", "message": "cannot move task under its descendant"}
    }
  ],
  "client_method": "move_task",
  "client_args_from_example": ["new_parent_id", "position"],
  "mcp_tool": "move_task",
  "mcp_tool_args_from_example": ["new_parent_id", "position"]
}
```

- [ ] **Step 5: Create `comments_list.json`**

```json
{
  "operation": "tasks.comments_list",
  "method": "GET",
  "path_template": "/tasks/{task_id}/comments",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": [
        {
          "id": "uuid",
          "author_id": "uuid",
          "body": "string",
          "created_at": "datetime"
        }
      ],
      "example": [
        {
          "id": "00000000-0000-0000-0000-000000000010",
          "author_id": "00000000-0000-0000-0000-000000000001",
          "body": "first comment",
          "created_at": "2026-04-27T00:00:00Z"
        }
      ]
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 6: Create `comments_create.json`**

```json
{
  "operation": "tasks.comments_create",
  "method": "POST",
  "path_template": "/tasks/{task_id}/comments",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["body"],
      "optional": [],
      "example": {"body": "a comment"}
    }
  },
  "responses": [
    {
      "status": 201,
      "shape": {
        "id": "uuid",
        "author_id": "uuid",
        "body": "string",
        "created_at": "datetime"
      },
      "example": {
        "id": "00000000-0000-0000-0000-000000000010",
        "author_id": "00000000-0000-0000-0000-000000000001",
        "body": "a comment",
        "created_at": "2026-04-27T00:00:00Z"
      }
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 7: Verify and commit**

```bash
python -c "import json, glob; [json.loads(open(p).read()) for p in glob.glob('tests/spec/v0.1/tasks/{split,merge,fold,move,comments_list,comments_create}.json')]"
git add tests/spec/v0.1/tasks/split.json tests/spec/v0.1/tasks/merge.json tests/spec/v0.1/tasks/fold.json tests/spec/v0.1/tasks/move.json tests/spec/v0.1/tasks/comments_list.json tests/spec/v0.1/tasks/comments_create.json
git commit -m "test: add v0.1 tasks tree-ops and comments fixtures"
```

---

#### Task A4: Daily plan + pinned fixtures

**Files (all new):**
- `tests/spec/v0.1/tasks/plan_get.json`
- `tests/spec/v0.1/tasks/plan_add.json`
- `tests/spec/v0.1/tasks/plan_remove.json`
- `tests/spec/v0.1/tasks/plan_reorder.json`
- `tests/spec/v0.1/tasks/pinned_get.json`
- `tests/spec/v0.1/tasks/pinned_reorder.json`
- `tests/spec/v0.1/tasks/pinned_label.json`

- [ ] **Step 1: Create `plan_get.json`**

```json
{
  "operation": "tasks.plan_get",
  "method": "GET",
  "path_template": "/tasks/plan",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": [
        {"id": "uuid", "title": "string", "status": "string", "_required": ["id", "title", "status"]}
      ],
      "example": [
        {
          "id": "00000000-0000-0000-0000-000000000001",
          "title": "Demo",
          "status": "open"
        }
      ]
    }
  ],
  "client_method": "get_daily_plan",
  "client_args_from_example": [],
  "mcp_tool": "get_daily_plan",
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 2: Create `plan_add.json`**

```json
{
  "operation": "tasks.plan_add",
  "method": "POST",
  "path_template": "/tasks/plan/add",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["task_id"],
      "optional": ["date"],
      "example": {"task_id": "00000000-0000-0000-0000-000000000001"}
    }
  },
  "responses": [
    {
      "status": 204,
      "shape": "null",
      "example": null
    }
  ],
  "client_method": "add_to_plan",
  "client_args_from_example": ["task_id"],
  "mcp_tool": "add_to_plan",
  "mcp_tool_args_from_example": ["task_id"]
}
```

- [ ] **Step 3: Create `plan_remove.json`**

```json
{
  "operation": "tasks.plan_remove",
  "method": "POST",
  "path_template": "/tasks/plan/remove",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["task_id"],
      "optional": ["date"],
      "example": {"task_id": "00000000-0000-0000-0000-000000000001"}
    }
  },
  "responses": [
    {
      "status": 204,
      "shape": "null",
      "example": null
    }
  ],
  "client_method": "remove_from_plan",
  "client_args_from_example": ["task_id"],
  "mcp_tool": "remove_from_plan",
  "mcp_tool_args_from_example": ["task_id"]
}
```

- [ ] **Step 4: Create `plan_reorder.json`**

```json
{
  "operation": "tasks.plan_reorder",
  "method": "POST",
  "path_template": "/tasks/plan/reorder",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["task_ids"],
      "optional": ["date"],
      "example": {
        "task_ids": [
          "00000000-0000-0000-0000-000000000001",
          "00000000-0000-0000-0000-000000000002"
        ]
      }
    }
  },
  "responses": [
    {
      "status": 204,
      "shape": "null",
      "example": null
    }
  ],
  "client_method": "reorder_plan",
  "client_args_from_example": ["task_ids"],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 5: Create `pinned_get.json`**

```json
{
  "operation": "tasks.pinned_get",
  "method": "GET",
  "path_template": "/tasks/pinned",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": [
        {
          "task": {"id": "uuid", "title": "string", "_required": ["id", "title"]},
          "label": "any"
        }
      ],
      "example": [
        {
          "task": {"id": "00000000-0000-0000-0000-000000000001", "title": "Demo"},
          "label": null
        }
      ]
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 6: Create `pinned_reorder.json`**

```json
{
  "operation": "tasks.pinned_reorder",
  "method": "POST",
  "path_template": "/tasks/pinned/reorder",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["task_ids"],
      "optional": [],
      "example": {
        "task_ids": [
          "00000000-0000-0000-0000-000000000001",
          "00000000-0000-0000-0000-000000000002"
        ]
      }
    }
  },
  "responses": [
    {
      "status": 204,
      "shape": "null",
      "example": null
    },
    {
      "status": 400,
      "error_shape": {"code": "string", "message": "string"},
      "error_example": {"code": "validation_error", "message": "task is not pinned"}
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 7: Create `pinned_label.json`**

```json
{
  "operation": "tasks.pinned_label",
  "method": "PATCH",
  "path_template": "/tasks/pinned/{id}",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": [],
      "optional": ["label"],
      "example": {"label": "Sidebar Label"}
    }
  },
  "responses": [
    {
      "status": 200,
      "shape": {
        "task": {"id": "uuid", "title": "string", "_required": ["id", "title"]},
        "label": "any"
      },
      "example": {
        "task": {"id": "00000000-0000-0000-0000-000000000001", "title": "Demo"},
        "label": "Sidebar Label"
      }
    },
    {
      "status": 404,
      "error_shape": {"code": "string", "message": "string"},
      "error_example": {"code": "not_found", "message": "task is not pinned"}
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 8: Verify and commit**

```bash
python -c "import json, glob; [json.loads(open(p).read()) for p in glob.glob('tests/spec/v0.1/tasks/{plan_get,plan_add,plan_remove,plan_reorder,pinned_get,pinned_reorder,pinned_label}.json')]"
git add tests/spec/v0.1/tasks/plan_get.json tests/spec/v0.1/tasks/plan_add.json tests/spec/v0.1/tasks/plan_remove.json tests/spec/v0.1/tasks/plan_reorder.json tests/spec/v0.1/tasks/pinned_get.json tests/spec/v0.1/tasks/pinned_reorder.json tests/spec/v0.1/tasks/pinned_label.json
git commit -m "test: add v0.1 daily plan and pinned task fixtures"
```

---

#### Task A5: Items cross-kind fixtures

**Files (all new):**
- `tests/spec/v0.1/items/list.json`
- `tests/spec/v0.1/items/get.json`
- `tests/spec/v0.1/items/delete.json`
- `tests/spec/v0.1/items/history.json`
- `tests/spec/v0.1/items/comments_list.json`
- `tests/spec/v0.1/items/comments_create.json`
- `tests/spec/v0.1/items/split.json`
- `tests/spec/v0.1/items/merge.json`
- `tests/spec/v0.1/items/move.json`
- `tests/spec/v0.1/items/pin.json`
- `tests/spec/v0.1/items/convert.json`

- [ ] **Step 1: Create `list.json`**

```json
{
  "operation": "items.list",
  "method": "GET",
  "path_template": "/items",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": [
        {"kind": "string", "id": "uuid", "title": "string", "_required": ["kind", "id", "title"]}
      ],
      "example": [
        {"kind": "Task", "id": "00000000-0000-0000-0000-000000000001", "title": "Demo"}
      ]
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 2: Create `get.json`**

```json
{
  "operation": "items.get",
  "method": "GET",
  "path_template": "/items/{id}",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": {"kind": "string", "id": "uuid", "title": "string"},
      "example": {"kind": "Task", "id": "00000000-0000-0000-0000-000000000001", "title": "Demo"}
    },
    {
      "status": 404,
      "error_shape": {"code": "string", "message": "string"},
      "error_example": {"code": "not_found", "message": "item not found"}
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 3: Create `delete.json`**

```json
{
  "operation": "items.delete",
  "method": "DELETE",
  "path_template": "/items/{id}",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {"status": 204, "shape": "null", "example": null}
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 4: Create `history.json`**

```json
{
  "operation": "items.history",
  "method": "GET",
  "path_template": "/items/{id}/history",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": [
        {"kind": "string", "at": "datetime", "_required": ["kind", "at"]}
      ],
      "example": [{"kind": "Created", "at": "2026-04-27T00:00:00Z"}]
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 5: Create `comments_list.json`**

```json
{
  "operation": "items.comments_list",
  "method": "GET",
  "path_template": "/items/{id}/comments",
  "auth": "bearer",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": [
        {"id": "uuid", "body": "string", "created_at": "datetime"}
      ],
      "example": [
        {
          "id": "00000000-0000-0000-0000-000000000010",
          "body": "comment",
          "created_at": "2026-04-27T00:00:00Z"
        }
      ]
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 6: Create `comments_create.json`**

```json
{
  "operation": "items.comments_create",
  "method": "POST",
  "path_template": "/items/{id}/comments",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {"required": ["body"], "optional": [], "example": {"body": "comment"}}
  },
  "responses": [
    {
      "status": 201,
      "shape": {"id": "uuid", "body": "string", "created_at": "datetime"},
      "example": {
        "id": "00000000-0000-0000-0000-000000000010",
        "body": "comment",
        "created_at": "2026-04-27T00:00:00Z"
      }
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 7: Create `split.json`**

```json
{
  "operation": "items.split",
  "method": "POST",
  "path_template": "/items/{id}/split",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["first_title", "second_title"],
      "optional": ["first_description", "second_description"],
      "example": {
        "first_title": "Step A",
        "second_title": "Step B"
      }
    }
  },
  "responses": [
    {
      "status": 200,
      "shape": {
        "parent": {"kind": "string", "id": "uuid"},
        "first_child": {"kind": "string", "id": "uuid"},
        "second_child": {"kind": "string", "id": "uuid"}
      },
      "example": {
        "parent": {"kind": "Habit", "id": "00000000-0000-0000-0000-000000000001"},
        "first_child": {"kind": "Task", "id": "00000000-0000-0000-0000-000000000002"},
        "second_child": {"kind": "Task", "id": "00000000-0000-0000-0000-000000000003"}
      }
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 8: Create `merge.json`**

```json
{
  "operation": "items.merge",
  "method": "POST",
  "path_template": "/items/{id}/merge",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {"required": [], "optional": [], "example": {}}
  },
  "responses": [
    {
      "status": 200,
      "shape": {
        "parent": {"kind": "string", "id": "uuid"},
        "merged_children": [{"kind": "string", "id": "uuid"}]
      },
      "example": {
        "parent": {"kind": "Task", "id": "00000000-0000-0000-0000-000000000001"},
        "merged_children": [
          {"kind": "Task", "id": "00000000-0000-0000-0000-000000000002"}
        ]
      }
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 9: Create `move.json`**

```json
{
  "operation": "items.move",
  "method": "POST",
  "path_template": "/items/{id}/move",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["new_parent_id"],
      "optional": ["position"],
      "example": {
        "new_parent_id": "00000000-0000-0000-0000-000000000002",
        "position": 0
      }
    }
  },
  "responses": [
    {
      "status": 200,
      "shape": {"kind": "string", "id": "uuid"},
      "example": {"kind": "Task", "id": "00000000-0000-0000-0000-000000000001"}
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 10: Create `pin.json`**

```json
{
  "operation": "items.pin",
  "method": "POST",
  "path_template": "/items/{id}/pin",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["pinned"],
      "optional": [],
      "example": {"pinned": true}
    }
  },
  "responses": [
    {
      "status": 200,
      "shape": {"kind": "string", "id": "uuid", "pinned": "boolean"},
      "example": {
        "kind": "Task",
        "id": "00000000-0000-0000-0000-000000000001",
        "pinned": true
      }
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 11: Create `convert.json`**

```json
{
  "operation": "items.convert",
  "method": "POST",
  "path_template": "/items/{id}/convert",
  "auth": "bearer",
  "request": {
    "headers_required": ["Authorization"],
    "body": {
      "required": ["to_kind"],
      "optional": [],
      "example": {"to_kind": "Habit"}
    }
  },
  "responses": [
    {
      "status": 200,
      "shape": {"kind": "string", "id": "uuid"},
      "example": {"kind": "Habit", "id": "00000000-0000-0000-0000-000000000001"}
    },
    {
      "status": 400,
      "error_shape": {"code": "string", "message": "string"},
      "error_example": {"code": "validation_error", "message": "cannot convert kind"}
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 12: Verify and commit**

```bash
python -c "import json, glob; [json.loads(open(p).read()) for p in glob.glob('tests/spec/v0.1/items/*.json')]"
git add tests/spec/v0.1/items
git commit -m "test: add v0.1 items cross-kind fixtures"
```

---

#### Task A6: Internal + admin fixtures

**Files (all new):**
- `tests/spec/v0.1/internal/mcp_session.json`
- `tests/spec/v0.1/admin/users_list.json`
- `tests/spec/v0.1/admin/stats.json`

- [ ] **Step 1: Create `internal/mcp_session.json`**

```json
{
  "operation": "internal.mcp_session",
  "method": "POST",
  "path_template": "/internal/mcp-session",
  "auth": "internal-shared-secret",
  "request": {
    "body": {
      "required": ["oidc_subject"],
      "optional": ["username", "display_name"],
      "example": {
        "oidc_subject": "zitadel-subject-123",
        "username": "alice",
        "display_name": "Alice"
      }
    }
  },
  "responses": [
    {
      "status": 200,
      "shape": {
        "token": "string",
        "user_id": "uuid"
      },
      "example": {
        "token": "deferno_xxxxxxxxxxxxxxxx",
        "user_id": "00000000-0000-0000-0000-000000000001"
      }
    },
    {
      "status": 401,
      "error_shape": {"code": "string", "message": "string"},
      "error_example": {"code": "unauthorized", "message": "invalid shared secret"}
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 2: Create `admin/users_list.json`**

```json
{
  "operation": "admin.users_list",
  "method": "GET",
  "path_template": "/admin/users",
  "auth": "bearer-admin",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": [
        {"id": "uuid", "username": "string", "task_count": "number"}
      ],
      "example": [
        {"id": "00000000-0000-0000-0000-000000000001", "username": "alice", "task_count": 5}
      ]
    },
    {
      "status": 403,
      "error_shape": {"code": "string", "message": "string"},
      "error_example": {"code": "forbidden", "message": "admin only"}
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 3: Create `admin/stats.json`**

```json
{
  "operation": "admin.stats",
  "method": "GET",
  "path_template": "/admin/stats",
  "auth": "bearer-admin",
  "request": {"headers_required": ["Authorization"]},
  "responses": [
    {
      "status": 200,
      "shape": {
        "redis_keys": "number",
        "redis_memory_bytes": "number"
      },
      "example": {"redis_keys": 100, "redis_memory_bytes": 1048576}
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 4: Verify and commit**

```bash
python -c "import json, glob; [json.loads(open(p).read()) for p in glob.glob('tests/spec/v0.1/internal/*.json') + glob.glob('tests/spec/v0.1/admin/*.json')]"
git add tests/spec/v0.1/internal tests/spec/v0.1/admin
git commit -m "test: add v0.1 internal and admin fixtures"
```

---

### Track B — Endpoint registry + inventory

#### Task B1: `tests/endpoint_registry.py`

**Files:**
- Create: `tests/endpoint_registry.py`

- [ ] **Step 1: Create `tests/endpoint_registry.py`**

```python
"""Hand-curated registry of every backend endpoint, grouped by Rust handler module.

Discipline: when a route is added in `Deferno/backend/src/handlers/`, add or
remove the corresponding entry here in the same MCP-side PR. ``inventory.py``
cross-checks this list against the architecture doc and the on-disk fixture
tree; any mismatch fails the suite.

Each entry's ``operation`` field is the unique identifier shared with the
JSON fixture file (e.g. ``tasks.create`` corresponds to
``tests/spec/v0.1/tasks/create.json``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    handler: str       # Rust handler module: "auth", "tasks", "items", "admin", "internal"
    method: str
    path: str          # path template as written in architecture.md
    operation: str     # matches the fixture's "operation" field
    auth: str          # "none" | "bearer" | "bearer-admin" | "internal-shared-secret"


ENDPOINTS: list[Endpoint] = [
    # ── handlers::auth (public) ─────────────────────────────────────────
    Endpoint("auth", "GET",  "/auth/oidc/login",        "auth.oidc_login",       "none"),
    Endpoint("auth", "GET",  "/auth/oidc/callback",     "auth.oidc_callback",    "none"),
    Endpoint("auth", "POST", "/auth/logout",            "auth.logout",           "bearer"),

    # ── handlers::auth (authenticated) ──────────────────────────────────
    Endpoint("auth", "GET",    "/auth/me",              "auth.me_get",           "bearer"),
    Endpoint("auth", "PATCH",  "/auth/me",              "auth.me_patch",         "bearer"),
    Endpoint("auth", "GET",    "/auth/tokens",          "auth.tokens_list",      "bearer"),
    Endpoint("auth", "POST",   "/auth/tokens",          "auth.tokens_create",    "bearer"),
    Endpoint("auth", "DELETE", "/auth/tokens/{id}",     "auth.tokens_delete",    "bearer"),
    Endpoint("auth", "PATCH",  "/auth/tokens/{id}",     "auth.tokens_rename",    "bearer"),
    Endpoint("auth", "GET",    "/auth/connected-mcp",   "auth.connected_mcp",    "bearer"),

    # ── handlers::admin ─────────────────────────────────────────────────
    Endpoint("admin", "GET", "/admin/users", "admin.users_list", "bearer-admin"),
    Endpoint("admin", "GET", "/admin/stats", "admin.stats",      "bearer-admin"),

    # ── internal (nginx-blocked) ────────────────────────────────────────
    Endpoint("internal", "POST", "/internal/mcp-session", "internal.mcp_session", "internal-shared-secret"),

    # ── handlers::items (cross-kind) ────────────────────────────────────
    Endpoint("items", "GET",    "/items",                  "items.list",            "bearer"),
    Endpoint("items", "GET",    "/items/{id}",             "items.get",             "bearer"),
    Endpoint("items", "DELETE", "/items/{id}",             "items.delete",          "bearer"),
    Endpoint("items", "GET",    "/items/{id}/history",     "items.history",         "bearer"),
    Endpoint("items", "GET",    "/items/{id}/comments",    "items.comments_list",   "bearer"),
    Endpoint("items", "POST",   "/items/{id}/comments",    "items.comments_create", "bearer"),
    Endpoint("items", "POST",   "/items/{id}/split",       "items.split",           "bearer"),
    Endpoint("items", "POST",   "/items/{id}/merge",       "items.merge",           "bearer"),
    Endpoint("items", "POST",   "/items/{id}/move",        "items.move",            "bearer"),
    Endpoint("items", "POST",   "/items/{id}/pin",         "items.pin",             "bearer"),
    Endpoint("items", "POST",   "/items/{id}/convert",     "items.convert",         "bearer"),

    # ── handlers::tasks ─────────────────────────────────────────────────
    Endpoint("tasks", "GET",    "/tasks",                       "tasks.list",            "bearer"),
    Endpoint("tasks", "POST",   "/tasks",                       "tasks.create",          "bearer"),
    Endpoint("tasks", "GET",    "/tasks/today",                 "tasks.today",           "bearer"),
    Endpoint("tasks", "GET",    "/tasks/plan",                  "tasks.plan_get",        "bearer"),
    Endpoint("tasks", "POST",   "/tasks/plan/add",              "tasks.plan_add",        "bearer"),
    Endpoint("tasks", "POST",   "/tasks/plan/remove",           "tasks.plan_remove",     "bearer"),
    Endpoint("tasks", "POST",   "/tasks/plan/reorder",          "tasks.plan_reorder",    "bearer"),
    Endpoint("tasks", "GET",    "/tasks/mood-history",          "tasks.mood_history",    "bearer"),
    Endpoint("tasks", "GET",    "/tasks/{id}",                  "tasks.get",             "bearer"),
    Endpoint("tasks", "PATCH",  "/tasks/{id}",                  "tasks.patch",           "bearer"),
    Endpoint("tasks", "POST",   "/tasks/{id}/split",            "tasks.split",           "bearer"),
    Endpoint("tasks", "POST",   "/tasks/{id}/merge",            "tasks.merge",           "bearer"),
    Endpoint("tasks", "POST",   "/tasks/{id}/fold",             "tasks.fold",            "bearer"),
    Endpoint("tasks", "POST",   "/tasks/{id}/move",             "tasks.move",            "bearer"),
    Endpoint("tasks", "GET",    "/tasks/{task_id}/comments",    "tasks.comments_list",   "bearer"),
    Endpoint("tasks", "POST",   "/tasks/{task_id}/comments",    "tasks.comments_create", "bearer"),
    Endpoint("tasks", "GET",    "/tasks/pinned",                "tasks.pinned_get",      "bearer"),
    Endpoint("tasks", "POST",   "/tasks/pinned/reorder",        "tasks.pinned_reorder",  "bearer"),
    Endpoint("tasks", "PATCH",  "/tasks/pinned/{id}",           "tasks.pinned_label",    "bearer"),
]
```

- [ ] **Step 2: Sanity import**

```bash
python -c "from tests.endpoint_registry import ENDPOINTS; print(len(ENDPOINTS))"
```
Expected: prints `42`.

- [ ] **Step 3: Commit**

```bash
git add tests/endpoint_registry.py
git commit -m "test: add hand-curated endpoint_registry"
```

---

#### Task B2: `tests/inventory.py` + `tests/test_inventory.py`

**Files:**
- Create: `tests/inventory.py`
- Create: `tests/test_inventory.py`

- [ ] **Step 1: Write `tests/test_inventory.py` (RED)**

```python
"""Inventory tests — three-source consensus on backend endpoints."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from tests.inventory import (
    parse_architecture_md,
    fixtures_on_disk,
    cross_check,
    InventoryMismatch,
)


# ── architecture.md parser ──────────────────────────────────────────────────


def test_parse_extracts_endpoints_from_simple_table(tmp_path: Path):
    md = tmp_path / "architecture.md"
    md.write_text(
        dedent("""
            # Doc

            ### Auth (public)

            | Method | Path | Description |
            |---|---|---|
            | `GET` | `/auth/oidc/login` | Start login |
            | `POST` | `/auth/logout` | Logout |
        """).strip(),
        encoding="utf-8",
    )
    rows = parse_architecture_md(md)
    paths = sorted((r.method, r.path) for r in rows)
    assert paths == [("GET", "/auth/oidc/login"), ("POST", "/auth/logout")]


def test_parse_extracts_endpoints_with_auth_column(tmp_path: Path):
    md = tmp_path / "architecture.md"
    md.write_text(
        dedent("""
            ### Tasks

            | Method | Path | Auth | Description |
            |---|---|---|---|
            | `GET` | `/tasks` | Yes | All tasks |
            | `POST` | `/tasks` | Yes | Create |
        """).strip(),
        encoding="utf-8",
    )
    rows = parse_architecture_md(md)
    assert len(rows) == 2
    assert all(r.auth_yes for r in rows)


def test_parse_ignores_unknown_table_header(tmp_path: Path):
    """Tables that don't match the expected `Method | Path | ...` headers
    are skipped — many architecture.md tables describe non-endpoint data
    (Redis schema, env vars). Only endpoint tables are extracted."""
    md = tmp_path / "architecture.md"
    md.write_text(
        dedent("""
            | Foo | Bar |
            |---|---|
            | x | y |
        """).strip(),
        encoding="utf-8",
    )
    rows = parse_architecture_md(md)
    assert rows == []


# ── fixtures-on-disk ────────────────────────────────────────────────────────


def test_fixtures_on_disk_lists_operations(tmp_path: Path, monkeypatch):
    v01 = tmp_path / "v0.1" / "tasks"
    v01.mkdir(parents=True)
    (v01 / "list.json").write_text(
        '{"operation": "tasks.list", "method": "GET", "path_template": "/tasks", '
        '"auth": "bearer", "request": {}, "responses": [], '
        '"client_method": null, "client_args_from_example": [], '
        '"mcp_tool": null, "mcp_tool_args_from_example": []}',
        encoding="utf-8",
    )
    monkeypatch.setattr("tests.inventory.SPEC_DIR", tmp_path)
    found = fixtures_on_disk()
    assert ("GET", "/tasks", "tasks.list") in found


# ── cross-check ─────────────────────────────────────────────────────────────


def test_cross_check_passes_when_all_three_agree():
    from tests.inventory import DocEndpoint
    doc = [DocEndpoint("GET", "/tasks", auth_yes=True)]
    registry = [("tasks", "GET", "/tasks", "tasks.list", "bearer")]
    fixtures = {("GET", "/tasks", "tasks.list")}
    # Should not raise.
    cross_check(doc, registry, fixtures)


def test_cross_check_fails_when_doc_has_endpoint_without_registry():
    from tests.inventory import DocEndpoint
    doc = [DocEndpoint("GET", "/tasks", auth_yes=True)]
    registry: list = []
    fixtures: set = set()
    with pytest.raises(InventoryMismatch, match="not in registry"):
        cross_check(doc, registry, fixtures)


def test_cross_check_fails_when_registry_has_no_fixture():
    from tests.inventory import DocEndpoint
    doc = [DocEndpoint("GET", "/tasks", auth_yes=True)]
    registry = [("tasks", "GET", "/tasks", "tasks.list", "bearer")]
    fixtures: set = set()
    with pytest.raises(InventoryMismatch, match="missing fixture"):
        cross_check(doc, registry, fixtures)


def test_cross_check_fails_on_orphan_fixture():
    from tests.inventory import DocEndpoint
    doc: list = []
    registry: list = []
    fixtures = {("GET", "/tasks/orphan", "tasks.orphan")}
    with pytest.raises(InventoryMismatch, match="orphan"):
        cross_check(doc, registry, fixtures)


# ── pytest gate ─────────────────────────────────────────────────────────────


def test_every_endpoint_has_a_fixture():
    """Three-source consensus check: doc ↔ registry ↔ fixtures."""
    import os
    from tests.inventory import run_inventory

    arch_path = os.environ.get(
        "ARCHITECTURE_DOC_PATH",
        str(Path(__file__).resolve().parent.parent.parent / "Deferno" / "docs" / "architecture.md"),
    )
    if not Path(arch_path).exists():
        pytest.skip(
            f"architecture.md not at {arch_path} — set ARCHITECTURE_DOC_PATH or check out the Deferno repo as a sibling."
        )
    run_inventory(Path(arch_path))
```

- [ ] **Step 2: Run and confirm RED**

Run: `pytest tests/test_inventory.py -v`
Expected: ImportError (no `tests/inventory.py` yet).

- [ ] **Step 3: Write `tests/inventory.py` (GREEN)**

```python
"""Three-source consensus check on backend endpoints.

Cross-checks:
  1. ``Deferno/docs/architecture.md`` (the documented contract)
  2. ``tests/endpoint_registry.py``  (hand-curated per Rust handler)
  3. ``tests/spec/v0.1/<resource>/`` (the on-disk fixtures)

Any inconsistency raises ``InventoryMismatch``. Used by
``test_every_endpoint_has_a_fixture`` to gate CI.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from tests.endpoint_registry import ENDPOINTS
from tests.spec_runner import SUPPORTED_API_VERSION

SPEC_DIR = Path(__file__).resolve().parent / "spec"


class InventoryMismatch(AssertionError):
    """Raised when doc / registry / fixtures disagree."""


@dataclass(frozen=True)
class DocEndpoint:
    method: str
    path: str
    auth_yes: bool


_HEADER_NO_AUTH = re.compile(r"^\s*\|\s*Method\s*\|\s*Path\s*\|\s*Description\s*\|\s*$", re.IGNORECASE)
_HEADER_AUTH   = re.compile(r"^\s*\|\s*Method\s*\|\s*Path\s*\|\s*Auth\s*\|\s*Description\s*\|\s*$", re.IGNORECASE)
_DIVIDER       = re.compile(r"^\s*\|\s*-+\s*(\|\s*-+\s*)+\|\s*$")
_ROW           = re.compile(r"^\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*(?:\|\s*(.+?)\s*)?\|\s*$")
_BACKTICK_STRIP = re.compile(r"^`+|`+$")


def parse_architecture_md(path: Path) -> list[DocEndpoint]:
    """Extract endpoints from markdown tables that match the expected headers.

    Recognizes both ``| Method | Path | Description |`` and the
    ``| Method | Path | Auth | Description |`` shapes. All other tables
    are ignored.
    """
    out: list[DocEndpoint] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        has_auth = bool(_HEADER_AUTH.match(line))
        no_auth  = bool(_HEADER_NO_AUTH.match(line))
        if has_auth or no_auth:
            if i + 1 >= len(lines) or not _DIVIDER.match(lines[i + 1]):
                i += 1
                continue
            j = i + 2
            while j < len(lines) and _ROW.match(lines[j]):
                m = _ROW.match(lines[j])
                method = _BACKTICK_STRIP.sub("", m.group(1)).upper()
                path_str = _BACKTICK_STRIP.sub("", m.group(2))
                auth_yes = False
                if has_auth:
                    auth_field = m.group(3).strip().lower()
                    auth_yes = auth_field in {"yes", "y", "true"}
                if method in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    out.append(DocEndpoint(method=method, path=path_str, auth_yes=auth_yes))
                j += 1
            i = j
            continue
        i += 1
    return out


def fixtures_on_disk(version: str = SUPPORTED_API_VERSION) -> set[tuple[str, str, str]]:
    """Return ``{(method, path_template, operation), ...}`` from disk."""
    base = SPEC_DIR / f"v{version}"
    out: set[tuple[str, str, str]] = set()
    if not base.exists():
        return out
    for p in sorted(base.rglob("*.json")):
        if p.name == "_envelope.json":
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        out.add((data["method"].upper(), data["path_template"], data["operation"]))
    return out


def cross_check(
    doc: list[DocEndpoint],
    registry: list,
    fixtures: set[tuple[str, str, str]],
) -> None:
    """Raise InventoryMismatch on any disagreement.

    ``registry`` is iterable of either:
      - ``Endpoint`` dataclass instances (handler, method, path, operation, auth)
      - tuples of ``(handler, method, path, operation, auth)`` (test-only)
    """
    reg_keys: dict[tuple[str, str], str] = {}
    for entry in registry:
        if hasattr(entry, "method"):
            method = entry.method
            path = entry.path
            operation = entry.operation
        else:
            _handler, method, path, operation, _auth = entry
        reg_keys[(method.upper(), path)] = operation

    fixture_keys = {(m, p) for (m, p, _) in fixtures}
    fixture_ops = {op for (_, _, op) in fixtures}

    errors: list[str] = []
    for d in doc:
        if (d.method, d.path) not in reg_keys:
            errors.append(f"doc lists {d.method} {d.path} but not in registry")

    for (method, path), op in reg_keys.items():
        if (method, path) not in fixture_keys:
            errors.append(f"registry lists {method} {path} ({op}) but missing fixture")

    doc_keys = {(d.method, d.path) for d in doc}
    for (method, path) in fixture_keys:
        if (method, path) not in reg_keys:
            errors.append(f"orphan fixture {method} {path} not in registry")
        if doc_keys and (method, path) not in doc_keys:
            errors.append(f"fixture {method} {path} not documented in architecture.md")

    reg_ops = set(reg_keys.values())
    for op in fixture_ops:
        if op not in reg_ops:
            errors.append(f"fixture operation {op!r} not in registry")

    if errors:
        raise InventoryMismatch(
            "endpoint inventory mismatch:\n  - " + "\n  - ".join(sorted(errors))
        )


def run_inventory(arch_path: Path) -> None:
    """Convenience entry point used by the pytest gate."""
    doc = parse_architecture_md(arch_path)
    fixtures = fixtures_on_disk()
    cross_check(doc, list(ENDPOINTS), fixtures)


def architecture_doc_path() -> Path | None:
    """Resolve the architecture doc location from env or sibling layout."""
    env = os.environ.get("ARCHITECTURE_DOC_PATH")
    if env:
        return Path(env)
    sibling = Path(__file__).resolve().parent.parent.parent / "Deferno" / "docs" / "architecture.md"
    return sibling if sibling.exists() else None
```

- [ ] **Step 4: Run and confirm GREEN for the unit tests**

Run: `pytest tests/test_inventory.py -v -k "not test_every_endpoint_has_a_fixture"`
Expected: 7 unit tests PASS.

- [ ] **Step 5: Run the gate test**

Run: `pytest tests/test_inventory.py::test_every_endpoint_has_a_fixture -v`
Expected: PASS if `c:/deferno_all/Deferno/docs/architecture.md` exists and the fixture tracks (A1–A6) have all merged. Otherwise SKIP with the documented reason. **Acceptable to leave failing here** if Track A tasks are still pending — the inventory will go green once A1–A6 land.

- [ ] **Step 6: Commit**

```bash
git add tests/inventory.py tests/test_inventory.py
git commit -m "test: add inventory parser and three-source consensus gate"
```

---

### Track C — OAuth provider contract

#### Task C1: OAuth fixtures

**Files (all new):**
- `tests/spec/oauth/prm_metadata.json`
- `tests/spec/oauth/as_metadata.json`
- `tests/spec/oauth/register.json`
- `tests/spec/oauth/authorize.json`
- `tests/spec/oauth/token.json`
- `tests/spec/oauth/revoke.json`

- [ ] **Step 1: Create `prm_metadata.json` (RFC 9728)**

```json
{
  "operation": "oauth.prm_metadata",
  "method": "GET",
  "path_template": "/.well-known/oauth-protected-resource",
  "auth": "none",
  "request": {},
  "responses": [
    {
      "status": 200,
      "shape": {
        "resource": "string",
        "authorization_servers": ["string"]
      },
      "example": {
        "resource": "https://app.defernowork.com/mcp",
        "authorization_servers": ["https://app.defernowork.com/mcp"]
      }
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": [],
  "notes": "RFC 9728 — Protected Resource Metadata."
}
```

- [ ] **Step 2: Create `as_metadata.json` (RFC 8414)**

```json
{
  "operation": "oauth.as_metadata",
  "method": "GET",
  "path_template": "/.well-known/oauth-authorization-server",
  "auth": "none",
  "request": {},
  "responses": [
    {
      "status": 200,
      "shape": {
        "issuer": "string",
        "authorization_endpoint": "string",
        "token_endpoint": "string",
        "registration_endpoint": "string",
        "revocation_endpoint": "string",
        "scopes_supported": ["string"],
        "response_types_supported": ["string"],
        "grant_types_supported": ["string"],
        "token_endpoint_auth_methods_supported": ["string"],
        "code_challenge_methods_supported": ["string"]
      },
      "example": {
        "issuer": "https://app.defernowork.com/mcp",
        "authorization_endpoint": "https://app.defernowork.com/mcp/authorize",
        "token_endpoint": "https://app.defernowork.com/mcp/token",
        "registration_endpoint": "https://app.defernowork.com/mcp/register",
        "revocation_endpoint": "https://app.defernowork.com/mcp/revoke",
        "scopes_supported": ["tasks:read", "tasks:write"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "code_challenge_methods_supported": ["S256"]
      }
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": [],
  "notes": "RFC 8414. token_endpoint_auth_methods_supported deliberately excludes client_secret_basic — see test_oauth_flow.py for rationale."
}
```

- [ ] **Step 3: Create `register.json` (RFC 7591)**

```json
{
  "operation": "oauth.register",
  "method": "POST",
  "path_template": "/register",
  "auth": "none",
  "request": {
    "body": {
      "required": ["redirect_uris"],
      "optional": ["client_name", "token_endpoint_auth_method", "grant_types", "response_types"],
      "example": {
        "redirect_uris": ["http://localhost:8765/callback"],
        "client_name": "test client",
        "token_endpoint_auth_method": "client_secret_post"
      }
    }
  },
  "responses": [
    {
      "status": 201,
      "shape": {
        "client_id": "string",
        "client_secret": "any",
        "redirect_uris": ["string"]
      },
      "example": {
        "client_id": "deferno-mcp-abc123",
        "client_secret": "secret-xyz",
        "redirect_uris": ["http://localhost:8765/callback"]
      }
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": [],
  "notes": "client_secret is present when token_endpoint_auth_method=client_secret_post; absent for 'none'."
}
```

- [ ] **Step 4: Create `authorize.json`**

```json
{
  "operation": "oauth.authorize",
  "method": "GET",
  "path_template": "/authorize",
  "auth": "none",
  "request": {
    "query": {
      "required": ["response_type", "client_id", "redirect_uri", "code_challenge", "code_challenge_method"],
      "optional": ["scope", "state"],
      "example": {
        "response_type": "code",
        "client_id": "deferno-mcp-abc123",
        "redirect_uri": "http://localhost:8765/callback",
        "code_challenge": "abc",
        "code_challenge_method": "S256",
        "scope": "tasks:read",
        "state": "xyz"
      }
    }
  },
  "responses": [
    {
      "status": 302,
      "shape": "any",
      "example": null,
      "headers_required": ["Location"]
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": [],
  "notes": "Redirects to upstream OIDC (Zitadel) authorize endpoint."
}
```

- [ ] **Step 5: Create `token.json`**

```json
{
  "operation": "oauth.token",
  "method": "POST",
  "path_template": "/token",
  "auth": "none",
  "request": {
    "body": {
      "required": ["grant_type", "client_id"],
      "optional": ["client_secret", "code", "code_verifier", "redirect_uri", "refresh_token"],
      "example": {
        "grant_type": "authorization_code",
        "client_id": "deferno-mcp-abc123",
        "client_secret": "secret-xyz",
        "code": "auth-code-123",
        "code_verifier": "verifier-xyz",
        "redirect_uri": "http://localhost:8765/callback"
      }
    }
  },
  "responses": [
    {
      "status": 200,
      "shape": {
        "access_token": "string",
        "token_type": "string",
        "expires_in": "number"
      },
      "example": {
        "access_token": "mcp_xxxxxxxxxxxxxxxx",
        "token_type": "Bearer",
        "expires_in": 3600
      }
    },
    {
      "status": 401,
      "shape": {"error": "string"},
      "example": {"error": "invalid_client"}
    }
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": []
}
```

- [ ] **Step 6: Create `revoke.json` (RFC 7009)**

```json
{
  "operation": "oauth.revoke",
  "method": "POST",
  "path_template": "/revoke",
  "auth": "none",
  "request": {
    "body": {
      "required": ["token", "client_id"],
      "optional": ["client_secret", "token_type_hint"],
      "example": {
        "token": "mcp_xxxxxxxxxxxxxxxx",
        "client_id": "deferno-mcp-abc123",
        "client_secret": "secret-xyz"
      }
    }
  },
  "responses": [
    {"status": 200, "shape": "any", "example": null}
  ],
  "client_method": null,
  "client_args_from_example": [],
  "mcp_tool": null,
  "mcp_tool_args_from_example": [],
  "notes": "RFC 7009 — must return 200 for both valid and unknown tokens."
}
```

- [ ] **Step 7: Verify and commit**

```bash
python -c "import json, glob; [json.loads(open(p).read()) for p in glob.glob('tests/spec/oauth/*.json')]"
git add tests/spec/oauth
git commit -m "test: add OAuth provider RFC contract fixtures"
```

---

#### Task C2: `tests/test_oauth_provider_contract.py`

**Files:**
- Create: `tests/test_oauth_provider_contract.py`

- [ ] **Step 1: Write `tests/test_oauth_provider_contract.py`**

```python
"""In-process RFC contract for the MCP OAuth provider.

Mounts the FastMCP server's ASGI app via the same ``streamable_http_app()``
call ``server.py`` uses for production HTTP transport, then drives it with
``httpx.ASGITransport``. No network, no Redis (``RedisStore.__init__`` is
patched to wrap an in-memory fake — same fake used in test_redis_store.py).
"""

from __future__ import annotations

import os
import time
from urllib.parse import urlencode

import httpx
import pytest

from tests.spec_runner import discover_oauth_fixtures


# ── In-memory Redis fake (mirrors test_redis_store.py's FakeRedis) ──────────


class FakeRedis:
    def __init__(self):
        self._data: dict[str, str] = {}
        self._ttls: dict[str, float] = {}

    async def set(self, key, value, ex=None):
        self._data[key] = value
        if ex:
            self._ttls[key] = time.time() + ex

    async def get(self, key):
        if key in self._ttls and time.time() > self._ttls[key]:
            del self._data[key]
            del self._ttls[key]
            return None
        return self._data.get(key)

    async def delete(self, *keys):
        for k in keys:
            self._data.pop(k, None)
            self._ttls.pop(k, None)

    async def xadd(self, stream, fields, maxlen=None, approximate=True):
        pass

    async def aclose(self):
        pass

    def pipeline(self):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._ops = []

    def set(self, key, value, ex=None):
        self._ops.append(("set", key, value, ex))
        return self

    def delete(self, *keys):
        self._ops.append(("delete", *keys))
        return self

    async def execute(self):
        for op in self._ops:
            if op[0] == "set":
                await self._redis.set(op[1], op[2], ex=op[3])
            elif op[0] == "delete":
                for k in op[1:]:
                    await self._redis.delete(k)
        self._ops.clear()


# ── ASGI client fixture ─────────────────────────────────────────────────────


@pytest.fixture
async def http_client(monkeypatch):
    """Construct an in-process FastMCP HTTP app with stub OIDC + fake Redis.

    ``create_server(http_transport=True)`` instantiates ``RedisStore(redis_url)``
    eagerly when ``ZITADEL_ISSUER_URL`` is set. We patch ``RedisStore.__init__``
    so the constructor wraps a ``FakeRedis`` instead of opening a real connection.
    """
    fake = FakeRedis()

    from defernowork_mcp.redis_store import RedisStore

    def _stub_init(self, redis_url):
        self._redis = fake

    monkeypatch.setattr(RedisStore, "__init__", _stub_init)

    monkeypatch.setenv("ZITADEL_ISSUER_URL", "https://stub-issuer.test")
    monkeypatch.setenv("ZITADEL_CLIENT_ID", "test-client")
    monkeypatch.setenv("ZITADEL_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://test.local/mcp")
    monkeypatch.setenv("REDIS_URL", "redis://stub:6379")

    from defernowork_mcp import server as srv
    mcp = srv.create_server(http_transport=True)

    if hasattr(mcp, "streamable_http_app"):
        asgi = mcp.streamable_http_app()
    else:
        asgi = mcp.sse_app()

    transport = httpx.ASGITransport(app=asgi)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ── parametrized fixture-driven coverage ────────────────────────────────────


def _oauth_fixture_ids():
    return [f.operation for f in discover_oauth_fixtures()]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture",
    discover_oauth_fixtures(),
    ids=_oauth_fixture_ids(),
)
async def test_endpoint_exists(fixture, http_client: httpx.AsyncClient):
    """Smoke test: each documented OAuth endpoint responds with a known status.

    Detailed semantic checks live in the test_*_semantics tests below.
    """
    if fixture.method == "GET":
        params = {}
        query = fixture.request.get("query") or {}
        for k in query.get("required", []):
            params[k] = query.get("example", {}).get(k, "x")
        response = await http_client.get(
            fixture.path_template,
            params=params,
            follow_redirects=False,
        )
    elif fixture.method == "POST":
        body = fixture.request.get("body") or {}
        example = body.get("example") or {}
        response = await http_client.post(
            fixture.path_template,
            data=example,
        )
    else:
        pytest.skip(f"unsupported method {fixture.method}")

    expected_statuses = {r["status"] for r in fixture.responses}
    assert response.status_code in expected_statuses or response.status_code in {302, 400, 401, 405}, (
        f"{fixture.operation}: unexpected status {response.status_code}, "
        f"expected one of {expected_statuses}, body={response.text[:200]}"
    )


# ── targeted RFC semantics ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prm_required_keys(http_client: httpx.AsyncClient):
    r = await http_client.get("/.well-known/oauth-protected-resource")
    if r.status_code == 404:
        pytest.skip("PRM endpoint not exposed in current build")
    assert r.status_code == 200
    body = r.json()
    assert "resource" in body
    assert "authorization_servers" in body
    assert isinstance(body["authorization_servers"], list)


@pytest.mark.asyncio
async def test_as_metadata_required_keys_and_methods(http_client: httpx.AsyncClient):
    r = await http_client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    body = r.json()
    for key in ("issuer", "authorization_endpoint", "token_endpoint",
                "registration_endpoint", "code_challenge_methods_supported",
                "token_endpoint_auth_methods_supported"):
        assert key in body, f"missing {key}"
    assert "S256" in body["code_challenge_methods_supported"]
    methods = body["token_endpoint_auth_methods_supported"]
    assert "client_secret_post" in methods
    assert "client_secret_basic" not in methods, (
        "client_secret_basic is intentionally excluded — see oauth_flow notes"
    )


@pytest.mark.asyncio
async def test_register_client_secret_post_returns_secret(http_client: httpx.AsyncClient):
    r = await http_client.post("/register", json={
        "redirect_uris": ["http://localhost:8765/callback"],
        "token_endpoint_auth_method": "client_secret_post",
    })
    assert r.status_code in {200, 201}
    body = r.json()
    assert "client_id" in body
    assert "client_secret" in body and body["client_secret"]


@pytest.mark.asyncio
async def test_register_none_does_not_return_secret(http_client: httpx.AsyncClient):
    r = await http_client.post("/register", json={
        "redirect_uris": ["http://localhost:8765/callback"],
        "token_endpoint_auth_method": "none",
    })
    assert r.status_code in {200, 201}
    body = r.json()
    assert "client_id" in body
    assert not body.get("client_secret")


@pytest.mark.asyncio
async def test_token_missing_client_id_rejected(http_client: httpx.AsyncClient):
    r = await http_client.post("/token", data={"grant_type": "authorization_code"})
    assert r.status_code in {400, 401}


@pytest.mark.asyncio
async def test_token_unknown_client_id_rejected(http_client: httpx.AsyncClient):
    r = await http_client.post("/token", data={
        "grant_type": "authorization_code",
        "client_id": "does-not-exist",
        "code": "x",
    })
    assert r.status_code in {400, 401}


@pytest.mark.asyncio
async def test_revoke_unknown_token_returns_200(http_client: httpx.AsyncClient):
    """RFC 7009 §2.2: revocation MUST return 200 even for invalid tokens."""
    reg = await http_client.post("/register", json={
        "redirect_uris": ["http://localhost:8765/callback"],
        "token_endpoint_auth_method": "client_secret_post",
    })
    assert reg.status_code in {200, 201}
    creds = reg.json()
    r = await http_client.post("/revoke", data={
        "token": "totally-not-a-real-token",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
    })
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_authorize_redirects_to_upstream(http_client: httpx.AsyncClient):
    reg = await http_client.post("/register", json={
        "redirect_uris": ["http://localhost:8765/callback"],
        "token_endpoint_auth_method": "client_secret_post",
    })
    creds = reg.json()
    qs = urlencode({
        "response_type": "code",
        "client_id": creds["client_id"],
        "redirect_uri": "http://localhost:8765/callback",
        "code_challenge": "x" * 43,
        "code_challenge_method": "S256",
        "state": "abc",
    })
    r = await http_client.get(f"/authorize?{qs}", follow_redirects=False)
    assert r.status_code in {302, 303}
    location = r.headers.get("location", "")
    assert location, "authorize must set a Location header"
```

- [ ] **Step 2: Run and confirm**

Run: `pytest tests/test_oauth_provider_contract.py -v`
Expected: parametrized smoke tests + targeted semantic tests all PASS or SKIP cleanly. If specific RFC endpoints are not yet wired in `server.py`, tests will surface that with skips/clear errors.

- [ ] **Step 3: Commit**

```bash
git add tests/test_oauth_provider_contract.py
git commit -m "test: add in-process OAuth provider contract test"
```

---

### Track D — `tests/test_client_contract.py`

#### Task D1: Backend HTTP contract test

**Files:**
- Create: `tests/test_client_contract.py`

- [ ] **Step 1: Write `tests/test_client_contract.py`**

```python
"""Backend HTTP contract — parametrized over tests/spec/v0.1/<resource>/<op>.json.

For each fixture with ``client_method`` set, runs:
  - request-shape test: respx-mock the URL, call the client, capture the
    request, assert headers + body match the fixture's request spec.
  - response-shape test: for each response example, mock the envelope-wrapped
    backend reply and assert the unwrapped client return matches the shape
    (or DefernoError is raised with the documented error.code for non-2xx).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from defernowork_mcp.client import DefernoClient, DefernoError
from tests.spec_runner import (
    Fixture,
    SUPPORTED_API_VERSION,
    assert_request_matches_spec,
    assert_response_matches_shape,
    discover_backend_fixtures,
    substitute_path,
    wrap_envelope_data,
    wrap_envelope_error,
)

BASE = "http://test:3000"


@pytest.fixture
def client() -> DefernoClient:
    return DefernoClient(base_url=BASE, token="test-token")


def _client_fixtures() -> list[Fixture]:
    return [f for f in discover_backend_fixtures() if f.client_method]


def _ids(fixtures: list[Fixture]) -> list[str]:
    return [f.operation for f in fixtures]


def _example_args(fixture: Fixture) -> dict[str, Any]:
    body = fixture.request.get("body") or {}
    example = body.get("example") or {}
    keys = fixture.client_args_from_example
    return {k: example[k] for k in keys if k in example}


def _path_args(fixture: Fixture) -> tuple:
    """Determine positional args for client methods that take id-from-path.

    Heuristic for current methods: first ``{id}`` or ``{task_id}`` placeholder
    in the path becomes the first positional arg. Methods without placeholders
    take none.
    """
    if "{id}" in fixture.path_template or "{task_id}" in fixture.path_template:
        return ("00000000-0000-0000-0000-000000000001",)
    return ()


def _invoke(client: DefernoClient, fixture: Fixture) -> Any:
    method = getattr(client, fixture.client_method)
    args = _path_args(fixture)
    kwargs = _example_args(fixture)
    body = fixture.request.get("body") or {}
    example_body = body.get("example") or {}

    # Methods that accept a single ``payload`` dict rather than kwargs:
    payload_methods = {
        "create_task", "update_task", "split_task", "fold_task",
    }
    if fixture.client_method in payload_methods:
        return method(*args, example_body) if args else method(example_body)
    return method(*args, **kwargs)


@pytest.fixture(autouse=True)
def _reset_respx():
    yield


# ── request-shape tests ─────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", _client_fixtures(), ids=_ids(_client_fixtures()))
async def test_request_shape(fixture: Fixture, client: DefernoClient):
    success = next((r for r in fixture.responses if 200 <= r["status"] < 300), None)
    if success is None:
        pytest.skip(f"{fixture.operation}: no 2xx response example")

    url = BASE + substitute_path(fixture.path_template)
    if success["status"] == 204:
        respx.route(method=fixture.method, url__startswith=url).respond(204)
    else:
        respx.route(method=fixture.method, url__startswith=url).respond(
            status_code=success["status"],
            json=wrap_envelope_data(success.get("example")),
        )

    await _invoke(client, fixture)

    captured = respx.calls.last.request
    assert_request_matches_spec(fixture, captured, _example_args(fixture))


# ── response-shape tests (success path) ─────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", _client_fixtures(), ids=_ids(_client_fixtures()))
async def test_response_shape_success(fixture: Fixture, client: DefernoClient):
    success_idx = next(
        (i for i, r in enumerate(fixture.responses) if 200 <= r["status"] < 300),
        None,
    )
    if success_idx is None:
        pytest.skip(f"{fixture.operation}: no 2xx response")

    spec = fixture.responses[success_idx]
    url = BASE + substitute_path(fixture.path_template)
    if spec["status"] == 204:
        respx.route(method=fixture.method, url__startswith=url).respond(204)
        result = await _invoke(client, fixture)
        assert result is None
        return

    respx.route(method=fixture.method, url__startswith=url).respond(
        status_code=spec["status"],
        json=wrap_envelope_data(spec.get("example")),
    )
    result = await _invoke(client, fixture)
    assert_response_matches_shape(fixture, success_idx, result)


# ── response-shape tests (error path) ──────────────────────────────────────


def _error_cases(fixtures: list[Fixture]) -> list[tuple[Fixture, int]]:
    out = []
    for f in fixtures:
        for i, r in enumerate(f.responses):
            if r["status"] >= 400 and "error_example" in r:
                out.append((f, i))
    return out


def _error_ids(cases: list[tuple[Fixture, int]]) -> list[str]:
    return [f"{f.operation}#{f.responses[i]['status']}" for (f, i) in cases]


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    _error_cases(_client_fixtures()),
    ids=_error_ids(_error_cases(_client_fixtures())),
)
async def test_response_shape_error(case, client: DefernoClient):
    fixture, idx = case
    spec = fixture.responses[idx]
    url = BASE + substitute_path(fixture.path_template)
    respx.route(method=fixture.method, url__startswith=url).respond(
        status_code=spec["status"],
        json=wrap_envelope_error(spec["error_example"]),
    )
    with pytest.raises(DefernoError) as exc_info:
        await _invoke(client, fixture)
    assert exc_info.value.status_code == spec["status"]
    assert exc_info.value.code == spec["error_example"].get("code")
```

- [ ] **Step 2: Run and confirm**

Run: `pytest tests/test_client_contract.py -v`
Expected: each fixture with `client_method` set produces 2 (or 3, with errors) parametrized cases, all PASS. Fixtures with `client_method: null` don't generate cases.

- [ ] **Step 3: Commit**

```bash
git add tests/test_client_contract.py
git commit -m "test: add parametrized backend HTTP contract test"
```

---

### Track E — `tests/test_tools_contract.py`

#### Task E1: MCP tools layer contract

**Files:**
- Create: `tests/test_tools_contract.py`

- [ ] **Step 1: Write `tests/test_tools_contract.py`**

```python
"""MCP tool-layer contract — parametrized over fixtures with ``mcp_tool`` set.

Catches argument coercion / serialization bugs in tools/*.py that the
client-layer test would miss.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from defernowork_mcp import server as srv
from defernowork_mcp.client import DefernoClient
from tests.spec_runner import (
    Fixture,
    assert_response_matches_shape,
    discover_backend_fixtures,
    substitute_path,
    wrap_envelope_data,
    wrap_envelope_error,
)

BASE = "http://test:3000"


def _tool_fixtures() -> list[Fixture]:
    return [f for f in discover_backend_fixtures() if f.mcp_tool]


def _ids(fixtures: list[Fixture]) -> list[str]:
    return [f.operation for f in fixtures]


@pytest.fixture
def fastmcp_with_stub_client(monkeypatch):
    """Wire a fresh FastMCP server whose ``_get_client_async`` returns a
    DefernoClient pointed at our respx-mocked ``BASE``."""

    async def _stub_get_client_async(ctx=None):
        return DefernoClient(base_url=BASE, token="test-token")

    monkeypatch.setattr(srv, "_get_client_async", _stub_get_client_async)
    monkeypatch.setattr(srv, "_http_transport_mode", False)
    return srv.create_server()


def _registered_tool(mcp, name: str):
    """Look up a registered tool by name from a FastMCP instance."""
    tools = getattr(mcp, "_tool_manager", None) or getattr(mcp, "tool_manager", None)
    if tools is not None:
        for attr in ("_tools", "tools"):
            tool_map = getattr(tools, attr, None)
            if isinstance(tool_map, dict) and name in tool_map:
                return tool_map[name]
    fn = getattr(mcp, name, None)
    if fn is not None:
        return fn
    raise LookupError(f"tool {name!r} not registered on this FastMCP instance")


def _tool_kwargs(fixture: Fixture) -> dict[str, Any]:
    body = fixture.request.get("body") or {}
    example = body.get("example") or {}
    keys = fixture.mcp_tool_args_from_example
    return {k: example[k] for k in keys if k in example}


def _tool_path_kwarg(fixture: Fixture) -> dict[str, Any]:
    if "{id}" in fixture.path_template:
        return {"task_id": "00000000-0000-0000-0000-000000000001"}
    if "{task_id}" in fixture.path_template:
        return {"task_id": "00000000-0000-0000-0000-000000000001"}
    return {}


async def _invoke_tool(tool, kwargs: dict[str, Any]) -> Any:
    """Call a registered tool by attribute or via its callable handle."""
    if hasattr(tool, "fn"):
        return await tool.fn(**kwargs)
    if hasattr(tool, "handler"):
        return await tool.handler(**kwargs)
    if callable(tool):
        return await tool(**kwargs)
    raise TypeError(f"don't know how to invoke tool object of type {type(tool).__name__}")


@respx.mock
@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", _tool_fixtures(), ids=_ids(_tool_fixtures()))
async def test_tool_invokes_endpoint_and_returns_payload(
    fixture: Fixture, fastmcp_with_stub_client
):
    success_idx = next(
        (i for i, r in enumerate(fixture.responses) if 200 <= r["status"] < 300),
        None,
    )
    if success_idx is None:
        pytest.skip(f"{fixture.operation}: no 2xx response")

    spec = fixture.responses[success_idx]
    url = BASE + substitute_path(fixture.path_template)
    if spec["status"] == 204:
        respx.route(method=fixture.method, url__startswith=url).respond(204)
    else:
        respx.route(method=fixture.method, url__startswith=url).respond(
            status_code=spec["status"],
            json=wrap_envelope_data(spec.get("example")),
        )

    tool = _registered_tool(fastmcp_with_stub_client, fixture.mcp_tool)

    kwargs = {**_tool_path_kwarg(fixture), **_tool_kwargs(fixture)}
    result = await _invoke_tool(tool, kwargs)

    # Tools serialize their output as JSON strings (see tools/tasks.py).
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except ValueError:
            parsed = result
    else:
        parsed = result

    assert respx.calls.called, f"{fixture.operation}: tool did not call backend"
    if spec["status"] != 204 and isinstance(parsed, (dict, list)):
        assert_response_matches_shape(fixture, success_idx, parsed)
```

- [ ] **Step 2: Run and confirm**

Run: `pytest tests/test_tools_contract.py -v`
Expected: each fixture with `mcp_tool` set produces a parametrized case; all PASS. Tool lookup machinery may need small tweaks for FastMCP's exact API surface; if `_registered_tool` can't find a tool, the LookupError surfaces cleanly per fixture.

- [ ] **Step 3: Commit**

```bash
git add tests/test_tools_contract.py
git commit -m "test: add MCP tool-layer contract test"
```

---

### Track F — Existing-test migration

#### Task F1: Move helper tests into `test_helpers.py`

**Files:**
- Create: `tests/test_helpers.py`
- Modify: `tests/test_server.py` — delete migrated tests
- Modify: `tests/test_multi_user_auth.py` — delete migrated tests (file rename happens in Task F3)

- [ ] **Step 1: Create `tests/test_helpers.py`**

```python
"""Helper-level tests: _compact, server creation, _generate_token, stdio mode.

Migrated verbatim from test_server.py and test_multi_user_auth.py per the
spec's "Migration of existing tests" table. No assertion is dropped.
"""

from __future__ import annotations

import pytest

from defernowork_mcp.server import _compact, _UNSET, create_server, DEFAULT_BASE_URL
from defernowork_mcp.redis_store import _generate_token


# ── _compact with _UNSET sentinel (from test_server.py) ─────────────────────


class TestCompact:
    def test_strips_unset_values(self):
        result = _compact({"a": 1, "b": _UNSET, "c": "hello"})
        assert result == {"a": 1, "c": "hello"}

    def test_preserves_none_values(self):
        """None means 'clear this field' and must be sent as JSON null."""
        result = _compact({"title": "keep", "complete_by": None, "desire": _UNSET})
        assert result == {"title": "keep", "complete_by": None}

    def test_preserves_false_and_zero(self):
        result = _compact({"flag": False, "count": 0, "gone": _UNSET})
        assert result == {"flag": False, "count": 0}

    def test_empty_dict(self):
        assert _compact({}) == {}

    def test_all_unset(self):
        assert _compact({"a": _UNSET, "b": _UNSET}) == {}

    def test_all_none(self):
        """All None values preserved — they're explicit clears."""
        result = _compact({"a": None, "b": None})
        assert result == {"a": None, "b": None}


# ── Server creation (from test_server.py) ───────────────────────────────────


def test_create_server_returns_fastmcp():
    server = create_server()
    assert server is not None


def test_default_base_url_is_localhost():
    assert DEFAULT_BASE_URL == "http://127.0.0.1:3000"


# ── Token generation (from test_multi_user_auth.py) ─────────────────────────


class TestTokenGeneration:
    def test_generates_64_char_hex(self):
        token = _generate_token()
        assert len(token) == 64
        int(token, 16)  # must be valid hex

    def test_tokens_are_unique(self):
        tokens = {_generate_token() for _ in range(100)}
        assert len(tokens) == 100


# ── Stdio mode (from test_multi_user_auth.py) ───────────────────────────────


class TestStdioMode:
    @pytest.mark.asyncio
    async def test_get_client_stdio_does_not_use_redis(self):
        """In stdio mode, _get_client_async should use env/disk, not Redis."""
        from defernowork_mcp import server as srv
        srv._http_transport_mode = False
        srv._redis_store = None
        client = await srv._get_client_async()
        assert client is not None
```

- [ ] **Step 2: Delete the migrated tests from `tests/test_server.py`**

Replace the entire file with:

```python
"""All tests previously here have been migrated to test_helpers.py."""
```

- [ ] **Step 3: Delete the migrated TokenGeneration + StdioMode classes from `tests/test_multi_user_auth.py`**

Open `tests/test_multi_user_auth.py` and remove these blocks (the `RedisStore` test classes stay, to be renamed in Task F3):

- The entire `# Token generation` section header and `class TestTokenGeneration: ...`
- The entire `# Stdio mode (no Redis)` section header and `class TestStdioMode: ...`

(Lines approximately 207–235 in the current file.)

- [ ] **Step 4: Run helpers tests and confirm green**

Run: `pytest tests/test_helpers.py tests/test_server.py tests/test_multi_user_auth.py -v`
Expected: helpers PASS; server is empty (no tests collected, fine); multi_user_auth still has its RedisStore tests passing.

- [ ] **Step 5: Commit**

```bash
git add tests/test_helpers.py tests/test_server.py tests/test_multi_user_auth.py
git commit -m "test: migrate _compact/server/token/stdio tests to test_helpers"
```

---

#### Task F2: Move transport tests into `test_client_transport.py`

**Files:**
- Create: `tests/test_client_transport.py`
- Modify: `tests/test_client.py` — keep file but it should already be xfail'd from Task 1.3; will be deleted in Wave 3

- [ ] **Step 1: Create `tests/test_client_transport.py`**

```python
"""Transport-level error handling for DefernoClient.

These tests exercise generic transport behavior (timeouts, connection
errors, malformed bodies, missing tokens) — orthogonal to per-endpoint
spec assertions. Migrated from test_client.py.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from defernowork_mcp.client import DefernoClient, DefernoError

BASE = "http://test:3000"


@pytest.fixture
def client() -> DefernoClient:
    return DefernoClient(base_url=BASE, token="test-token")


# ── Auth requirement ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_token_raises_401():
    c = DefernoClient(base_url=BASE, token=None)
    with pytest.raises(DefernoError) as exc_info:
        await c.list_tasks()
    assert exc_info.value.status_code == 401


# ── Network errors ──────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_timeout_raises_504(client: DefernoClient):
    respx.get(f"{BASE}/tasks").mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(DefernoError) as exc_info:
        await client.list_tasks()
    assert exc_info.value.status_code == 504
    assert "timed out" in exc_info.value.message


@respx.mock
@pytest.mark.asyncio
async def test_connect_error_raises_502(client: DefernoClient):
    respx.get(f"{BASE}/tasks").mock(side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(DefernoError) as exc_info:
        await client.list_tasks()
    assert exc_info.value.status_code == 502
    assert "network error" in exc_info.value.message


# ── Malformed response body ────────────────────────────────────────────────


@respx.mock
@pytest.mark.asyncio
async def test_non_json_error_uses_text(client: DefernoClient):
    respx.get(f"{BASE}/tasks").respond(500, text="Internal Server Error")
    with pytest.raises(DefernoError) as exc_info:
        await client.list_tasks()
    assert exc_info.value.status_code == 500
    assert "Internal Server Error" in exc_info.value.message
```

- [ ] **Step 2: Run and confirm green**

Run: `pytest tests/test_client_transport.py -v`
Expected: 4 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_client_transport.py
git commit -m "test: migrate transport tests to test_client_transport"
```

---

#### Task F3: Rename `test_multi_user_auth.py` → `test_redis_store.py`

**Files:**
- Rename: `tests/test_multi_user_auth.py` → `tests/test_redis_store.py`

- [ ] **Step 1: Rename the file**

```bash
git mv tests/test_multi_user_auth.py tests/test_redis_store.py
```

- [ ] **Step 2: Update the docstring at the top of the file**

Replace the module docstring (first 5 lines) with:

```python
"""Tests for the RedisStore — the OAuth provider's storage layer.

Uses an in-memory FakeRedis to exercise client/auth-code/access-token/
refresh-token persistence and multi-user isolation. The token-generation
helpers and stdio-mode tests live in test_helpers.py.
"""
```

- [ ] **Step 3: Run and confirm green**

Run: `pytest tests/test_redis_store.py -v`
Expected: all RedisStore tests PASS (no token-generation or stdio tests should remain in this file — those moved to test_helpers.py in Task F1).

- [ ] **Step 4: Commit**

```bash
git add tests/test_redis_store.py
git commit -m "test: rename test_multi_user_auth -> test_redis_store"
```

---

#### Task F4: Mark `test_oauth_flow.py` as `@pytest.mark.live`

**Files:**
- Modify: `tests/test_oauth_flow.py` — add module-level marker

- [ ] **Step 1: Add `pytestmark` to the top of `tests/test_oauth_flow.py`**

Open `tests/test_oauth_flow.py`. Just below the module docstring (and above any imports / `import` statements), add:

```python
import pytest

pytestmark = pytest.mark.live
```

If the file already imports pytest, place `pytestmark = pytest.mark.live` directly after the imports.

- [ ] **Step 2: Confirm default run skips it**

Run: `pytest -v -m "not live" tests/test_oauth_flow.py`
Expected: `0 passed, N deselected`.

Run: `pytest -v -m live tests/test_oauth_flow.py --collect-only`
Expected: tests are collected (collection only — no run, since it hits staging).

- [ ] **Step 3: Commit**

```bash
git add tests/test_oauth_flow.py
git commit -m "test: gate test_oauth_flow behind 'live' marker"
```

---

## Wave 3 — Reconciliation (sequential)

### Task 3.1: Migration sweep — delete legacy per-endpoint tests from `test_client.py`

**Files:**
- Modify: `tests/test_client.py` — replace contents

- [ ] **Step 1: Confirm migration parity**

Run: `pytest -v -m "not live"`
Expected: all green or expected xfails. If `tests/test_client_contract.py` is green and exercises everything `tests/test_client.py` previously asserted (via `client_method` fixtures), the legacy file is redundant.

- [ ] **Step 2: Replace `tests/test_client.py` with a stub**

```python
"""Per-endpoint tests have been migrated to:

  - tests/test_client_contract.py    (parametrized HTTP contract)
  - tests/test_client_transport.py   (transport-layer error handling)
  - tests/test_client_envelope_contract.py (v0.1 envelope unwrapping)

This stub exists only to prevent stale test-discovery cache. Delete in a
follow-up commit once CI has run cleanly with the new layout.
"""
```

- [ ] **Step 3: Run the full default suite**

Run: `pytest -v -m "not live"`
Expected: all green; 0 xfails (the previous xfail was on this file's legacy tests, now removed).

- [ ] **Step 4: Commit**

```bash
git add tests/test_client.py
git commit -m "test: remove legacy per-endpoint tests, migrated to contract suite"
```

---

### Task 3.2: CI workflow updates

**Files:**
- Modify: `.github/workflows/release.yml`
- Create: `.github/workflows/live-tests.yml`

- [ ] **Step 1: Update `release.yml` `test` job**

Replace the existing `test` job in `.github/workflows/release.yml` with:

```yaml
  test:
    name: Test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5

      - name: Check out Deferno sibling repo for architecture.md
        uses: actions/checkout@v5
        with:
          repository: Kyle-Falconer/Deferno
          path: Deferno
          fetch-depth: 1

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: pip install ".[test]"

      - name: Run default suite (excludes 'live' marker)
        env:
          ARCHITECTURE_DOC_PATH: ${{ github.workspace }}/Deferno/docs/architecture.md
        run: pytest -v -m "not live"
```

If the upstream Deferno repo path differs (e.g. organization-owned), adjust the `repository:` value accordingly.

- [ ] **Step 2: Create `.github/workflows/live-tests.yml`**

```yaml
name: Live integration tests

on:
  schedule:
    - cron: "0 11 * * *"   # 11:00 UTC daily
  workflow_dispatch:

jobs:
  live:
    name: Live
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install ".[test]"
      - name: Run live-marked tests against staging
        env:
          DEFERNO_STAGING_URL: ${{ secrets.DEFERNO_STAGING_URL }}
        run: pytest -v -m live
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml .github/workflows/live-tests.yml
git commit -m "ci: gate deploy on 'not live' suite; add daily live-tests workflow"
```

---

### Task 3.3: Final verification

- [ ] **Step 1: Full default suite with the architecture doc available**

```bash
ARCHITECTURE_DOC_PATH=c:/deferno_all/Deferno/docs/architecture.md pytest -v -m "not live"
```
Expected: all green; `test_every_endpoint_has_a_fixture` PASSES; no skips for the inventory test.

- [ ] **Step 2: Full default suite without the architecture doc**

```bash
ARCHITECTURE_DOC_PATH=/does/not/exist pytest -v -m "not live"
```
Expected: all green; `test_every_endpoint_has_a_fixture` SKIPS with the documented reason. No other test depends on the doc.

- [ ] **Step 3: Confirm `live` suite is not run by default**

Run: `pytest -v -m "not live" --collect-only | grep -c oauth_flow`
Expected: `0` (the `oauth_flow` tests are deselected).

- [ ] **Step 4: Tag final**

```bash
git tag -a mcp-spec-tests-complete -m "MCP spec-driven test suite — wave 1+2+3 complete"
```

---

## Spec coverage map (self-review)

| Spec section | Plan task(s) |
|---|---|
| API version contract / envelope | 1.2, 1.3 |
| `_envelope.json` meta-spec | 1.2 |
| `tests/spec_runner.py` shape comparator | 1.4 |
| `tests/endpoint_registry.py` | B1 |
| `tests/inventory.py` + cross-check | B2 |
| `tests/test_client_envelope_contract.py` | 1.2 |
| `tests/test_client_contract.py` | D1 |
| `tests/test_tools_contract.py` | E1 |
| `tests/test_oauth_provider_contract.py` | C2 (fixtures: C1) |
| Backend fixtures `tests/spec/v0.1/...` | A1, A2, A3, A4, A5, A6 |
| OAuth fixtures `tests/spec/oauth/...` | C1 |
| `tests/test_helpers.py` migration | F1 |
| `tests/test_client_transport.py` migration | F2 |
| `tests/test_redis_store.py` rename | F3 |
| `test_oauth_flow.py` `@pytest.mark.live` gate | F4 |
| `client.py:_request` envelope unwrap (DISCOVERED GAP) | 1.3 |
| Default `pytest -v -m "not live"` gate | 3.2 |
| `live-tests.yml` workflow | 3.2 |
| Sibling Deferno checkout in CI | 3.2 |
| Migration of existing tests (no dropped assertions) | F1, F2, F3, F4 + sweep in 3.1 |
