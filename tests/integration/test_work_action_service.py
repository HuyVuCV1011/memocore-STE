from datetime import UTC, datetime

from memocore.domain.models import Note, Reminder, ReminderStatus, Task
from memocore.services.event_service import EventService
from memocore.services.work_action_service import WorkActionService


async def test_task_action_requires_confirmation_and_supports_undo(repos):
    note = await repos["notes"].create(Note(raw_text="task source"))
    task = await repos["tasks"].create(
        Task(
            title="Hoàn thành proposal",
            priority="medium",
            due_at=datetime(2026, 6, 12, 9, 0, tzinfo=UTC),
            source_note_id=note.id,
        )
    )
    service = WorkActionService(
        repos["tasks"],
        repos["reminders"],
        EventService(repos["events"]),
    )

    confirmation = await service.handle(f"work:q:t:done:{task.id}")
    unchanged = await repos["tasks"].get_by_id(task.id)

    assert confirmation is not None
    assert confirmation.title == "Xác nhận hoàn thành"
    assert unchanged is not None and str(unchanged.status) == "candidate"

    result = await service.handle(f"work:x:t:done:{task.id}")
    changed = await repos["tasks"].get_by_id(task.id)
    undo_action = result.actions[0].action_id

    assert changed is not None and str(changed.status) == "done"
    assert undo_action.startswith("work:u:e:")

    undone = await service.handle(undo_action)
    restored = await repos["tasks"].get_by_id(task.id)

    assert undone is not None and undone.title == "Đã hoàn tác"
    assert restored is not None and str(restored.status) == "candidate"
    assert restored.priority == "medium"
    assert restored.due_at == task.due_at


async def test_work_action_can_cancel_task(repos):
    note = await repos["notes"].create(Note(raw_text="task source"))
    task = await repos["tasks"].create(Task(title="Task cần bỏ", source_note_id=note.id))
    service = WorkActionService(
        repos["tasks"],
        repos["reminders"],
        EventService(repos["events"]),
    )

    question = await service.handle(f"work:q:t:cancel:{task.id}")
    result = await service.handle(f"work:x:t:cancel:{task.id}")

    updated = await repos["tasks"].get_by_id(task.id)
    assert question is not None and question.title == "Xác nhận bỏ task"
    assert result is not None
    assert updated is not None and str(updated.status) == "cancelled"


async def test_tasks_view_shows_daily_recurrence(repos):
    note = await repos["notes"].create(Note(raw_text="daily view"))
    task = await repos["tasks"].create(
        Task(
            title="Daily task",
            source_note_id=note.id,
            recurrence_rule="daily",
        )
    )
    service = WorkActionService(
        repos["tasks"],
        repos["reminders"],
        EventService(repos["events"]),
    )

    response = await service.tasks_view()

    assert task.title in response.sections[0].lines[0]
    assert "🔁 Hằng ngày" in response.sections[0].lines[0]


async def test_reminder_reschedule_records_diff_and_undo(repos):
    note = await repos["notes"].create(Note(raw_text="reminder source"))
    reminder = await repos["reminders"].create(
        Reminder(
            title="Họp STE",
            remind_at=datetime(2026, 6, 12, 2, 0, tzinfo=UTC),
            status=ReminderStatus.SCHEDULED,
            source_note_id=note.id,
        )
    )
    service = WorkActionService(
        repos["tasks"],
        repos["reminders"],
        EventService(repos["events"]),
    )

    result = await service.handle(f"work:x:r:due:week:{reminder.id}")
    changed = await repos["reminders"].get_by_id(reminder.id)

    assert result is not None
    assert "remind_at:" in result.summary
    assert changed is not None and changed.remind_at != reminder.remind_at

    await service.handle(result.actions[0].action_id)
    restored = await repos["reminders"].get_by_id(reminder.id)
    assert restored is not None and restored.remind_at == reminder.remind_at
