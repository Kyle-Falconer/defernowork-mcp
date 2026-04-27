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
