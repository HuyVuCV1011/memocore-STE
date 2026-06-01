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


async def test_secretary_waiting_summary(repos):
    note = await repos["notes"].create(Note(raw_text="waiting"))
    await repos["tasks"].create(
        Task(title="Wait for budget approval", source_note_id=note.id, status="waiting")
    )
    await repos["followups"].create(FollowUp(title="Ask Lan for an update", source_note_id=note.id))
    service = SecretaryService(
        repos["tasks"], repos["followups"], repos["projects"], repos["memory"]
    )

    summary = await service.waiting()

    assert "Wait for budget approval" in summary
    assert "Ask Lan for an update" in summary
