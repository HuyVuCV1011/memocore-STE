from datetime import UTC, datetime, timedelta

from memocore.domain.models import Commitment, FollowUp, Note, Reminder, ReminderStatus, Task
from memocore.adapters.telegram.presenter import present_response
from memocore.adapters.telegram.handlers import _agenda_response
from memocore.services.event_service import EventService
from memocore.services.secretary_service import SecretaryService
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
    assert question is not None and question.title == "Xác nhận bỏ"
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


async def test_today_actions_map_to_numbered_actionable_tasks_only(repos):
    now = datetime(2030, 1, 2, 10, 0, tzinfo=UTC)
    note = await repos["notes"].create(Note(raw_text="today action mapping"))
    actionable = []
    for index in range(6):
        actionable.append(
            await repos["tasks"].create(
                Task(
                    title=f"Actionable {index + 1}",
                    due_at=now - timedelta(hours=index + 1),
                    source_note_id=note.id,
                )
            )
        )
    waiting = await repos["tasks"].create(
        Task(
            title="Waiting item",
            status="waiting",
            due_at=now - timedelta(hours=1),
            source_note_id=note.id,
        )
    )
    service = WorkActionService(
        repos["tasks"],
        repos["reminders"],
        EventService(repos["events"]),
    )

    response = await service.agenda_view(
        "Hôm nay - Thứ Tư, 02/01/2030\n\nCần làm\n1. task",
        now.date(),
        title="Hôm nay",
        now=now,
    )

    assert response.title == "Hôm nay - Thứ Tư, 02/01/2030"
    assert response.summary.startswith("Cần làm")
    assert len(response.actions) == 10
    assert {action.label for action in response.actions} == {
        f"{icon} Task {index}"
        for index in range(1, 6)
        for icon in ("✅", "⏰")
    }
    action_ids = {action.action_id for action in response.actions}
    expected_visible = sorted(actionable, key=lambda task: task.due_at)[:5]
    hidden = next(task for task in actionable if task not in expected_visible)
    assert all(task.id in " ".join(action_ids) for task in expected_visible)
    assert hidden.id not in " ".join(action_ids)
    assert waiting.id not in " ".join(action_ids)
    assert not any(":pri:" in action_id for action_id in action_ids)


async def test_agenda_view_preserves_today_and_tomorrow_date_headings(repos):
    now = datetime(2030, 1, 2, 10, 0, tzinfo=UTC)
    service = WorkActionService(
        repos["tasks"],
        repos["reminders"],
        EventService(repos["events"]),
    )

    today = await service.agenda_view(
        "Hôm nay - Thứ Tư, 02/01/2030\n\nCần làm\nKhông có task.",
        now.date(),
        title="Hôm nay",
        now=now,
    )
    tomorrow = await service.agenda_view(
        "Ngày mai - Thứ Năm, 03/01/2030\n\nCần làm\nKhông có task.",
        (now + timedelta(days=1)).date(),
        title="Ngày mai",
        now=now,
    )

    assert today.title == "Hôm nay - Thứ Tư, 02/01/2030"
    assert tomorrow.title == "Ngày mai - Thứ Năm, 03/01/2030"
    assert "Hôm nay" not in today.summary
    assert "Ngày mai" not in tomorrow.summary
    today_text, _ = present_response(today)
    tomorrow_text, _ = present_response(tomorrow)
    assert today_text.count("Hôm nay") == 1
    assert tomorrow_text.count("Ngày mai") == 1
    assert "Thứ Tư, 02/01/2030" in today_text
    assert "Thứ Năm, 03/01/2030" in tomorrow_text


async def test_tomorrow_production_path_only_renders_target_date(repos):
    now = datetime(2030, 1, 2, 10, 0, tzinfo=UTC)
    note = await repos["notes"].create(Note(raw_text="tomorrow production path"))
    overdue = await repos["tasks"].create(
        Task(
            title="Việc quá hạn hôm nay",
            due_at=now - timedelta(hours=1),
            source_note_id=note.id,
        )
    )
    tomorrow_task = await repos["tasks"].create(
        Task(
            title="Việc đúng ngày mai",
            due_at=now + timedelta(hours=23),
            source_note_id=note.id,
        )
    )
    outside_target = await repos["tasks"].create(
        Task(
            title="Việc ngoài ngày mục tiêu",
            due_at=now + timedelta(days=3),
            source_note_id=note.id,
        )
    )
    secretary = SecretaryService(
        repos["tasks"],
        repos["reminders"],
        repos["followups"],
        repos["projects"],
        repos["memory"],
        meeting_repo=repos["meetings"],
        activity_link_repo=repos["activity_links"],
    )
    work_actions = WorkActionService(
        repos["tasks"],
        repos["reminders"],
        EventService(repos["events"]),
    )

    response, _ = await _agenda_response(
        secretary,
        work_actions,
        "tomorrow",
        now=now,
    )
    text, _ = present_response(response)

    assert text.count("Ngày mai") == 1
    assert "Thứ 5, 03/01/2030" in text
    assert tomorrow_task.title in text
    assert overdue.title not in text
    assert outside_target.title not in text
    assert "Mốc tiếp theo" not in text
    assert all(task.id not in text for task in (overdue, tomorrow_task, outside_target))
    assert all(outside_target.id not in action.action_id for action in response.actions)


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


async def test_waiting_view_can_close_followup_with_undo(repos):
    note = await repos["notes"].create(Note(raw_text="follow-up work view"))
    followup = await repos["followups"].create(
        FollowUp(
            title="Ask Alex for BI file",
            due_at=datetime(2026, 7, 15, 9, 0, tzinfo=UTC),
            source_note_id=note.id,
        )
    )
    service = WorkActionService(
        repos["tasks"],
        repos["reminders"],
        EventService(repos["events"]),
        followup_repo=repos["followups"],
        commitment_repo=repos["commitments"],
    )

    view = await service.waiting_view()
    done_action = next(action.action_id for action in view.actions if action.action_id.startswith("work:q:f:done:"))
    confirmation = await service.handle(done_action)
    result = await service.handle(confirmation.actions[0].action_id)
    updated = await repos["followups"].get_by_id(followup.id)
    undone = await service.handle(result.actions[0].action_id)
    restored = await repos["followups"].get_by_id(followup.id)

    assert "Ask Alex for BI file" in "\n".join(view.sections[0].lines)
    assert confirmation is not None and confirmation.title == "Xác nhận hoàn thành"
    assert result is not None and result.title == "Đã cập nhật"
    assert updated is not None and str(updated.status) == "done"
    assert undone is not None and undone.title == "Đã hoàn tác"
    assert restored is not None and str(restored.status) == "open"
    assert restored.due_at == followup.due_at


async def test_commitments_view_can_reschedule_and_cancel(repos):
    note = await repos["notes"].create(Note(raw_text="commitment work view"))
    commitment = await repos["commitments"].create(
        Commitment(
            title="Send design notes",
            due_at=datetime(2026, 7, 15, 9, 0, tzinfo=UTC),
            source_note_id=note.id,
        )
    )
    service = WorkActionService(
        repos["tasks"],
        repos["reminders"],
        EventService(repos["events"]),
        followup_repo=repos["followups"],
        commitment_repo=repos["commitments"],
    )

    view = await service.commitments_view()
    due_action = next(action.action_id for action in view.actions if action.action_id.startswith("work:q:c:due:"))
    due_choice = await service.handle(due_action)
    due_confirmation = await service.handle(
        next(action.action_id for action in due_choice.actions if ":tomorrow:" in action.action_id)
    )
    due_result = await service.handle(due_confirmation.actions[0].action_id)
    changed = await repos["commitments"].get_by_id(commitment.id)
    cancel_question = await service.handle(f"work:q:c:cancel:{commitment.id}")
    cancel_result = await service.handle(cancel_question.actions[0].action_id)
    cancelled = await repos["commitments"].get_by_id(commitment.id)

    assert "Send design notes" in "\n".join(view.sections[0].lines)
    assert due_result is not None and due_result.title == "Đã cập nhật"
    assert changed is not None and changed.due_at != commitment.due_at
    assert cancel_result is not None and cancel_result.title == "Đã cập nhật"
    assert cancelled is not None and str(cancelled.status) == "cancelled"
