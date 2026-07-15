from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from memocore.app import (
    send_due_backups,
    send_due_morning_briefings,
    send_due_nudges,
    send_due_reminders,
    send_due_weekly_reviews,
)
from memocore.config import Settings
from memocore.domain.models import EventType, FollowUp, Note, Reminder, ReminderStatus, Task
from memocore.domain.schemas import CaptureRequest
from memocore.services.event_service import EventService
from memocore.services.reminder_service import ReminderService
from memocore.services.secretary_service import SecretaryService


def _settings(**overrides) -> Settings:
    values = {
        "telegram_bot_token": "test-token",
        "telegram_owner_id": 9001,
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
    assert "Nhận định" in briefing
    assert "Điểm cần chú ý" in briefing
    assert "Nên làm tiếp" in briefing
    assert "Overdue budget review" in briefing
    assert "Prepare Alex meeting" in briefing
    assert "1 lời nhắc" in briefing
    assert "Score:" not in briefing
    assert "Lý do:" not in briefing


async def test_briefing_empty_day_offers_a_proactive_next_step(repos):
    now = datetime(2026, 6, 8, 8, 0, tzinfo=UTC)

    briefing = await _secretary(repos).daily_briefing(now)

    assert "chưa có áp lực bắt buộc" in briefing
    assert "chọn một ưu tiên chủ động" in briefing
    assert "/task" in briefing


async def test_briefing_names_today_and_upcoming_tasks_in_analysis(repos):
    now = datetime(2026, 6, 20, 20, 15, tzinfo=UTC)
    note = await repos["notes"].create(Note(raw_text="briefing names"))
    await repos["tasks"].create(
        Task(
            title="Hoàn thiện ver 4.0 của memocore",
            source_note_id=note.id,
            due_at=datetime(2026, 6, 21, 16, 59, tzinfo=UTC),
        )
    )
    await repos["tasks"].create(
        Task(
            title="Tạo kịch bản audio sảng văn",
            source_note_id=note.id,
            due_at=datetime(2026, 6, 21, 17, 0, tzinfo=UTC),
        )
    )
    service = SecretaryService(
        repos["tasks"],
        repos["reminders"],
        repos["followups"],
        repos["projects"],
        repos["memory"],
        display_timezone=ZoneInfo("Asia/Ho_Chi_Minh"),
    )

    briefing = await service.daily_briefing(now)

    assert "Việc cần chốt hôm nay là “Hoàn thiện ver 4.0 của memocore”" in briefing
    assert "Hôm nay: “Hoàn thiện ver 4.0 của memocore” hạn 23:59 hôm nay." in briefing
    assert "Sắp tới: “Tạo kịch bản audio sảng văn” hạn 00:00 ngày mai." in briefing


async def test_task_views_show_recurrence_badge(repos):
    note = await repos["notes"].create(Note(raw_text="daily view"))
    await repos["tasks"].create(
        Task(
            title="Tạo kịch bản audio sảng văn",
            source_note_id=note.id,
            due_at=datetime(2026, 6, 22, 0, 0, tzinfo=UTC),
            recurrence_rule="daily",
            recurrence_series_id="daily-view",
            recurrence_occurrence_at=datetime(2026, 6, 22, 0, 0, tzinfo=UTC),
        )
    )

    tasks = await _secretary(repos).tasks()

    assert "Tạo kịch bản audio sảng văn · 🔁 Hằng ngày" in tasks


async def test_v32_scheduled_morning_briefing_sends_once_per_day(repos):
    now = datetime(2026, 6, 8, 8, 5, tzinfo=UTC)
    await repos["notes"].create(Note(raw_text="hello", source_chat_id="9001", source_message_id="1"))
    bot = AsyncMock()
    event_service = EventService(repos["events"])
    settings = _settings(morning_briefing_time="08:00")

    first = await send_due_morning_briefings(
        _secretary(repos), event_service, bot, settings, now
    )
    second = await send_due_morning_briefings(
        _secretary(repos), event_service, bot, settings, now
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


async def test_interval_recurring_reminder_is_rescheduled_after_send(
    capture_service, fake_provider, repos
):
    response = await capture_service.capture(
        CaptureRequest(
            raw_text="Nhắc tôi mỗi 2 ngày 8h tưới cây",
            source_chat_id="9001",
            source_message_id="interval-reminder",
        )
    )
    reminder = (await repos["reminders"].list_by_note(response.note_id))[0]
    service = ReminderService(repos["reminders"], EventService(repos["events"]))

    await service.mark_sent(reminder.id)
    updated = await repos["reminders"].get_by_id(reminder.id)

    assert response.reminders_created == 1
    assert reminder.title == "tưới cây"
    assert reminder.recurrence_rule == "interval:2d"
    assert updated.status == ReminderStatus.SCHEDULED
    assert updated.remind_at == reminder.remind_at + timedelta(days=2)
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

    first = await send_due_nudges(_secretary(repos), event_service, bot, settings, now)
    second = await send_due_nudges(_secretary(repos), event_service, bot, settings, now)
    quiet = await send_due_nudges(
        _secretary(repos),
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


async def test_followup_nudges_respect_explicit_followup_window_without_delaying_deadlines(
    repos,
):
    now = datetime(2026, 6, 8, 8, 0, tzinfo=UTC)
    note = await repos["notes"].create(
        Note(raw_text="preferred windows", source_chat_id="9001", source_message_id="1")
    )
    await repos["followups"].create(
        FollowUp(
            title="Ask Lan for update",
            source_note_id=note.id,
            due_at=now - timedelta(days=1),
        )
    )
    await repos["tasks"].create(
        Task(
            title="Submit report",
            source_note_id=note.id,
            due_at=now + timedelta(hours=2),
        )
    )
    bot = AsyncMock()
    event_service = EventService(repos["events"])

    sent = await send_due_nudges(
        _secretary(repos),
        event_service,
        bot,
        _settings(
            proactive_deadline_warning_hours=4,
            followup_nudge_window_start="13:00",
            followup_nudge_window_end="17:00",
        ),
        now,
    )

    assert sent == 1
    assert bot.send_message.await_count == 1
    assert "Submit report" in bot.send_message.await_args.kwargs["text"]
    assert "Ask Lan" not in bot.send_message.await_args.kwargs["text"]


async def test_predeadline_warning_sends_before_task_is_overdue(repos):
    now = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    note = await repos["notes"].create(
        Note(raw_text="setup", source_chat_id="9001", source_message_id="1")
    )
    await repos["tasks"].create(
        Task(title="Submit proposal", source_note_id=note.id, due_at=now + timedelta(hours=3))
    )
    bot = AsyncMock()
    event_service = EventService(repos["events"])
    settings = _settings(proactive_deadline_warning_hours=4)

    sent = await send_due_nudges(_secretary(repos), event_service, bot, settings, now)

    assert sent == 1
    assert "Task sắp đến hạn" in bot.send_message.await_args.kwargs["text"]
    events = await event_service.list_recent(EventType.NUDGE_SENT, limit=10)
    assert events[0].entity_type == "task_deadline_warning"


async def test_multiple_nudges_are_bundled_limited_and_audited_per_item(repos):
    now = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    note = await repos["notes"].create(
        Note(raw_text="setup", source_chat_id="9001", source_message_id="1")
    )
    for index in range(3):
        await repos["tasks"].create(
            Task(
                title=f"Overdue item {index + 1}",
                source_note_id=note.id,
                due_at=now - timedelta(hours=index + 1),
            )
        )
    bot = AsyncMock()
    event_service = EventService(repos["events"])
    settings = _settings(
        proactive_nudge_bundle_threshold=2,
        proactive_nudge_max_per_run=2,
    )

    first = await send_due_nudges(_secretary(repos), event_service, bot, settings, now)
    second = await send_due_nudges(_secretary(repos), event_service, bot, settings, now)

    assert first == 1
    assert second == 1
    assert bot.send_message.await_count == 2
    first_text = bot.send_message.await_args_list[0].kwargs["text"]
    second_text = bot.send_message.await_args_list[1].kwargs["text"]
    assert first_text.startswith("Nudge digest")
    assert first_text.count("Task quá hạn") == 2
    assert second_text.count("Task quá hạn") == 1
    events = await event_service.list_recent(EventType.NUDGE_SENT, limit=10)
    assert len(events) == 3


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
        _secretary(repos), event_service, bot, settings, now
    )
    second = await send_due_weekly_reviews(
        _secretary(repos), event_service, bot, settings, now
    )

    assert first == 1
    assert second == 0
    assert bot.send_message.await_count == 1
    assert "Weekly review" in bot.send_message.await_args.kwargs["text"]
    assert "Finish V3 plan" in bot.send_message.await_args.kwargs["text"]


async def test_scheduled_backup_runs_once_per_day(repos, tmp_path):
    now = datetime(2026, 7, 15, 3, 35, tzinfo=UTC)
    event_service = EventService(repos["events"])
    settings = _settings(
        database_path=repos["events"].database.db_path,
        backup_time="03:30",
        backup_dir=tmp_path / "backups",
    )

    first = await send_due_backups(event_service, settings, now)
    second = await send_due_backups(event_service, settings, now)
    events = await event_service.list_recent(EventType.BACKUP_CREATED, limit=10)

    assert first == 1
    assert second == 0
    assert len(events) == 1
    assert events[0].payload["verified"] is True


async def test_scheduled_messages_ignore_chat_ids_found_in_notes(repos):
    now = datetime(2026, 6, 8, 8, 5, tzinfo=UTC)
    await repos["notes"].create(
        Note(raw_text="owner note", source_chat_id="9001", source_message_id="1")
    )
    await repos["notes"].create(
        Note(raw_text="untrusted note", source_chat_id="6666", source_message_id="2")
    )
    bot = AsyncMock()
    event_service = EventService(repos["events"])

    sent = await send_due_morning_briefings(
        _secretary(repos),
        event_service,
        bot,
        _settings(morning_briefing_time="08:00"),
        now,
    )

    assert sent == 1
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == 9001


async def test_reminder_delivery_uses_owner_id_not_source_chat_id(repos):
    now = datetime(2026, 6, 8, 9, 0, tzinfo=UTC)
    note = await repos["notes"].create(
        Note(raw_text="untrusted source", source_chat_id="6666", source_message_id="1")
    )
    await repos["reminders"].create(
        Reminder(
            title="Owner-only reminder",
            source_note_id=note.id,
            remind_at=now - timedelta(minutes=1),
            status=ReminderStatus.SCHEDULED,
        )
    )
    bot = AsyncMock()
    service = ReminderService(repos["reminders"], EventService(repos["events"]))

    sent = await send_due_reminders(service, bot, owner_id=9001, now=now)

    assert sent == 1
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == 9001
