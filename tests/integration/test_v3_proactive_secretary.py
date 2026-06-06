from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from memocore.app import (
    send_due_morning_briefings,
    send_due_nudges,
    send_due_weekly_reviews,
)
from memocore.config import Settings
from memocore.domain.models import FollowUp, Note, Reminder, ReminderStatus, Task
from memocore.domain.schemas import CaptureRequest
from memocore.services.event_service import EventService
from memocore.services.reminder_service import ReminderService
from memocore.services.secretary_service import SecretaryService


def _settings(**overrides) -> Settings:
    values = {
        "telegram_bot_token": "test-token",
        "user_timezone": "UTC",
        "quiet_hours_start": None,
        "quiet_hours_end": None,
    }
    values.update(overrides)
    return Settings(**values)


def _secretary(repos) -> SecretaryService:
    return SecretaryService(
        repos["tasks"],
        repos["reminders"],
        repos["followups"],
        repos["projects"],
        repos["memory"],
        meeting_repo=None,
    )


async def test_v31_manual_daily_briefing_groups_open_loops(repos):
    now = datetime(2026, 6, 8, 8, 0, tzinfo=UTC)
    note = await repos["notes"].create(
        Note(raw_text="setup", source_chat_id="9001", source_message_id="1")
    )
    await repos["tasks"].create(
        Task(
            title="Overdue budget review",
            source_note_id=note.id,
            due_at=now - timedelta(days=1),
        )
    )
    await repos["tasks"].create(
        Task(
            title="Prepare Alex meeting",
            source_note_id=note.id,
            due_at=now + timedelta(hours=2),
        )
    )
    await repos["reminders"].create(
        Reminder(
            title="Ping Alex",
            source_note_id=note.id,
            remind_at=now + timedelta(hours=1),
            status=ReminderStatus.SCHEDULED,
        )
    )
    await repos["followups"].create(FollowUp(title="Ask Lan for update", source_note_id=note.id))

    briefing = await _secretary(repos).daily_briefing(now)

    assert "Briefing hôm nay" in briefing
    assert "Overdue budget review" in briefing
    assert "Prepare Alex meeting" in briefing
    assert "Ping Alex" in briefing
    assert "Ask Lan for update" in briefing


async def test_v32_scheduled_morning_briefing_sends_once_per_day(repos):
    now = datetime(2026, 6, 8, 8, 5, tzinfo=UTC)
    await repos["notes"].create(Note(raw_text="hello", source_chat_id="9001", source_message_id="1"))
    bot = AsyncMock()
    event_service = EventService(repos["events"])
    settings = _settings(morning_briefing_time="08:00")

    first = await send_due_morning_briefings(
        _secretary(repos), repos["notes"], event_service, bot, settings, now
    )
    second = await send_due_morning_briefings(
        _secretary(repos), repos["notes"], event_service, bot, settings, now
    )

    assert first == 1
    assert second == 0
    assert bot.send_message.await_count == 1
    assert bot.send_message.await_args.kwargs["chat_id"] == 9001
    assert "Briefing hôm nay" in bot.send_message.await_args.kwargs["text"]


async def test_v33_recurring_daily_reminder_is_rescheduled_after_send(
    capture_service, fake_provider, repos
):
    response = await capture_service.capture(
        CaptureRequest(
            raw_text="Nhắc tôi mỗi ngày 8h uống thuốc",
            source_chat_id="9001",
            source_message_id="1",
        )
    )
    reminders = await repos["reminders"].list_by_note(response.note_id)
    reminder = reminders[0]
    service = ReminderService(repos["reminders"], EventService(repos["events"]))

    await service.mark_sent(reminder.id)
    updated = await repos["reminders"].get_by_id(reminder.id)

    assert response.reminders_created == 1
    assert reminder.title == "uống thuốc"
    assert reminder.recurrence_rule == "daily"
    assert updated.status == ReminderStatus.SCHEDULED
    assert updated.remind_at == reminder.remind_at + timedelta(days=1)
    assert fake_provider.calls == []


async def test_v33_recurring_weekly_reminder_is_supported(capture_service, fake_provider, repos):
    response = await capture_service.capture(
        CaptureRequest(
            raw_text="Nhắc tôi mỗi thứ 2 9h review team",
            source_chat_id="9001",
            source_message_id="1",
        )
    )
    reminder = (await repos["reminders"].list_by_note(response.note_id))[0]

    assert reminder.title == "review team"
    assert reminder.recurrence_rule == "weekly:0"
    assert reminder.status == ReminderStatus.SCHEDULED
    assert fake_provider.calls == []


async def test_v34_deadline_nudges_respect_cooldown_and_quiet_hours(repos):
    now = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    note = await repos["notes"].create(
        Note(raw_text="setup", source_chat_id="9001", source_message_id="1")
    )
    await repos["tasks"].create(
        Task(title="Submit report", source_note_id=note.id, due_at=now - timedelta(hours=2))
    )
    bot = AsyncMock()
    event_service = EventService(repos["events"])
    settings = _settings(proactive_nudge_cooldown_hours=24)

    first = await send_due_nudges(_secretary(repos), repos["notes"], event_service, bot, settings, now)
    second = await send_due_nudges(_secretary(repos), repos["notes"], event_service, bot, settings, now)
    quiet = await send_due_nudges(
        _secretary(repos),
        repos["notes"],
        event_service,
        bot,
        _settings(quiet_hours_start="11:00", quiet_hours_end="13:00"),
        now,
    )

    assert first == 1
    assert second == 0
    assert quiet == 0
    assert bot.send_message.await_count == 1
    assert "Task quá hạn" in bot.send_message.await_args.kwargs["text"]


async def test_v35_weekly_review_sends_once_and_includes_done_task(repos):
    now = datetime(2026, 6, 8, 8, 35, tzinfo=UTC)
    note = await repos["notes"].create(
        Note(raw_text="setup", source_chat_id="9001", source_message_id="1")
    )
    task = await repos["tasks"].create(Task(title="Finish V3 plan", source_note_id=note.id))
    await repos["tasks"].update_status(task.id, "done")
    bot = AsyncMock()
    event_service = EventService(repos["events"])
    settings = _settings(weekly_review_weekday=0, weekly_review_time="08:30")

    first = await send_due_weekly_reviews(
        _secretary(repos), repos["notes"], event_service, bot, settings, now
    )
    second = await send_due_weekly_reviews(
        _secretary(repos), repos["notes"], event_service, bot, settings, now
    )

    assert first == 1
    assert second == 0
    assert bot.send_message.await_count == 1
    assert "Weekly review" in bot.send_message.await_args.kwargs["text"]
    assert "Finish V3 plan" in bot.send_message.await_args.kwargs["text"]
