from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memocore.services.backup_service import BackupService
from memocore.services.review_window_service import review_window_report

from scripts.quality import module_size_check, release_check


@dataclass(frozen=True)
class GateResult:
    level: str
    name: str
    detail: str


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    results = evaluate_v4_readiness(
        root=root,
        database_path=Path(args.database),
        backup_dir=Path(args.backup_dir),
        required_days=args.days,
        tag=args.tag,
        require_clean=args.require_clean,
    )
    print("MemoCore V4 readiness gate")
    print("")
    for result in results:
        print(f"{result.level:<4} {result.name}: {result.detail}")
    print("")
    has_failures = any(result.level == "FAIL" for result in results)
    has_warnings = any(result.level == "WARN" for result in results)
    if has_failures:
        print("Result: not ready")
        return 1
    if has_warnings:
        print("Result: collecting")
        return 1 if args.strict else 0
    print("Result: ready")
    return 0


def evaluate_v4_readiness(
    *,
    root: Path,
    database_path: Path,
    backup_dir: Path,
    required_days: int = 14,
    tag: str | None = None,
    require_clean: bool = False,
) -> list[GateResult]:
    return [
        _release_metadata_gate(root, tag=tag),
        _working_tree_gate(root, require_clean=require_clean),
        _module_size_gate(root),
        _review_window_gate(database_path, required_days=required_days),
        _backup_gate(database_path, backup_dir),
    ]


def _release_metadata_gate(root: Path, *, tag: str | None) -> GateResult:
    try:
        pyproject_version = release_check.pyproject_version_at(root / "pyproject.toml")
        init_version = release_check.init_version_at(root / "src/memocore/__init__.py")
        changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    except (OSError, KeyError, ValueError) as exc:
        return GateResult("FAIL", "Release metadata", str(exc))
    if pyproject_version != init_version:
        return GateResult(
            "FAIL",
            "Release metadata",
            f"pyproject={pyproject_version}, __version__={init_version}",
        )
    if "## Unreleased" not in changelog:
        return GateResult("FAIL", "Release metadata", "CHANGELOG.md missing Unreleased section")
    if tag:
        expected = tag.removeprefix("refs/tags/").removeprefix("v")
        if expected != pyproject_version:
            return GateResult(
                "FAIL",
                "Release metadata",
                f"tag {tag} does not match package version {pyproject_version}",
            )
        if not release_check.changelog_has_version(changelog, expected):
            return GateResult(
                "FAIL",
                "Release metadata",
                f"CHANGELOG.md missing release section for {expected}",
            )
    return GateResult("OK", "Release metadata", f"version={pyproject_version}")


def _working_tree_gate(root: Path, *, require_clean: bool) -> GateResult:
    clean = release_check.git_is_clean(root)
    if clean:
        return GateResult("OK", "Working tree", "clean")
    level = "FAIL" if require_clean else "WARN"
    return GateResult(level, "Working tree", "dirty; commit or stash before release")


def _module_size_gate(root: Path) -> GateResult:
    failures = module_size_check.check_module_sizes(root)
    if failures:
        return GateResult("FAIL", "Module size", "; ".join(failures))
    return GateResult("OK", "Module size", "large-module budgets pass")


def _review_window_gate(database_path: Path, *, required_days: int) -> GateResult:
    report = review_window_report(database_path, required_days=required_days)
    if report.gate_passed:
        return GateResult("OK", "Review window", report.summary())
    level = "FAIL" if report.status == "failed" else "WARN"
    return GateResult(level, "Review window", report.summary())


def _backup_gate(database_path: Path, backup_dir: Path) -> GateResult:
    if not database_path.exists():
        return GateResult("FAIL", "Backup/restore", f"database not found: {database_path}")
    service = BackupService(database_path, backup_dir)
    backups = service.list_backups()
    if not backups:
        return GateResult("WARN", "Backup/restore", "no backup manifest found")
    backup_id = backups[0].get("backup_id")
    if not backup_id:
        return GateResult("WARN", "Backup/restore", "latest backup manifest has no backup_id")
    verification = service.verify_backup(str(backup_id))
    if not verification.ok:
        return GateResult(
            "FAIL",
            "Backup/restore",
            f"{backup_id}: {verification.error or verification.integrity}",
        )
    drill = service.latest_restore_drill()
    if not drill or not drill.get("verified"):
        return GateResult(
            "WARN",
            "Backup/restore",
            f"{backup_id} verified; restore drill missing",
        )
    return GateResult(
        "OK",
        "Backup/restore",
        f"{backup_id} verified; restore_drill={drill.get('backup_id')}",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report whether the local MemoCore V4 release gates are ready."
    )
    parser.add_argument("--root", default=REPO_ROOT, help="Repository root.")
    parser.add_argument("--database", default="data/memocore.db", help="SQLite database path.")
    parser.add_argument("--backup-dir", default="backups", help="Backup directory.")
    parser.add_argument("--days", type=int, default=14, help="Required review-window days.")
    parser.add_argument("--tag", help="Optional release tag to validate.")
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="Treat a dirty working tree as a failure instead of a warning.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on warnings as well as failures.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
