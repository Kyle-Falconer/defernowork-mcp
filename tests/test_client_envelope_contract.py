"""v0.1 envelope contract: client._request unwraps + validates version."""

from __future__ import annotations

import json
from pathlib import Path

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
