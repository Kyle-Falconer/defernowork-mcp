"""TDD tests for the wrapper-string parser in helpers.claude_code."""
from __future__ import annotations

import pytest

from helpers.claude_code import ModelFormatDeviation, parse_wrapper


def test_parse_wrapper_extracts_json_between_markers():
    raw = """\
some preamble
<<<DEFERNO_E2E_BEGIN>>>
{"identity": "alice@example.com"}
<<<DEFERNO_E2E_END>>>
trailing
"""
    assert parse_wrapper(raw) == {"identity": "alice@example.com"}


def test_parse_wrapper_handles_multiline_json():
    raw = """\
<<<DEFERNO_E2E_BEGIN>>>
{
  "identity": "bob",
  "scopes": ["read", "write"]
}
<<<DEFERNO_E2E_END>>>
"""
    assert parse_wrapper(raw) == {"identity": "bob", "scopes": ["read", "write"]}


def test_parse_wrapper_rejects_no_match():
    with pytest.raises(ModelFormatDeviation):
        parse_wrapper("Hello, I cannot help with that.")


def test_parse_wrapper_rejects_multiple_pairs():
    raw = """\
<<<DEFERNO_E2E_BEGIN>>>{"a":1}<<<DEFERNO_E2E_END>>>
<<<DEFERNO_E2E_BEGIN>>>{"b":2}<<<DEFERNO_E2E_END>>>
"""
    with pytest.raises(ModelFormatDeviation):
        parse_wrapper(raw)


def test_parse_wrapper_rejects_unparseable_json():
    raw = "<<<DEFERNO_E2E_BEGIN>>>{not json}<<<DEFERNO_E2E_END>>>"
    with pytest.raises(ModelFormatDeviation):
        parse_wrapper(raw)
