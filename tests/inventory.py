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
