from __future__ import annotations

from datetime import UTC, datetime, timedelta

from memocore.domain.models import Commitment, EventType, FollowUp, Note, Task, TaskStatus
from memocore.services.clarification_service import ClarificationService
from memocore.services.daily_closeout_service import DailyCloseoutService
from memocore.services.event_service import EventService
from memocore.services.reminder_service import ReminderService


def _services(repos):
    events = EventService(repos["events"])
    clarification = ClarificationService(
        repos["clarifications"],
        repos["reminders"],
        ReminderService(repos["reminders"], events),
        events,
        task_repo=repos["tasks"],
        followup_repo=repos["followups"],
        commitment_repo=repos["commitments"],
    )
    closeout = DailyCloseoutService(
        repos["tasks"],
        repos["clarifications"],
        events,
        followup_repo=repos["followups"],
        commitment_repo=repos["commitments"],
    )
    return closeout, clarification, events


async def test_daily_closeout_previews_before_writing_and_confirms(repos):
    now = datetime(2026, 7, 15, 20, 0, tzinfo=UTC)
    note = await repos["notes"].create(Note(raw_text="closeout"))
    overdue = await repos["tasks"].create(
        Task(
            title="Finish report",
            source_note_id=note.id,
            due_at=now - timedelta(hours=2),
            priority="high",
        )
    )
    tomorrow = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
    closeout, clarification, events = _services(repos)

    preview = await closeout.preview(source_chat_id="chat-1", now=now)
    unchanged = await repos["tasks"].get_by_id(overdue.id)
    result = await clarification.answer_pending("chat-1", "xác nhận")
    updated = await repos["tasks"].get_by_id(overdue.id)
    applied = await events.list_recent(EventType.DAILY_CLOSEOUT_APPLIED, limit=10)

    assert "Preview: chuyển 1 mục" in preview.summary
    assert unchanged.due_at == overdue.due_at
    assert result.handled is True
    assert "đã chuyển 1 task, 0 follow-up và 0 commitment" in result.message
    assert updated.due_at == tomorrow
    assert applied[0].payload["task_count"] == 1
    assert applied[0].payload["followup_count"] == 0
    assert applied[0].payload["commitment_count"] == 0


async def test_daily_closeout_rolls_followups_and_commitments(repos):
    now = datetime(2026, 7, 15, 20, 0, tzinfo=UTC)
    note = await repos["notes"].create(Note(raw_text="multi artifact closeout"))
    followup = await repos["followups"].create(
        FollowUp(
            title="Ask Alex for status",
            source_note_id=note.id,
            due_at=now - timedelta(days=1),
        )
    )
    commitment = await repos["commitments"].create(
        Commitment(
            title="Send design notes",
            source_note_id=note.id,
            due_at=now,
        )
    )
    closeout, clarification, events = _services(repos)

    preview = await closeout.preview(source_chat_id="chat-1", now=now)
    result = await clarification.answer_pending("chat-1", "xác nhận")
    updated_followup = await repos["followups"].get_by_id(followup.id)
    updated_commitment = await repos["commitments"].get_by_id(commitment.id)
    applied = await events.list_recent(EventType.DAILY_CLOSEOUT_APPLIED, limit=10)

    assert "Preview: chuyển 2 mục" in preview.summary
    assert "Follow-up:" in preview.sections[0].lines
    assert "Commitment:" in preview.sections[0].lines
    assert result.handled is True
    assert "0 task, 1 follow-up và 1 commitment" in result.message
    assert updated_followup.due_at == datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
    assert updated_commitment.due_at == datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
    assert applied[0].payload["followup_count"] == 1
    assert applied[0].payload["commitment_count"] == 1


async def test_daily_closeout_skips_task_changed_after_preview(repos):
    now = datetime(2026, 7, 15, 20, 0, tzinfo=UTC)
    note = await repos["notes"].create(Note(raw_text="stale closeout"))
    task = await repos["tasks"].create(
        Task(title="Move me tomorrow", source_note_id=note.id, due_at=None)
    )
    closeout, clarification, _events = _services(repos)

    await closeout.preview(source_chat_id="chat-1", now=now)
    await repos["tasks"].update_status(task.id, TaskStatus.CANCELLED.value)
    result = await clarification.answer_pending("chat-1", "xác nhận")
    updated = await repos["tasks"].get_by_id(task.id)

    assert "đã chuyển 0 task, 0 follow-up và 0 commitment" in result.message
    assert "Bỏ qua 1 mục" in result.message
    assert updated.status == TaskStatus.CANCELLED or str(updated.status) == "cancelled"


async def test_daily_closeout_cancel_keeps_tasks_unchanged(repos):
    now = datetime(2026, 7, 15, 20, 0, tzinfo=UTC)
    note = await repos["notes"].create(Note(raw_text="cancel closeout"))
    task = await repos["tasks"].create(
        Task(title="Keep unchanged", source_note_id=note.id, due_at=now)
    )
    closeout, clarification, _events = _services(repos)

    await closeout.preview(source_chat_id="chat-1", now=now)
    result = await clarification.answer_pending("chat-1", "không")
    updated = await repos["tasks"].get_by_id(task.id)

    assert result.message == "Dạ, em giữ nguyên các task, follow-up và commitment."
    assert updated.due_at == task.due_at
