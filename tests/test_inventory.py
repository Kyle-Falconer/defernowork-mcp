"""Inventory tests — three-source consensus on backend endpoints."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from _pytest.outcomes import Failed, Skipped

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


def test_parse_normalizes_axum_placeholders_to_braces(tmp_path: Path):
    """Architecture.md uses both `:id` (Axum/Rust) and `{id}` (OpenAPI-ish)
    notations. Normalize both to `{id}` so the cross-checker sees one form."""
    md = tmp_path / "architecture.md"
    md.write_text(
        dedent("""
            ### Items

            | Method | Path | Auth | Description |
            |---|---|---|---|
            | `GET` | `/items/:id` | Yes | Single item |
            | `GET` | `/tasks/:task_id/comments` | Yes | Task comments |
            | `DELETE` | `/auth/tokens/{id}` | Yes | Revoke a token |
        """).strip(),
        encoding="utf-8",
    )
    rows = parse_architecture_md(md)
    paths = sorted((r.method, r.path) for r in rows)
    assert paths == [
        ("DELETE", "/auth/tokens/{id}"),
        ("GET", "/items/{id}"),
        ("GET", "/tasks/{task_id}/comments"),
    ]


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
    """Three-source consensus check: doc ↔ registry ↔ fixtures.

    Skips when the architecture document is absent and nothing asked for it.
    Fails when ``ARCHITECTURE_DOC_PATH`` asked for it and it is not there.
    """
    from tests.inventory import architecture_doc, missing_doc_message, run_inventory

    doc = architecture_doc()
    if not doc.exists():
        if doc.required:
            pytest.fail(missing_doc_message(doc))
        pytest.skip(missing_doc_message(doc))
    run_inventory(doc.path)


# ── document resolution ─────────────────────────────────────────────


def test_env_var_makes_the_document_required(monkeypatch, tmp_path: Path):
    """CI sets the variable, so a missing document there is a failure."""
    from tests.inventory import ARCHITECTURE_DOC_ENV, architecture_doc

    monkeypatch.setenv(ARCHITECTURE_DOC_ENV, str(tmp_path / "architecture.md"))
    doc = architecture_doc()
    assert doc.required
    assert doc.path == tmp_path / "architecture.md"


def test_sibling_fallback_is_optional(monkeypatch):
    """A contributor without the Deferno repo checked out still runs the suite."""
    from tests.inventory import (
        ARCHITECTURE_DOC_ENV,
        SIBLING_ARCHITECTURE_DOC,
        architecture_doc,
    )

    monkeypatch.delenv(ARCHITECTURE_DOC_ENV, raising=False)
    doc = architecture_doc()
    assert not doc.required
    assert doc.path == SIBLING_ARCHITECTURE_DOC


def test_required_message_names_the_document_and_the_secret(tmp_path: Path):
    from tests.inventory import ArchitectureDoc, missing_doc_message

    message = missing_doc_message(
        ArchitectureDoc(path=tmp_path / "architecture.md", required=True)
    )
    assert str(tmp_path / "architecture.md") in message
    assert "DEFERNO_REPO_TOKEN" in message
    assert "ARCHITECTURE_DOC_PATH" in message


def test_skip_message_names_the_document_and_the_sibling_checkout(tmp_path: Path):
    from tests.inventory import ArchitectureDoc, missing_doc_message

    message = missing_doc_message(
        ArchitectureDoc(path=tmp_path / "architecture.md", required=False)
    )
    assert str(tmp_path / "architecture.md") in message
    assert "Deferno" in message
    assert "ARCHITECTURE_DOC_PATH" in message


def test_gate_fails_when_a_required_document_is_missing(monkeypatch, tmp_path: Path):
    """The whole point of #32: a missing document breaks the build, not the gate."""
    from tests.inventory import ARCHITECTURE_DOC_ENV

    monkeypatch.setenv(ARCHITECTURE_DOC_ENV, str(tmp_path / "nope.md"))
    with pytest.raises(Failed, match="DEFERNO_REPO_TOKEN"):
        test_every_endpoint_has_a_fixture()


def test_gate_skips_when_no_document_was_asked_for(monkeypatch, tmp_path: Path):
    from tests import inventory
    from tests.inventory import ARCHITECTURE_DOC_ENV

    monkeypatch.delenv(ARCHITECTURE_DOC_ENV, raising=False)
    monkeypatch.setattr(inventory, "SIBLING_ARCHITECTURE_DOC", tmp_path / "nope.md")
    with pytest.raises(Skipped, match="Check out the Deferno repo"):
        test_every_endpoint_has_a_fixture()
