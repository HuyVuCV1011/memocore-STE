from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

from memocore.adapters.storage.repositories import EventLogRepository
from memocore.adapters.storage.sqlite import Database
from memocore.domain.models import EventType, FeedbackSignal
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
        await event_service.record_owner_observation(
            "message",
            observed_at=now - timedelta(days=offset),
            display_timezone=UTC,
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


async def test_v4_readiness_fails_when_high_feedback_has_no_regression_link(
    tmp_path, monkeypatch
):
    root = _minimal_root(tmp_path)
    database = Database(tmp_path / "memocore.db")
    await database.initialize()
    event_service = EventService(EventLogRepository(database))
    await event_service.record_owner_observation("message", display_timezone=UTC)
    feedback = await event_service.record_feedback(
        FeedbackSignal.CORRECTION,
        "task",
        "task-1",
        source_chat_id="owner-chat",
        source_message_id="message-1",
        details={"severity": "high", "category": "wrong_entity"},
    )
    await database.close()
    _ignore_module_budget(monkeypatch)

    results = v4_readiness_gate.evaluate_v4_readiness(
        root=root,
        database_path=tmp_path / "memocore.db",
        backup_dir=tmp_path / "backups",
    )

    gate = _result(results, "Production regressions")
    assert gate.level == "FAIL"
    assert feedback.id in gate.detail


async def test_v4_readiness_rejects_nonexistent_regression_fixture(tmp_path, monkeypatch):
    root = _minimal_root(tmp_path)
    database = Database(tmp_path / "memocore.db")
    await database.initialize()
    event_service = EventService(EventLogRepository(database))
    await event_service.record_owner_observation("message", display_timezone=UTC)
    feedback = await event_service.record_feedback(
        FeedbackSignal.CORRECTION,
        "task",
        "task-1",
        source_chat_id="owner-chat",
        source_message_id="message-1",
        details={"severity": "critical"},
    )
    await database.close()
    (root / "qa/production_regressions.json").write_text(
        '{"schema_version":1,"links":[{"feedback_event_id":"'
        + feedback.id
        + '","fixture_id":"missing fixture"}]}',
        encoding="utf-8",
    )
    _ignore_module_budget(monkeypatch)

    results = v4_readiness_gate.evaluate_v4_readiness(
        root=root,
        database_path=tmp_path / "memocore.db",
        backup_dir=tmp_path / "backups",
    )

    gate = _result(results, "Production regressions")
    assert gate.level == "FAIL"
    assert "unknown transcript fixture" in gate.detail


async def test_v4_readiness_counts_valid_high_feedback_regression_link(tmp_path, monkeypatch):
    root = _minimal_root(tmp_path)
    database = Database(tmp_path / "memocore.db")
    await database.initialize()
    event_service = EventService(EventLogRepository(database))
    await event_service.record_owner_observation("message", display_timezone=UTC)
    feedback = await event_service.record_feedback(
        FeedbackSignal.CORRECTION,
        "task",
        "task-1",
        source_chat_id="owner-chat",
        source_message_id="message-1",
        details={"severity": "high"},
    )
    await database.close()
    (root / "qa/production_regressions.json").write_text(
        '{"schema_version":1,"links":[{"feedback_event_id":"'
        + feedback.id
        + '","fixture_id":"known fixture"}]}',
        encoding="utf-8",
    )
    _ignore_module_budget(monkeypatch)

    results = v4_readiness_gate.evaluate_v4_readiness(
        root=root,
        database_path=tmp_path / "memocore.db",
        backup_dir=tmp_path / "backups",
    )

    gate = _result(results, "Production regressions")
    assert gate.level == "OK"
    assert "1/1" in gate.detail


async def test_v4_readiness_excludes_nonproduction_feedback(tmp_path, monkeypatch):
    root = _minimal_root(tmp_path)
    database = Database(tmp_path / "memocore.db")
    await database.initialize()
    await EventService(EventLogRepository(database)).append_event(
        EventType.USER_FEEDBACK_RECORDED,
        "task",
        "task-legacy",
        {"signal": "correction", "status": "open", "severity": "high"},
    )
    await database.close()
    _ignore_module_budget(monkeypatch)

    results = v4_readiness_gate.evaluate_v4_readiness(
        root=root,
        database_path=tmp_path / "memocore.db",
        backup_dir=tmp_path / "backups",
    )

    gate = _result(results, "Production regressions")
    assert gate.level == "OK"
    assert "0/0" in gate.detail


async def test_v4_readiness_rejects_registry_link_to_nonproduction_feedback(
    tmp_path, monkeypatch
):
    root = _minimal_root(tmp_path)
    database = Database(tmp_path / "memocore.db")
    await database.initialize()
    feedback = await EventService(EventLogRepository(database)).append_event(
        EventType.USER_FEEDBACK_RECORDED,
        "task",
        "task-nonproduction",
        {"signal": "correction", "status": "open", "severity": "high"},
    )
    await database.close()
    (root / "qa/production_regressions.json").write_text(
        '{"schema_version":1,"links":[{"feedback_event_id":"'
        + feedback.id
        + '","fixture_id":"known fixture"}]}',
        encoding="utf-8",
    )
    _ignore_module_budget(monkeypatch)

    results = v4_readiness_gate.evaluate_v4_readiness(
        root=root,
        database_path=tmp_path / "memocore.db",
        backup_dir=tmp_path / "backups",
    )

    gate = _result(results, "Production regressions")
    assert gate.level == "FAIL"
    assert feedback.id in gate.detail


async def test_v4_readiness_requires_medium_wrong_entity_regression(tmp_path, monkeypatch):
    root = _minimal_root(tmp_path)
    database = Database(tmp_path / "memocore.db")
    await database.initialize()
    event_service = EventService(EventLogRepository(database))
    await event_service.record_owner_observation("message", display_timezone=UTC)
    feedback = await event_service.record_feedback(
        FeedbackSignal.CORRECTION,
        "task",
        "task-1",
        source_chat_id="owner-chat",
        source_message_id="message-1",
        details={"severity": "medium", "category": "wrong_entity"},
    )
    await database.close()
    _ignore_module_budget(monkeypatch)

    results = v4_readiness_gate.evaluate_v4_readiness(
        root=root,
        database_path=tmp_path / "memocore.db",
        backup_dir=tmp_path / "backups",
    )

    gate = _result(results, "Production regressions")
    assert gate.level == "FAIL"
    assert feedback.id in gate.detail


async def test_v4_readiness_fails_closed_on_malformed_production_feedback(
    tmp_path, monkeypatch
):
    root = _minimal_root(tmp_path)
    database = Database(tmp_path / "memocore.db")
    await database.initialize()
    event_service = EventService(EventLogRepository(database))
    await event_service.record_owner_observation("message", display_timezone=UTC)
    feedback = await event_service.append_event(
        EventType.USER_FEEDBACK_RECORDED,
        "task",
        "task-malformed",
        {"provenance": "telegram_owner_private", "schema_version": 1},
    )
    await database.close()
    _ignore_module_budget(monkeypatch)

    results = v4_readiness_gate.evaluate_v4_readiness(
        root=root,
        database_path=tmp_path / "memocore.db",
        backup_dir=tmp_path / "backups",
    )

    gate = _result(results, "Production regressions")
    assert gate.level == "FAIL"
    assert feedback.id in gate.detail


async def test_v4_readiness_fails_closed_on_malformed_json_and_non_object_feedback(
    tmp_path, monkeypatch
):
    root = _minimal_root(tmp_path)
    database_path = tmp_path / "memocore.db"
    database = Database(database_path)
    await database.initialize()
    await EventService(EventLogRepository(database)).record_owner_observation(
        "message", display_timezone=UTC
    )
    await database.close()
    created_at = datetime.now(UTC).isoformat()
    with sqlite3.connect(database_path) as conn:
        conn.executemany(
            """
            INSERT INTO event_logs (id, event_type, entity_type, entity_id, payload, created_at)
            VALUES (?, 'user_feedback_recorded', 'task', ?, ?, ?)
            """,
            (
                ("malformed-json", "task-json", "{", created_at),
                ("non-object-json", "task-array", "[]", created_at),
                (
                    "partial-transport",
                    "task-partial",
                    '{"schema_version":1,"source_chat_id":"owner-chat"}',
                    created_at,
                ),
            ),
        )
    _ignore_module_budget(monkeypatch)

    results = v4_readiness_gate.evaluate_v4_readiness(
        root=root,
        database_path=database_path,
        backup_dir=tmp_path / "backups",
    )

    gate = _result(results, "Production regressions")
    assert gate.level == "FAIL"
    assert "malformed-json" in gate.detail
    assert "non-object-json" in gate.detail
    assert "partial-transport" in gate.detail


async def test_v4_readiness_excludes_old_production_feedback(tmp_path, monkeypatch):
    root = _minimal_root(tmp_path)
    database = Database(tmp_path / "memocore.db")
    await database.initialize()
    now = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    feedback = await EventService(EventLogRepository(database)).append_event(
        EventType.USER_FEEDBACK_RECORDED,
        "task",
        "task-old",
        {
            "schema_version": 1,
            "metadata_policy_version": 1,
            "provenance": "telegram_owner_private",
            "signal": "correction",
            "status": "open",
            "artifact": {"type": "task", "id": "task-old"},
            "source_note_id": None,
            "details": {"severity": "critical"},
        },
        created_at=now - timedelta(days=30),
    )
    await database.close()
    (root / "qa/production_regressions.json").write_text(
        '{"schema_version":1,"links":[{"feedback_event_id":"'
        + feedback.id
        + '","fixture_id":"known fixture"}]}',
        encoding="utf-8",
    )
    _ignore_module_budget(monkeypatch)

    results = v4_readiness_gate.evaluate_v4_readiness(
        root=root,
        database_path=tmp_path / "memocore.db",
        backup_dir=tmp_path / "backups",
        now=now,
    )

    gate = _result(results, "Production regressions")
    assert gate.level == "OK"
    assert "0/0" in gate.detail


async def test_v4_readiness_rejects_unknown_feedback_link(tmp_path, monkeypatch):
    root = _minimal_root(tmp_path)
    await _initialized_database(tmp_path / "memocore.db")
    (root / "qa/production_regressions.json").write_text(
        '{"schema_version":1,"links":[{"feedback_event_id":"unknown-id",'
        '"fixture_id":"known fixture"}]}',
        encoding="utf-8",
    )
    _ignore_module_budget(monkeypatch)

    results = v4_readiness_gate.evaluate_v4_readiness(
        root=root,
        database_path=tmp_path / "memocore.db",
        backup_dir=tmp_path / "backups",
    )

    gate = _result(results, "Production regressions")
    assert gate.level == "FAIL"
    assert "unknown or non-required" in gate.detail


def test_v4_readiness_cli_strict_requires_owner_and_timezone(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_OWNER_ID", raising=False)
    monkeypatch.delenv("USER_TIMEZONE", raising=False)

    result = v4_readiness_gate.main(
        ["--root", str(tmp_path), "--database", str(tmp_path / "db"), "--strict"]
    )

    assert result == 1
    assert "requires a positive Telegram owner ID" in capsys.readouterr().err


def test_v4_readiness_cli_uses_owner_and_timezone_environment(monkeypatch):
    captured = {}
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "9001")
    monkeypatch.setenv("USER_TIMEZONE", "Asia/Bangkok")

    def fake_evaluate(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(v4_readiness_gate, "evaluate_v4_readiness", fake_evaluate)

    assert v4_readiness_gate.main(["--strict"]) == 0
    assert captured["telegram_owner_id"] == 9001
    assert str(captured["display_timezone"]) == "Asia/Bangkok"


def test_v4_readiness_cli_flags_override_environment(monkeypatch):
    captured = {}
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "111")
    monkeypatch.setenv("USER_TIMEZONE", "UTC")
    monkeypatch.setattr(
        v4_readiness_gate,
        "evaluate_v4_readiness",
        lambda **kwargs: captured.update(kwargs) or [],
    )

    assert (
        v4_readiness_gate.main(
            ["--strict", "--telegram-owner-id", "9001", "--timezone", "Asia/Bangkok"]
        )
        == 0
    )
    assert captured["telegram_owner_id"] == 9001
    assert str(captured["display_timezone"]) == "Asia/Bangkok"


def test_v4_readiness_cli_rejects_zero_owner(monkeypatch, capsys):
    monkeypatch.setenv("USER_TIMEZONE", "UTC")
    monkeypatch.delenv("TELEGRAM_OWNER_ID", raising=False)

    result = v4_readiness_gate.main(
        ["--strict", "--telegram-owner-id", "0", "--timezone", "UTC"]
    )

    assert result == 1
    assert "positive Telegram owner ID" in capsys.readouterr().err


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
    (root / "qa").mkdir()
    (root / "qa/production_regressions.json").write_text(
        '{"schema_version":1,"links":[]}', encoding="utf-8"
    )
    (root / "tests/evaluation/transcripts").mkdir(parents=True)
    (root / "tests/evaluation/transcripts/known.json").write_text(
        '{"name":"known fixture","steps":[]}', encoding="utf-8"
    )
    return root


def _ignore_module_budget(monkeypatch) -> None:
    monkeypatch.setattr(
        module_size_check,
        "MODULE_BUDGETS",
        (module_size_check.ModuleBudget("src/memocore/services/conversation_service.py", 10, ""),),
    )


async def _initialized_database(path: Path) -> Database:
    database = Database(path)
    await database.initialize()
    await database.close()
    return database


def _result(results: list[v4_readiness_gate.GateResult], name: str) -> v4_readiness_gate.GateResult:
    return next(result for result in results if result.name == name)
