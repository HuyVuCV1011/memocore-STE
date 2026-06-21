from datetime import UTC, datetime, timedelta

from memocore.domain.models import FollowUp, Note, Reminder, ReminderStatus, Task
from memocore.domain.schemas import CaptureRequest
from memocore.services.secretary_service import SecretaryService


async def test_capture_is_idempotent_for_source_message(capture_service, repos):
    request = CaptureRequest(
        raw_text="Remind me tomorrow to call Alex",
        source_chat_id="123",
        source_message_id="456",
    )

    first = await capture_service.capture(request)
    second = await capture_service.capture(request)

    assert second.note_id == first.note_id
    assert second.duplicate is True
    assert len(await repos["tasks"].list_by_note(first.note_id)) == 1


async def test_reminder_claim_is_atomic(repos):
    note = await repos["notes"].create(Note(raw_text="remind me"))
    reminder = await repos["reminders"].create(
        Reminder(
            title="Call Alex",
            source_note_id=note.id,
            remind_at=datetime.now(UTC) - timedelta(minutes=1),
            status=ReminderStatus.SCHEDULED,
        )
    )
    now = datetime.now(UTC)

    first = await repos["reminders"].claim_due(now, now - timedelta(minutes=2))
    second = await repos["reminders"].claim_due(now, now - timedelta(minutes=2))

    assert [item.id for item in first] == [reminder.id]
    assert second == []


async def test_reminder_claim_handles_non_utc_offsets(repos):
    note = await repos["notes"].create(Note(raw_text="nhac toi"))
    reminder = await repos["reminders"].create(
        Reminder(
            title="Nhắn tin",
            source_note_id=note.id,
            remind_at=datetime.fromisoformat("2026-06-03T13:00:00+07:00"),
            status=ReminderStatus.SCHEDULED,
        )
    )

    due = await repos["reminders"].claim_due(
        datetime.fromisoformat("2026-06-03T06:01:00+00:00"),
        datetime.fromisoformat("2026-06-03T05:58:00+00:00"),
    )

    assert [item.id for item in due] == [reminder.id]


async def test_secretary_waiting_summary(repos):
    note = await repos["notes"].create(Note(raw_text="waiting"))
    await repos["tasks"].create(
        Task(title="Wait for budget approval", source_note_id=note.id, status="waiting")
    )
    await repos["followups"].create(FollowUp(title="Ask Lan for an update", source_note_id=note.id))
    service = SecretaryService(
        repos["tasks"],
        repos["reminders"],
        repos["followups"],
        repos["projects"],
        repos["memory"],
    )

    summary = await service.waiting()

    assert "Wait for budget approval" in summary
    assert "Ask Lan for an update" in summary


async def test_secretary_lists_tasks_and_reminders(repos):
    note = await repos["notes"].create(Note(raw_text="remind me"))
    await repos["tasks"].create(Task(title="Call Alex", source_note_id=note.id))
    await repos["reminders"].create(
        Reminder(
            title="Ping Alex",
            source_note_id=note.id,
            remind_at=datetime(2026, 6, 3, 6, 0, tzinfo=UTC),
            status=ReminderStatus.SCHEDULED,
        )
    )
    service = SecretaryService(
        repos["tasks"],
        repos["reminders"],
        repos["followups"],
        repos["projects"],
        repos["memory"],
    )

    tasks = await service.tasks()
    reminders = await service.reminders()

    assert "Call Alex" in tasks
    assert "Ping Alex" in reminders
    assert "đã lên lịch" in reminders


async def test_secretary_lists_reminders_in_descending_time_order(repos):
    note = await repos["notes"].create(Note(raw_text="remind me"))
    await repos["reminders"].create(
        Reminder(
            title="Morning reminder",
            source_note_id=note.id,
            remind_at=datetime(2026, 6, 3, 6, 0, tzinfo=UTC),
            status=ReminderStatus.SCHEDULED,
        )
    )
    await repos["reminders"].create(
        Reminder(
            title="Evening reminder",
            source_note_id=note.id,
            remind_at=datetime(2026, 6, 3, 18, 0, tzinfo=UTC),
            status=ReminderStatus.SCHEDULED,
        )
    )
    service = SecretaryService(
        repos["tasks"],
        repos["reminders"],
        repos["followups"],
        repos["projects"],
        repos["memory"],
    )

    reminders = await service.reminders()

    assert reminders.index("Evening reminder") < reminders.index("Morning reminder")


async def test_secretary_today_includes_tasks_due_later_today(repos):
    note = await repos["notes"].create(Note(raw_text="today I need to finish"))
    await repos["tasks"].create(
        Task(
            title="Finish MemoCore",
            source_note_id=note.id,
            due_at=datetime.fromisoformat("2026-06-03T23:59:59+07:00"),
        )
    )
    service = SecretaryService(
        repos["tasks"],
        repos["reminders"],
        repos["followups"],
        repos["projects"],
        repos["memory"],
        display_timezone=datetime.fromisoformat("2026-06-03T00:00:00+07:00").tzinfo,
    )

    summary = await service.today()

    assert "Finish MemoCore" in summary
    assert "Top 3" not in summary
    assert "Score:" not in summary
    assert "Evidence:" not in summary
    assert "tin cậy" not in summary
