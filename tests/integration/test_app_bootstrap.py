import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from memocore import app as app_module
from memocore.config import ModelConfig, Settings


async def test_create_app_wires_all_v4_services(monkeypatch, tmp_path):
    fake_app = SimpleNamespace(bot_data={}, bot=AsyncMock())
    monkeypatch.setattr(app_module, "create_bot", lambda *args: fake_app)

    async def idle_loop(*args, **kwargs):
        await asyncio.Event().wait()

    for name in (
        "reminder_dispatch_loop",
        "scheduled_morning_briefing_loop",
        "proactive_nudge_loop",
        "scheduled_weekly_review_loop",
        "scheduled_backup_loop",
    ):
        monkeypatch.setattr(app_module, name, idle_loop)

    settings = Settings(
        telegram_bot_token="test-token",
        telegram_owner_id=9001,
        database_path=tmp_path / "bootstrap.db",
        model=ModelConfig(provider="ollama", name="test-model"),
        _env_file=None,
    )

    created = await app_module.create_app(settings)

    assert created is fake_app
    assert fake_app.bot_data["database"] is not None
    assert fake_app.bot_data["reminder_task"] is not None
    conn = await fake_app.bot_data["database"].connection()
    migration = await (
        await conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            ("008_activity_links.sql",),
        )
    ).fetchone()
    assert migration is not None

    background_tasks = [
        value
        for key, value in fake_app.bot_data.items()
        if key.endswith("_task")
    ]
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)
    await fake_app.bot_data["database"].close()
