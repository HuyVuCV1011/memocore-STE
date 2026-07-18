from __future__ import annotations

from types import SimpleNamespace

from memocore.adapters.storage.sqlite import Database
from memocore.cli.doctor import (
    EXPECTED_COMMANDS,
    _check_backups,
    _pm2_deploy_stamp,
    has_failures,
    run_doctor,
)
from memocore.config import Settings


async def test_doctor_fails_closed_on_incomplete_restore_journal(tmp_path):
    import json

    from memocore.services.backup_service import BackupService

    db_path = tmp_path / "memocore.db"
    database = Database(db_path)
    await database.initialize()
    await database.close()
    backup_dir = tmp_path / "backups"
    BackupService(db_path, backup_dir).create_backup()
    (backup_dir / "latest-restore.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "incomplete",
                "phase": "swap_pending",
                "failure_code": "unknown",
                "rollback": "not_started",
            }
        ),
        encoding="utf-8",
    )

    result = _check_backups(
        Settings(
            telegram_bot_token="test-token",
            telegram_owner_id=9001,
            database_path=db_path,
            backup_dir=backup_dir,
        )
    )

    assert result.level == "FAIL"
    assert "rollback=not_started" in result.detail


async def test_doctor_fails_closed_on_malformed_restore_journal(tmp_path):
    db_path = tmp_path / "memocore.db"
    database = Database(db_path)
    await database.initialize()
    await database.close()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "latest-restore.json").write_text("{bad", encoding="utf-8")

    result = _check_backups(
        Settings(
            telegram_bot_token="test-token",
            telegram_owner_id=9001,
            database_path=db_path,
            backup_dir=backup_dir,
        )
    )

    assert result.level == "FAIL"
    assert "unreadable or invalid" in result.detail


async def test_doctor_reports_healthy_runtime_without_live_provider(tmp_path, monkeypatch):
    db_path = tmp_path / "memocore.db"
    database = Database(db_path)
    await database.initialize()
    await database.close()

    async def fake_telegram(settings):
        return SimpleNamespace(level="OK", name="Telegram", detail="fake")

    def fake_pm2():
        return SimpleNamespace(level="OK", name="PM2", detail="fake")

    monkeypatch.setattr("memocore.cli.doctor._check_telegram", fake_telegram)
    monkeypatch.setattr("memocore.cli.doctor._check_pm2_process", fake_pm2)

    results = await run_doctor(
        Settings(
            telegram_bot_token="test-token",
            telegram_owner_id=9001,
                database_path=db_path,
                backup_dir=tmp_path / "backups",
                model_provider="ollama",
        )
    )

    assert has_failures(results) is False
    assert {result.name for result in results} >= {
        "Runtime version",
        "Review window",
        "Config",
        "SQLite",
        "Runtime data",
        "Telegram",
        "PM2",
    }
    runtime_version = next(result for result in results if result.name == "Runtime version")
    assert "package=0.4.1" in runtime_version.detail
    assert "commit=" in runtime_version.detail
    assert "dirty=" in runtime_version.detail
    assert "schema=008_activity_links.sql" in runtime_version.detail


async def test_doctor_warns_about_invalid_chat_ids(tmp_path, monkeypatch):
    db_path = tmp_path / "memocore.db"
    database = Database(db_path)
    await database.initialize()
    conn = await database.connection()
    await conn.execute(
        """
        INSERT INTO notes (
            id, source, source_message_id, source_chat_id, raw_text, summary,
            tags, status, metadata, created_at, updated_at
        ) VALUES (
            'note-1', 'telegram', '1', 'test_chat_id', 'raw', '', '[]',
            'captured', '{}', datetime('now'), datetime('now')
        )
        """
    )
    await conn.commit()
    await database.close()

    async def fake_telegram(settings):
        return SimpleNamespace(level="OK", name="Telegram", detail="fake")

    def fake_pm2():
        return SimpleNamespace(level="OK", name="PM2", detail="fake")

    monkeypatch.setattr("memocore.cli.doctor._check_telegram", fake_telegram)
    monkeypatch.setattr("memocore.cli.doctor._check_pm2_process", fake_pm2)

    results = await run_doctor(
        Settings(
            telegram_bot_token="test-token",
            telegram_owner_id=9001,
            database_path=db_path,
            model_provider="ollama",
        )
    )

    runtime = next(result for result in results if result.name == "Runtime data")
    assert runtime.level == "WARN"
    assert "test_chat_id" in runtime.detail


def test_expected_telegram_command_contract_is_small():
    assert EXPECTED_COMMANDS == (
        "today",
        "work",
        "context",
        "search",
        "review",
    )


def test_pm2_deploy_stamp_formats_recorded_runtime_env():
    detail = _pm2_deploy_stamp(
        {
            "MEMOCORE_DEPLOY_COMMIT": "abc123",
            "MEMOCORE_DEPLOY_DIRTY": "no",
            "MEMOCORE_DEPLOY_SCHEMA": "008_activity_links.sql",
            "MEMOCORE_DEPLOYED_AT": "2026-07-15T00:00:00Z",
        }
    )

    assert "deploy_commit=abc123" in detail
    assert "deploy_dirty=no" in detail
    assert "deploy_schema=008_activity_links.sql" in detail
    assert "deployed_at=2026-07-15T00:00:00Z" in detail
