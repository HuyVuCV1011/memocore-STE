from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from memocore.adapters.storage.repositories import EventLogRepository
from memocore.adapters.storage.sqlite import Database
from memocore.domain.models import EventType
from memocore.services.backup_service import BackupService
from memocore.services.event_service import EventService
from scripts.quality import module_size_check, v4_readiness_gate


async def test_v4_readiness_reports_collecting_until_review_window_passes(
    tmp_path,
    monkeypatch,
):
    root = _minimal_root(tmp_path)
    await _initialized_database(tmp_path / "memocore.db")
    backup_dir = tmp_path / "backups"
    service = BackupService(tmp_path / "memocore.db", backup_dir)
    backup = service.create_backup()
    service.run_restore_drill(backup.backup_id)
    monkeypatch.setattr(
        module_size_check,
        "MODULE_BUDGETS",
        (module_size_check.ModuleBudget("src/memocore/services/conversation_service.py", 10, ""),),
    )

    results = v4_readiness_gate.evaluate_v4_readiness(
        root=root,
        database_path=tmp_path / "memocore.db",
        backup_dir=backup_dir,
        required_days=14,
    )

    assert _result(results, "Release metadata").level == "OK"
    assert _result(results, "Module size").level == "OK"
    assert _result(results, "Backup/restore").level == "OK"
    assert _result(results, "Review window").level == "WARN"


async def test_v4_readiness_passes_after_review_window_observations(tmp_path, monkeypatch):
    root = _minimal_root(tmp_path)
    database = Database(tmp_path / "memocore.db")
    await database.initialize()
    event_service = EventService(EventLogRepository(database))
    now = datetime.now(UTC)
    for offset in range(3):
        await event_service.append_event(
            EventType.NOTE_CAPTURED,
            "note",
            f"note-{offset}",
            created_at=now - timedelta(days=offset),
        )
    await database.close()

    backup_dir = tmp_path / "backups"
    service = BackupService(tmp_path / "memocore.db", backup_dir)
    backup = service.create_backup()
    service.run_restore_drill(backup.backup_id)
    monkeypatch.setattr(
        module_size_check,
        "MODULE_BUDGETS",
        (module_size_check.ModuleBudget("src/memocore/services/conversation_service.py", 10, ""),),
    )

    results = v4_readiness_gate.evaluate_v4_readiness(
        root=root,
        database_path=tmp_path / "memocore.db",
        backup_dir=backup_dir,
        required_days=3,
    )

    assert all(result.level == "OK" for result in results if result.name != "Working tree")


def test_v4_readiness_fails_release_tag_without_changelog_section(tmp_path, monkeypatch):
    root = _minimal_root(tmp_path)
    monkeypatch.setattr(
        module_size_check,
        "MODULE_BUDGETS",
        (module_size_check.ModuleBudget("src/memocore/services/conversation_service.py", 10, ""),),
    )

    result = v4_readiness_gate.evaluate_v4_readiness(
        root=root,
        database_path=tmp_path / "missing.db",
        backup_dir=tmp_path / "backups",
        tag="v0.4.1",
    )[0]

    assert result.level == "FAIL"
    assert "missing release section" in result.detail


def _minimal_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src/memocore/services").mkdir(parents=True)
    (root / "src/memocore/__init__.py").write_text('__version__ = "0.4.1"\n', encoding="utf-8")
    (root / "src/memocore/services/conversation_service.py").write_text("pass\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "memocore"\nversion = "0.4.1"\n',
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n", encoding="utf-8")
    return root


async def _initialized_database(path: Path) -> Database:
    database = Database(path)
    await database.initialize()
    await database.close()
    return database


def _result(results: list[v4_readiness_gate.GateResult], name: str) -> v4_readiness_gate.GateResult:
    return next(result for result in results if result.name == name)
