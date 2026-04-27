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
