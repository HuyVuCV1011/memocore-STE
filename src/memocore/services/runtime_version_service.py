from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from memocore import __version__


@dataclass(frozen=True)
class RuntimeVersionDescriptor:
    package_version: str
    git_commit: str
    git_dirty: bool | None
    schema_version: str

    def format(self) -> str:
        dirty = "unknown" if self.git_dirty is None else ("yes" if self.git_dirty else "no")
        return (
            f"package={self.package_version}, commit={self.git_commit}, "
            f"dirty={dirty}, schema={self.schema_version}"
        )


def runtime_version_descriptor(
    database_path: Path,
    *,
    repo_path: Path | None = None,
) -> RuntimeVersionDescriptor:
    repo_path = repo_path or Path.cwd()
    return RuntimeVersionDescriptor(
        package_version=_package_version(),
        git_commit=_git_commit(repo_path),
        git_dirty=_git_dirty(repo_path),
        schema_version=_schema_version(database_path),
    )


def _package_version() -> str:
    try:
        return metadata.version("memocore")
    except metadata.PackageNotFoundError:
        return __version__


def _git_commit(repo_path: Path) -> str:
    return _git(["rev-parse", "--short=12", "HEAD"], repo_path) or "unknown"


def _git_dirty(repo_path: Path) -> bool | None:
    output = _git(["status", "--porcelain"], repo_path)
    if output is None:
        return None
    return bool(output.strip())


def _git(args: list[str], repo_path: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _schema_version(database_path: Path) -> str:
    if str(database_path) == ":memory:":
        return "memory"
    if not database_path.exists():
        return "missing"
    try:
        conn = sqlite3.connect(database_path)
        row = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return "unavailable"
    if not row or not row[0]:
        return "base"
    return str(row[0])
