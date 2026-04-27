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
