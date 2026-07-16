from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memocore.services.backup_service import BackupService
from memocore.services.review_window_service import review_window_report
from memocore.services.event_service import (
    feedback_requires_regression,
    valid_feedback_payload,
)

from scripts.quality import module_size_check, release_check


@dataclass(frozen=True)
class GateResult:
    level: str
    name: str
    detail: str


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    telegram_owner_id = (
        _positive_int(str(args.telegram_owner_id))
        if args.telegram_owner_id is not None
        else _positive_int(os.getenv("TELEGRAM_OWNER_ID"))
    )
    timezone_name = args.timezone or os.getenv("USER_TIMEZONE")
    try:
        display_timezone = ZoneInfo(timezone_name) if timezone_name else UTC
    except ZoneInfoNotFoundError:
        print(f"Invalid timezone: {timezone_name}", file=sys.stderr)
        return 1
    if args.strict and (telegram_owner_id is None or not timezone_name):
        print(
            "Strict readiness requires a positive Telegram owner ID and valid timezone "
            "from flags or environment.",
            file=sys.stderr,
        )
        return 1
    results = evaluate_v4_readiness(
        root=root,
        database_path=Path(args.database),
        backup_dir=Path(args.backup_dir),
        required_days=args.days,
        telegram_owner_id=telegram_owner_id,
        display_timezone=display_timezone,
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
    telegram_owner_id: int | None = None,
    display_timezone: tzinfo = UTC,
    now: datetime | None = None,
) -> list[GateResult]:
    return [
        _release_metadata_gate(root, tag=tag),
        _working_tree_gate(root, require_clean=require_clean),
        _module_size_gate(root),
        _review_window_gate(
            database_path,
            required_days=required_days,
            telegram_owner_id=telegram_owner_id,
            display_timezone=display_timezone,
            now=now,
        ),
        _production_regression_gate(
            root,
            database_path,
            required_days=required_days,
            telegram_owner_id=telegram_owner_id,
            display_timezone=display_timezone,
            now=now,
        ),
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


def _review_window_gate(
    database_path: Path,
    *,
    required_days: int,
    telegram_owner_id: int | None = None,
    display_timezone: tzinfo = UTC,
    now: datetime | None = None,
) -> GateResult:
    report = review_window_report(
        database_path,
        required_days=required_days,
        telegram_owner_id=telegram_owner_id,
        display_timezone=display_timezone,
        now=now,
    )
    if report.gate_passed:
        return GateResult("OK", "Review window", report.summary())
    level = "FAIL" if report.status == "failed" else "WARN"
    return GateResult(level, "Review window", report.summary())


def _production_regression_gate(
    root: Path,
    database_path: Path,
    *,
    required_days: int,
    telegram_owner_id: int | None,
    display_timezone: tzinfo,
    now: datetime | None,
) -> GateResult:
    registry_path = root / "qa/production_regressions.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return GateResult("FAIL", "Production regressions", f"invalid registry: {exc}")
    if not isinstance(registry, dict) or registry.get("schema_version") != 1:
        return GateResult("FAIL", "Production regressions", "registry schema_version must be 1")
    links = registry.get("links")
    if not isinstance(links, list):
        return GateResult("FAIL", "Production regressions", "registry links must be a list")
    parsed_links: dict[str, str] = {}
    for index, link in enumerate(links):
        if not isinstance(link, dict) or set(link) != {"feedback_event_id", "fixture_id"}:
            return GateResult(
                "FAIL",
                "Production regressions",
                f"link {index} must contain only feedback_event_id and fixture_id",
            )
        feedback_id = link.get("feedback_event_id")
        fixture_id = link.get("fixture_id")
        if not isinstance(feedback_id, str) or not feedback_id.strip():
            return GateResult("FAIL", "Production regressions", f"link {index} has invalid feedback_event_id")
        if not isinstance(fixture_id, str) or not fixture_id.strip():
            return GateResult("FAIL", "Production regressions", f"link {index} has invalid fixture_id")
        if feedback_id in parsed_links:
            return GateResult("FAIL", "Production regressions", f"duplicate feedback link: {feedback_id}")
        parsed_links[feedback_id] = fixture_id

    fixture_ids = _transcript_fixture_ids(root / "tests/evaluation/transcripts")
    missing_fixtures = sorted({fixture for fixture in parsed_links.values() if fixture not in fixture_ids})
    if missing_fixtures:
        return GateResult(
            "FAIL",
            "Production regressions",
            f"unknown transcript fixture(s): {', '.join(missing_fixtures)}",
        )

    report = review_window_report(
        database_path,
        required_days=required_days,
        telegram_owner_id=telegram_owner_id,
        display_timezone=display_timezone,
        now=now,
    )
    normalized_now = now or datetime.now(UTC)
    if normalized_now.tzinfo is None:
        normalized_now = normalized_now.replace(tzinfo=UTC)
    normalized_now = normalized_now.astimezone(UTC)
    feedback_ids, all_valid_production_ids, malformed_ids = _regression_feedback_ids(
        database_path,
        since=report.window_start,
        until=report.window_end,
        end_inclusive=report.window_end == normalized_now,
    )
    if feedback_ids is None:
        return GateResult(
            "FAIL",
            "Production regressions",
            "could not inspect feedback events in the database",
        )
    if malformed_ids:
        return GateResult(
            "FAIL",
            "Production regressions",
            f"malformed production feedback event(s): {', '.join(sorted(malformed_ids))}",
        )
    unknown_feedback = sorted(set(parsed_links) - all_valid_production_ids)
    if unknown_feedback:
        return GateResult(
            "FAIL",
            "Production regressions",
            f"unknown or non-required feedback link(s): {', '.join(unknown_feedback)}",
        )
    uncovered = sorted(feedback_ids - set(parsed_links))
    if uncovered:
        return GateResult(
            "FAIL",
            "Production regressions",
            f"{len(uncovered)} high/critical feedback event(s) lack transcript links: {', '.join(uncovered)}",
        )
    return GateResult(
        "OK",
        "Production regressions",
        f"{len(feedback_ids)}/{len(feedback_ids)} high/critical feedback event(s) covered",
    )


def _regression_feedback_ids(
    database_path: Path,
    *,
    since: datetime,
    until: datetime,
    end_inclusive: bool,
) -> tuple[set[str] | None, set[str], set[str]]:
    if str(database_path) == ":memory:" or not database_path.exists():
        return set(), set(), set()
    try:
        conn = sqlite3.connect(database_path)
        rows = conn.execute(
            """
            SELECT id, payload, created_at FROM event_logs
            WHERE event_type = 'user_feedback_recorded'
            """,
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return None, set(), set()
    result: set[str] = set()
    all_valid: set[str] = set()
    malformed: set[str] = set()
    for event_id, raw_payload, raw_created_at in rows:
        created_at = _parse_event_time(raw_created_at)
        in_current_window = (
            created_at is not None
            and created_at >= since
            and (created_at <= until if end_inclusive else created_at < until)
        )
        try:
            payload = json.loads(raw_payload or "{}")
        except json.JSONDecodeError:
            if in_current_window:
                malformed.add(str(event_id))
            continue
        if not isinstance(payload, dict):
            if in_current_window:
                malformed.add(str(event_id))
            continue
        has_transport_key = bool(
            {"source_chat_id", "source_message_id", "turn"}.intersection(payload)
        )
        if has_transport_key and in_current_window:
            malformed.add(str(event_id))
            continue
        is_valid_production = valid_feedback_payload(payload, require_production=True)
        if is_valid_production:
            all_valid.add(str(event_id))
        if not in_current_window:
            continue
        if payload.get("provenance") == "telegram_owner_private" and not is_valid_production:
            malformed.add(str(event_id))
            continue
        if is_valid_production and feedback_requires_regression(payload):
            result.add(str(event_id))
    return result, all_valid, malformed


def _parse_event_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _transcript_fixture_ids(directory: Path) -> set[str]:
    result: set[str] = set()
    if not directory.exists():
        return result
    for path in directory.glob("*.json"):
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = payload.get("transcripts", [payload]) if isinstance(payload, dict) else []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                result.add(item["name"])
    return result


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
    parser.add_argument(
        "--telegram-owner-id",
        type=int,
        help="Owner ID required to verify legacy Telegram note observation days.",
    )
    parser.add_argument(
        "--timezone",
        help="IANA timezone used for owner-local review-window days.",
    )
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


def _positive_int(value: str | None) -> int | None:
    try:
        parsed = int(value or "")
    except ValueError:
        return None
    return parsed if parsed > 0 else None


if __name__ == "__main__":
    raise SystemExit(main())
