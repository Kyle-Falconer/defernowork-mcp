"""Three-source consensus check on backend endpoints.

Cross-checks:
  1. ``Deferno/docs/architecture.md`` (the documented contract)
  2. ``tests/endpoint_registry.py``  (hand-curated per Rust handler)
  3. ``tests/spec/v{ver}/<resource>/`` for each ``ver`` in
     ``SUPPORTED_API_VERSIONS`` (the on-disk fixtures)

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
from tests.spec_runner import SUPPORTED_API_VERSIONS

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
_AXUM_PLACEHOLDER = re.compile(r":(\w+)")


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
                path_str = _AXUM_PLACEHOLDER.sub(r"{\1}", path_str)
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


def fixtures_on_disk(
    versions: frozenset[str] | set[str] | None = None,
) -> set[tuple[str, str, str]]:
    """Return ``{(method, path_template, operation), ...}`` from disk.

    Walks every supported version directory (``tests/spec/v0.1/``,
    ``tests/spec/v0.2/``, ...) and unions the fixtures, so the cross-check
    stays green during the v0.1->v0.2 cutover window when fixtures live
    under either version.
    """
    if versions is None:
        versions = SUPPORTED_API_VERSIONS
    out: set[tuple[str, str, str]] = set()
    for version in sorted(versions):
        base = SPEC_DIR / f"v{version}"
        if not base.exists():
            continue
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


ARCHITECTURE_DOC_ENV = "ARCHITECTURE_DOC_PATH"

SIBLING_ARCHITECTURE_DOC = (
    Path(__file__).resolve().parent.parent.parent / "Deferno" / "docs" / "architecture.md"
)
"""Where the document sits when the Deferno repo is checked out beside this one."""


@dataclass(frozen=True)
class ArchitectureDoc:
    """Where the architecture document is, and whether its absence is an error."""

    path: Path
    required: bool

    def exists(self) -> bool:
        return self.path.exists()


def architecture_doc() -> ArchitectureDoc:
    """Locate the architecture document and say whether it has to be there.

    ``ARCHITECTURE_DOC_PATH`` names the document. Setting it declares that the
    document is expected, so a missing file is a failure rather than a skip. CI
    sets it.

    With the variable unset, the lookup falls back to a sibling Deferno
    checkout. A contributor may not have that repo, so a missing file there is
    not an error.
    """
    env = os.environ.get(ARCHITECTURE_DOC_ENV)
    if env:
        return ArchitectureDoc(path=Path(env), required=True)
    return ArchitectureDoc(path=SIBLING_ARCHITECTURE_DOC, required=False)


def missing_doc_message(doc: ArchitectureDoc) -> str:
    """Explain which document is missing and how to supply it."""
    if doc.required:
        return (
            f"architecture.md is not at {doc.path}, and {ARCHITECTURE_DOC_ENV} says "
            f"it should be. In CI the file comes from the Deferno sibling checkout, "
            f"which needs the DEFERNO_REPO_TOKEN repository secret. Locally, clear "
            f"{ARCHITECTURE_DOC_ENV} to skip this gate instead."
        )
    return (
        f"architecture.md is not at {doc.path}. Check out the Deferno repo beside "
        f"this one, or point {ARCHITECTURE_DOC_ENV} at the document."
    )
