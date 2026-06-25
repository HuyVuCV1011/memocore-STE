from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from memocore.adapters.storage.sqlite import Database
from memocore.cli.doctor import EXPECTED_COMMANDS, has_failures, run_doctor
from memocore.config import Settings


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
            model_provider="ollama",
        )
    )

    assert has_failures(results) is False
    assert {result.name for result in results} >= {
        "Config",
        "SQLite",
        "Runtime data",
        "Telegram",
        "PM2",
    }


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
        "memory",
        "context",
        "briefing",
        "capture",
        "review",
    )
