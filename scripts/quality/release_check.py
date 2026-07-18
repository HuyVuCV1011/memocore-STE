from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    failures: list[str] = []
    pyproject_version = pyproject_version_at(root / "pyproject.toml")
    init_version = init_version_at(root / "src" / "memocore" / "__init__.py")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")

    if pyproject_version != init_version:
        failures.append(
            f"pyproject version {pyproject_version} does not match __version__ {init_version}"
        )
    if "## Unreleased" not in changelog:
        failures.append("CHANGELOG.md must contain an Unreleased section")
    if args.tag:
        expected = args.tag.removeprefix("refs/tags/").removeprefix("v")
        if expected != pyproject_version:
            failures.append(f"tag {args.tag} does not match package version {pyproject_version}")
        if args.require_changelog_version and not changelog_has_version(changelog, expected):
            failures.append(f"CHANGELOG.md must contain a release section for {expected}")
    if args.require_clean and not git_is_clean(root):
        failures.append("working tree must be clean for release")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"Release metadata OK: version={pyproject_version}")
    return 0


def pyproject_version_at(path: Path) -> str:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return str(data["project"]["version"])


def init_version_at(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    if not match:
        raise ValueError(f"Could not find __version__ in {path}")
    return match.group(1)


def changelog_has_version(changelog: str, version: str) -> bool:
    escaped = re.escape(version)
    return re.search(rf"^##\s+(?:\[{escaped}\]|{escaped})(?:\s+-\s+\d{{4}}-\d{{2}}-\d{{2}})?\s*$", changelog, re.MULTILINE) is not None


def git_is_clean(root: Path) -> bool:
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return completed.returncode == 0 and not completed.stdout.strip()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate MemoCore release metadata.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--tag", help="Release tag to validate, such as v0.4.1.")
    parser.add_argument(
        "--require-changelog-version",
        action="store_true",
        help="Require CHANGELOG.md to contain a section for the tag/package version.",
    )
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="Require a clean Git working tree.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
