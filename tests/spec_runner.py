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
