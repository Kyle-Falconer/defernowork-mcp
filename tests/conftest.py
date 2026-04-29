"""Shared pytest plumbing for the defernowork-mcp test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = REPO_ROOT / "tests" / "spec"

# Belt-and-suspenders: norecursedirs in pyproject already excludes the bottle
# from default discovery; this also excludes it from explicit-path collection.
collect_ignore = ["e2e-bottle"]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def spec_dir() -> Path:
    return SPEC_DIR
