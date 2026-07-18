from __future__ import annotations

import importlib.util
from pathlib import Path


def _release_check_module():
    path = Path("scripts/quality/release_check.py")
    spec = importlib.util.spec_from_file_location("release_check", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_changelog_has_version_accepts_bracketed_and_plain_headers():
    release_check = _release_check_module()

    assert release_check.changelog_has_version("## [0.4.1] - 2026-07-15\n", "0.4.1")
    assert release_check.changelog_has_version("## 0.4.1\n", "0.4.1")
    assert not release_check.changelog_has_version("## Unreleased\n", "0.4.1")


def test_release_metadata_matches_project_version():
    release_check = _release_check_module()
    root = Path(".")

    assert release_check.pyproject_version_at(root / "pyproject.toml") == "0.4.1"
    assert release_check.init_version_at(root / "src/memocore/__init__.py") == "0.4.1"
