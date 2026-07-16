from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from memocore.domain.models import (
    ClarificationRequest,
    EventType,
    Note,
    Task,
    TaskStatus,
)
from memocore.domain.schemas import CaptureRequest
from memocore.services.conversation_service import ConversationService
from memocore.services.secretary_service import SecretaryService
from memocore.services.task_reference_resolver import TaskReferenceResolver
from memocore.services.work_action_service import WorkActionService


def _service(capture_service, repos, *, now=None) -> ConversationService:
    secretary = SecretaryService(
        repos["tasks"],
        repos["reminders"],
        repos["followups"],
        repos["projects"],
        repos["memory"],
        display_timezone=ZoneInfo("Asia/Ho_Chi_Minh"),
        person_repo=repos["people"],
    )
    service = ConversationService(
        capture_service,
        secretary,
        repos["notes"],
        repos["tasks"],
        capture_service.memory_service,
        capture_service.event_service,
    )
    if now is not None:
        service.now_provider = lambda: now
        service.task_reference_resolver = TaskReferenceResolver(
            repos["tasks"],
            service.task_list_context_repo,
            display_timezone=secretary.display_timezone,
            now_provider=lambda: now,
        )
    return service


async def _two_tasks(repos):
    note = await repos["notes"].create(Note(raw_text="task source"))
    first = await repos["tasks"].create(
        Task(
            title="Task đầu",
            due_at=datetime(2026, 6, 21, 9, 0, tzinfo=UTC),
            source_note_id=note.id,
        )
    )
    second = await repos["tasks"].create(
        Task(
            title="Tạo kịch bản audio sảng văn",
            due_at=datetime(2026, 6, 21, 10, 0, tzinfo=UTC),
            source_note_id=note.id,
        )
    )
    return first, second


async def test_due_update_resolves_task_2_from_briefing(capture_service, repos):
    _, second = await _two_tasks(repos)
    service = _service(capture_service, repos)
    briefing = await service.secretary_service.daily_briefing(
        datetime(2026, 6, 21, 1, 0, tzinfo=UTC)
    )
    await service.remember_task_list("chat-1", briefing, "briefing")

    result = await service.handle_text(
        CaptureRequest(
            raw_text="đổi task 2 thành 23:59",
            source_chat_id="chat-1",
            source_message_id="due-1",
        )
    )

    updated = await repos["tasks"].get_by_id(second.id)
    assert result.intent == "update_task_due"
    assert updated is not None
    assert updated.due_at.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%H:%M") == "23:59"


async def test_daily_wording_creates_scope_clarification_and_one_time_choice(
    capture_service, repos
):
    _, second = await _two_tasks(repos)
    service = _service(capture_service, repos)
    await service.remember_task_list(
        "chat-1",
        "Nên làm tiếp\n1. Task đầu — hạn hôm nay\n2. Tạo kịch bản audio sảng văn — hạn hôm nay",
        "briefing",
    )

    result = await service.handle_text(
        CaptureRequest(
            raw_text="đổi task 2 thành 23:59 hàng ngày",
            source_chat_id="chat-1",
            source_message_id="scope-1",
        )
    )
    pending = await repos["clarifications"].find_pending_for_chat("chat-1")

    assert result.reply == "Anh muốn áp dụng thế nào cho ‘Tạo kịch bản audio sảng văn’?"
    assert result.reply_markup is not None
    assert pending is not None and pending.entity_type == "task_recurrence_scope"

    answer = await capture_service.clarification_service.answer_pending(
        "chat-1", "Chỉ lần này"
    )
    updated = await repos["tasks"].get_by_id(second.id)

    assert answer.handled is True
    assert updated is not None and updated.recurrence_rule is None
    assert updated.due_at.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%H:%M") == "23:59"


async def test_daily_scope_choice_sets_recurrence(capture_service, repos):
    _, second = await _two_tasks(repos)
    service = _service(capture_service, repos)
    await service.remember_task_list(
        "chat-1",
        "1. Task đầu — hạn hôm nay\n2. Tạo kịch bản audio sảng văn — hạn hôm nay",
        "tasks",
    )
    await service.handle_text(
        CaptureRequest(
            raw_text="đổi việc 2 thành 23:59 hằng ngày",
            source_chat_id="chat-1",
        )
    )

    await capture_service.clarification_service.answer_pending(
        "chat-1", "Lặp hằng ngày"
    )
    updated = await repos["tasks"].get_by_id(second.id)

    assert updated is not None and updated.recurrence_rule == "daily"
    assert updated.recurrence_series_id == second.id


async def test_existing_recurring_task_updates_current_and_future_scope(
    capture_service, repos
):
    note = await repos["notes"].create(Note(raw_text="existing recurring"))
    task = await repos["tasks"].create(
        Task(
            title="Recurring task",
            status=TaskStatus.OPEN,
            due_at=datetime(2026, 6, 21, 3, 0, tzinfo=UTC),
            source_note_id=note.id,
            recurrence_rule="daily",
            recurrence_series_id="series-existing",
            recurrence_occurrence_at=datetime(2026, 6, 21, 3, 0, tzinfo=UTC),
        )
    )
    await repos["clarifications"].create(
        ClarificationRequest(
            source_chat_id="chat-1",
            entity_type="task_recurrence_scope",
            entity_id=task.id,
            field_name="due_at|2026-06-21T16:59:00+00:00|daily",
            question="Anh muốn áp dụng thế nào?",
        )
    )

    result = await capture_service.clarification_service.answer_pending(
        "chat-1", "Kỳ này và các kỳ sau"
    )
    updated = await repos["tasks"].get_by_id(task.id)

    assert result.handled is True
    assert updated is not None
    assert updated.due_at == datetime(2026, 6, 21, 16, 59, tzinfo=UTC)
    assert updated.recurrence_rule == "daily"
    assert updated.recurrence_occurrence_at == updated.due_at


async def test_due_update_of_existing_recurring_task_always_asks_scope(
    capture_service, repos
):
    note = await repos["notes"].create(Note(raw_text="existing daily"))
    task = await repos["tasks"].create(
        Task(
            title="Tạo kịch bản audio sảng văn",
            status=TaskStatus.OPEN,
            due_at=datetime(2026, 6, 21, 17, 0, tzinfo=UTC),
            source_note_id=note.id,
            recurrence_rule="daily",
            recurrence_series_id="daily-audio",
            recurrence_occurrence_at=datetime(2026, 6, 21, 17, 0, tzinfo=UTC),
        )
    )
    service = _service(capture_service, repos)

    result = await service.handle_text(
        CaptureRequest(
            raw_text="đổi giờ tạo kịch bản audio sang 23h00 cho tôi",
            source_chat_id="chat-1",
        )
    )
    pending = await repos["clarifications"].find_pending_for_chat("chat-1")
    unchanged = await repos["tasks"].get_by_id(task.id)

    assert result.reply == "Anh muốn áp dụng thế nào cho ‘Tạo kịch bản audio sảng văn’?"
    assert result.reply_markup is not None
    assert pending is not None and pending.entity_type == "task_recurrence_scope"
    assert pending.field_name.endswith("|daily")
    assert unchanged is not None
    assert unchanged.due_at == datetime(2026, 6, 21, 17, 0, tzinfo=UTC)


async def test_current_only_due_change_keeps_future_recurrence_anchor(
    capture_service, repos
):
    note = await repos["notes"].create(Note(raw_text="daily anchor"))
    original_occurrence = datetime(2026, 6, 21, 17, 0, tzinfo=UTC)
    task = await repos["tasks"].create(
        Task(
            title="Daily anchored task",
            status=TaskStatus.OPEN,
            due_at=original_occurrence,
            source_note_id=note.id,
            recurrence_rule="daily",
            recurrence_series_id="daily-anchor",
            recurrence_occurrence_at=original_occurrence,
        )
    )
    await repos["clarifications"].create(
        ClarificationRequest(
            source_chat_id="chat-1",
            entity_type="task_recurrence_scope",
            entity_id=task.id,
            field_name="due_at|2026-06-21T16:00:00+00:00|daily",
            question="Anh muốn áp dụng thế nào?",
        )
    )

    await capture_service.clarification_service.answer_pending(
        "chat-1", "Chỉ kỳ này"
    )
    updated = await repos["tasks"].get_by_id(task.id)
    _, next_task, created = await repos["tasks"].complete_and_schedule_next(task.id)

    assert updated is not None
    assert updated.due_at == datetime(2026, 6, 21, 16, 0, tzinfo=UTC)
    assert updated.recurrence_occurrence_at == original_occurrence
    assert created is True
    assert next_task is not None
    assert next_task.due_at == original_occurrence + timedelta(days=1)


async def test_done_confirmation_creates_next_recurring_occurrence(
    capture_service, repos
):
    note = await repos["notes"].create(Note(raw_text="confirmed daily"))
    due_at = datetime(2026, 6, 21, 17, 0, tzinfo=UTC)
    task = await repos["tasks"].create(
        Task(
            title="Tạo kịch bản audio sảng văn",
            status=TaskStatus.OPEN,
            due_at=due_at,
            source_note_id=note.id,
            recurrence_rule="daily",
            recurrence_series_id="confirmed-daily",
            recurrence_occurrence_at=due_at,
        )
    )
    await repos["clarifications"].create(
        ClarificationRequest(
            source_chat_id="chat-1",
            entity_type="task",
            entity_id=task.id,
            field_name="status|done",
            question="Anh muốn đánh dấu xong task này phải không?",
        )
    )

    result = await capture_service.clarification_service.answer_pending(
        "chat-1", "đúng"
    )
    active = await repos["tasks"].list_active()

    assert "tạo kỳ kế tiếp" in result.message
    assert len(active) == 1
    assert active[0].title == task.title
    assert active[0].due_at == due_at + timedelta(days=1)
    assert active[0].recurrence_rule == "daily"


async def test_direct_completion_formats_next_occurrence_in_user_timezone(
    capture_service, repos
):
    note = await repos["notes"].create(Note(raw_text="daily local time"))
    await repos["tasks"].create(
        Task(
            title="Tạo kịch bản audio sảng văn",
            status=TaskStatus.OPEN,
            due_at=datetime(2026, 6, 22, 16, 59, tzinfo=UTC),
            source_note_id=note.id,
            recurrence_rule="daily",
            recurrence_series_id="daily-local-time",
            recurrence_occurrence_at=datetime(2026, 6, 22, 16, 59, tzinfo=UTC),
        )
    )
    service = _service(capture_service, repos)

    result = await service.handle_text(
        CaptureRequest(raw_text="đã xong kịch bản sảng văn hôm nay")
    )

    assert result.intent == "mark_task_done"
    assert "23:59 23/06/2026" in result.reply
    assert "16:59" not in result.reply


async def test_completes_explicit_count_from_recent_task_list(capture_service, repos):
    note = await repos["notes"].create(Note(raw_text="three visible tasks"))
    tasks = [
        await repos["tasks"].create(Task(title=title, source_note_id=note.id))
        for title in (
            "Tạo kịch bản audio sảng văn",
            "Tập gym",
            "Làm giám khảo lớp APM10",
        )
    ]
    untouched = await repos["tasks"].create(
        Task(title="Task không nằm trong danh sách", source_note_id=note.id)
    )
    service = _service(capture_service, repos)
    await service.task_list_context_repo.save(
        "chat-1", [task.id for task in tasks], "today"
    )

    result = await service.handle_text(
        CaptureRequest(
            raw_text="tôi đã xong 3 task đó",
            source_chat_id="chat-1",
            source_message_id="done-three",
        )
    )

    assert result.intent == "mark_task_done"
    assert "Đã đánh dấu xong 3 task" in result.reply
    updated_tasks = [await repos["tasks"].get_by_id(task.id) for task in tasks]
    assert all(str(task.status) == "done" for task in updated_tasks)
    assert str((await repos["tasks"].get_by_id(untouched.id)).status) != "done"


async def test_completes_today_scope_including_overdue_tasks(capture_service, repos):
    now = datetime(2026, 6, 26, 18, 12, tzinfo=UTC)  # 01:12 on 27/06 locally
    note = await repos["notes"].create(Note(raw_text="today scope"))
    included = [
        await repos["tasks"].create(
            Task(
                title="Overdue daily",
                due_at=datetime(2026, 6, 25, 16, 59, tzinfo=UTC),
                source_note_id=note.id,
            )
        ),
        await repos["tasks"].create(
            Task(
                title="Due today",
                due_at=datetime(2026, 6, 27, 10, 0, tzinfo=UTC),
                source_note_id=note.id,
            )
        ),
    ]
    tomorrow = await repos["tasks"].create(
        Task(
            title="Due tomorrow",
            due_at=datetime(2026, 6, 28, 3, 0, tzinfo=UTC),
            source_note_id=note.id,
        )
    )
    service = _service(capture_service, repos, now=now)

    result = await service.handle_text(
        CaptureRequest(
            raw_text="đã xong hết task hôm nay",
            source_chat_id="chat-1",
            source_message_id="done-today",
        )
    )

    assert result.intent == "mark_task_done"
    assert "xác nhận đánh dấu xong 2 task" in result.reply
    answer = await capture_service.clarification_service.answer_pending(
        "chat-1", "xác nhận"
    )
    assert "Đã đánh dấu xong 2 task" in answer.message
    updated_tasks = [await repos["tasks"].get_by_id(task.id) for task in included]
    assert all(str(task.status) == "done" for task in updated_tasks)
    assert str((await repos["tasks"].get_by_id(tomorrow.id)).status) != "done"


async def test_vague_bulk_completion_requires_confirmation(capture_service, repos):
    first, second = await _two_tasks(repos)
    service = _service(capture_service, repos)
    await service.task_list_context_repo.save(
        "chat-1", [first.id, second.id], "tasks"
    )

    result = await service.handle_text(
        CaptureRequest(
            raw_text="xong hết task",
            source_chat_id="chat-1",
            source_message_id="confirm-bulk",
        )
    )
    pending = await repos["clarifications"].find_pending_for_chat("chat-1")

    assert "xác nhận đánh dấu xong 2 task" in result.reply
    assert pending is not None
    assert pending.entity_type == "task_bulk_done"
    assert str((await repos["tasks"].get_by_id(first.id)).status) != "done"
    assert str((await repos["tasks"].get_by_id(second.id)).status) != "done"


async def test_bulk_completion_confirmation_completes_resolved_set(
    capture_service, repos
):
    first, second = await _two_tasks(repos)
    service = _service(capture_service, repos)
    await service.task_list_context_repo.save(
        "chat-1", [first.id, second.id], "tasks"
    )
    await service.handle_text(
        CaptureRequest(raw_text="xong hết task", source_chat_id="chat-1")
    )

    answer = await capture_service.clarification_service.answer_pending(
        "chat-1", "xác nhận"
    )

    assert answer.handled is True
    assert "2 task" in answer.message
    assert str((await repos["tasks"].get_by_id(first.id)).status) == "done"
    assert str((await repos["tasks"].get_by_id(second.id)).status) == "done"


async def test_completion_warns_when_next_occurrence_is_already_overdue(
    capture_service, repos
):
    now = datetime(2026, 6, 26, 18, 12, tzinfo=UTC)  # 01:12 on 27/06 locally
    note = await repos["notes"].create(Note(raw_text="overdue recurrence"))
    await repos["tasks"].create(
        Task(
            title="Tạo kịch bản audio sảng văn",
            due_at=datetime(2026, 6, 25, 16, 59, tzinfo=UTC),
            source_note_id=note.id,
            recurrence_rule="daily",
            recurrence_series_id="overdue-daily",
            recurrence_occurrence_at=datetime(2026, 6, 25, 16, 59, tzinfo=UTC),
        )
    )
    service = _service(capture_service, repos, now=now)

    result = await service.handle_text(
        CaptureRequest(raw_text="đã xong kịch bản audio sảng văn")
    )

    assert "23:59 26/06/2026" in result.reply
    assert "hiện cũng đã quá hạn" in result.reply


async def test_today_scope_prefers_tasks_from_recent_today_view(
    capture_service, repos
):
    now = datetime(2026, 6, 26, 18, 12, tzinfo=UTC)
    note = await repos["notes"].create(Note(raw_text="bounded today view"))
    visible = await repos["tasks"].create(
        Task(
            title="Visible overdue",
            due_at=datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
            source_note_id=note.id,
        )
    )
    hidden = await repos["tasks"].create(
        Task(
            title="Hidden overdue",
            due_at=datetime(2026, 6, 25, 11, 0, tzinfo=UTC),
            source_note_id=note.id,
        )
    )
    service = _service(capture_service, repos, now=now)
    await service.task_list_context_repo.save("chat-1", [visible.id], "today")

    result = await service.handle_text(
        CaptureRequest(
            raw_text="đã xong hết task hôm nay",
            source_chat_id="chat-1",
        )
    )

    assert "Đã đánh dấu xong: Visible overdue" in result.reply
    assert str((await repos["tasks"].get_by_id(visible.id)).status) == "done"
    assert str((await repos["tasks"].get_by_id(hidden.id)).status) != "done"


async def test_expired_task_list_context_is_not_used_for_deictic_count(
    capture_service, repos
):
    now = datetime(2026, 6, 27, 2, 0, tzinfo=UTC)
    first, second = await _two_tasks(repos)
    service = _service(capture_service, repos, now=now)
    await service.task_list_context_repo.save(
        "chat-1",
        [first.id, second.id],
        "tasks",
        now=now - timedelta(hours=7),
    )

    result = await service.handle_text(
        CaptureRequest(
            raw_text="tôi đã xong 2 task đó",
            source_chat_id="chat-1",
        )
    )

    assert "chưa tìm thấy task khớp" in result.reply
    assert str((await repos["tasks"].get_by_id(first.id)).status) != "done"
    assert str((await repos["tasks"].get_by_id(second.id)).status) != "done"


async def test_large_today_scope_requires_confirmation(capture_service, repos):
    now = datetime(2026, 6, 26, 18, 12, tzinfo=UTC)
    note = await repos["notes"].create(Note(raw_text="large today scope"))
    tasks = [
        await repos["tasks"].create(
            Task(
                title=f"Today task {index}",
                due_at=datetime(2026, 6, 27, 8, index, tzinfo=UTC),
                source_note_id=note.id,
            )
        )
        for index in range(6)
    ]
    service = _service(capture_service, repos, now=now)

    result = await service.handle_text(
        CaptureRequest(
            raw_text="đã xong hết task hôm nay",
            source_chat_id="chat-1",
        )
    )

    assert "xác nhận đánh dấu xong 6 task" in result.reply
    unchanged = [await repos["tasks"].get_by_id(task.id) for task in tasks]
    assert all(str(task.status) != "done" for task in unchanged)


async def test_expired_bulk_confirmation_does_not_mutate_tasks(
    capture_service, repos
):
    now = datetime(2026, 6, 27, 2, 0, tzinfo=UTC)
    first, second = await _two_tasks(repos)
    await repos["clarifications"].create(
        ClarificationRequest(
            source_chat_id="chat-1",
            entity_type="task_bulk_done",
            entity_id=f"{first.id},{second.id}",
            field_name="status|done",
            question="Xác nhận?",
            created_at=now - timedelta(minutes=16),
            updated_at=now - timedelta(minutes=16),
        )
    )
    capture_service.clarification_service.now_provider = lambda: now

    answer = await capture_service.clarification_service.answer_pending(
        "chat-1", "xác nhận"
    )

    assert "đã hết hạn" in answer.message
    assert str((await repos["tasks"].get_by_id(first.id)).status) != "done"
    assert str((await repos["tasks"].get_by_id(second.id)).status) != "done"


async def test_bulk_confirmation_skips_tasks_that_are_no_longer_open(
    capture_service, repos
):
    first, second = await _two_tasks(repos)
    service = _service(capture_service, repos)
    await service.task_list_context_repo.save(
        "chat-1", [first.id, second.id], "tasks"
    )
    await service.handle_text(
        CaptureRequest(raw_text="xong hết task", source_chat_id="chat-1")
    )
    await repos["tasks"].update_status(second.id, TaskStatus.CANCELLED.value)

    answer = await capture_service.clarification_service.answer_pending(
        "chat-1", "xác nhận"
    )

    assert "Đã đánh dấu xong 1 task" in answer.message
    assert "Bỏ qua 1 task" in answer.message
    assert str((await repos["tasks"].get_by_id(first.id)).status) == "done"
    assert str((await repos["tasks"].get_by_id(second.id)).status) == "cancelled"


async def test_turn_clock_is_shared_by_today_query_and_due_update(
    capture_service, repos
):
    now = datetime(2026, 6, 26, 18, 12, tzinfo=UTC)  # 27/06 locally
    note = await repos["notes"].create(Note(raw_text="turn clock"))
    task = await repos["tasks"].create(
        Task(
            title="Clock task",
            due_at=datetime(2026, 6, 27, 8, 0, tzinfo=UTC),
            source_note_id=note.id,
        )
    )
    service = _service(capture_service, repos, now=now)
    await service.task_list_context_repo.save("chat-1", [task.id], "today")

    today = await service.handle_text(
        CaptureRequest(raw_text="hôm nay tôi cần làm gì", source_chat_id="chat-1")
    )
    updated = await service.handle_text(
        CaptureRequest(
            raw_text="đổi task 1 thành hôm nay 19h",
            source_chat_id="chat-1",
        )
    )
    changed = await repos["tasks"].get_by_id(task.id)

    assert "27/06/2026" in today.reply
    assert updated.intent == "update_task_due"
    assert changed is not None
    assert changed.due_at.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).strftime(
        "%H:%M %d/%m/%Y"
    ) == "19:00 27/06/2026"


async def test_recurrence_update_resolves_task_by_title(capture_service, repos):
    note = await repos["notes"].create(Note(raw_text="recurrence by title"))
    task = await repos["tasks"].create(
        Task(title="Tạo kịch bản audio sảng văn", source_note_id=note.id)
    )
    service = _service(capture_service, repos)

    result = await service.handle_text(
        CaptureRequest(raw_text="cho kịch bản audio sảng văn lặp hằng tuần")
    )
    updated = await repos["tasks"].get_by_id(task.id)

    assert result.intent == "update_task_recurrence"
    assert updated is not None and updated.recurrence_rule == "weekly"


async def test_task_resolution_emits_privacy_safe_metric(capture_service, repos):
    note = await repos["notes"].create(Note(raw_text="resolution metric"))
    task = await repos["tasks"].create(
        Task(title="Secret client launch", source_note_id=note.id)
    )
    service = _service(capture_service, repos)

    await service.handle_text(
        CaptureRequest(raw_text="đã xong secret client launch")
    )
    events = await service.event_service.list_recent(
        EventType.TASK_REFERENCE_RESOLVED
    )

    assert events
    payload = events[0].payload
    assert payload["source"] == "title_match"
    assert payload["resolution_reason"] == "unique_title_match"
    assert payload["candidate_count"] == 1
    assert "raw_text" not in payload
    assert "title" not in payload
    assert task.title not in str(payload)


async def test_recurring_backlog_can_skip_to_first_future_occurrence(
    capture_service, repos
):
    now = datetime(2026, 6, 26, 18, 12, tzinfo=UTC)
    note = await repos["notes"].create(Note(raw_text="backlog policy"))
    await repos["tasks"].create(
        Task(
            title="Tạo kịch bản audio sảng văn",
            due_at=datetime(2026, 6, 25, 16, 59, tzinfo=UTC),
            source_note_id=note.id,
            recurrence_rule="daily",
            recurrence_series_id="backlog-policy",
            recurrence_occurrence_at=datetime(2026, 6, 25, 16, 59, tzinfo=UTC),
        )
    )
    service = _service(capture_service, repos, now=now)
    capture_service.clarification_service.now_provider = lambda: now

    result = await service.handle_text(
        CaptureRequest(
            raw_text="đã xong kịch bản audio sảng văn hôm nay",
            source_chat_id="chat-1",
        )
    )
    pending = await repos["clarifications"].find_pending_for_chat("chat-1")

    assert "1 kỳ định kỳ đã lỡ" in result.reply
    assert pending is not None
    assert pending.entity_type == "recurrence_backlog_policy"

    answer = await capture_service.clarification_service.answer_pending(
        "chat-1", "2"
    )
    active = await repos["tasks"].list_active()

    assert "Đã bỏ qua backlog cho 1 task" in answer.message
    assert len(active) == 1
    assert active[0].due_at == datetime(2026, 6, 27, 16, 59, tzinfo=UTC)
    assert active[0].recurrence_occurrence_at == active[0].due_at


async def test_recurring_backlog_can_keep_each_missed_occurrence(
    capture_service, repos
):
    now = datetime(2026, 6, 26, 18, 12, tzinfo=UTC)
    note = await repos["notes"].create(Note(raw_text="keep backlog"))
    await repos["tasks"].create(
        Task(
            title="Tập gym",
            due_at=datetime(2026, 6, 25, 0, 0, tzinfo=UTC),
            source_note_id=note.id,
            recurrence_rule="daily",
            recurrence_series_id="keep-backlog",
            recurrence_occurrence_at=datetime(2026, 6, 25, 0, 0, tzinfo=UTC),
        )
    )
    service = _service(capture_service, repos, now=now)
    capture_service.clarification_service.now_provider = lambda: now
    await service.handle_text(
        CaptureRequest(raw_text="đã xong tập gym", source_chat_id="chat-1")
    )

    answer = await capture_service.clarification_service.answer_pending(
        "chat-1", "1"
    )
    active = await repos["tasks"].list_active()

    assert "giữ nguyên từng kỳ" in answer.message
    assert len(active) == 1
    assert active[0].due_at == datetime(2026, 6, 26, 0, 0, tzinfo=UTC)


async def test_batch_preview_allows_reselecting_tasks(capture_service, repos):
    first, second = await _two_tasks(repos)
    service = _service(capture_service, repos)
    await service.task_list_context_repo.save(
        "chat-1", [first.id, second.id], "tasks"
    )

    preview = await service.handle_text(
        CaptureRequest(raw_text="xong hết task", source_chat_id="chat-1")
    )
    edit = await capture_service.clarification_service.answer_pending(
        "chat-1", "chọn lại"
    )
    toggled = await capture_service.clarification_service.answer_pending(
        "chat-1", "2"
    )
    completed = await capture_service.clarification_service.answer_pending(
        "chat-1", "xác nhận"
    )

    assert "Sẽ hoàn thành 2 task" in preview.reply
    assert "☑ 1." in edit.message
    assert "☐ 2." in toggled.message
    assert "Đã đánh dấu xong 1 task" in completed.message
    assert str((await repos["tasks"].get_by_id(first.id)).status) == "done"
    assert str((await repos["tasks"].get_by_id(second.id)).status) != "done"


async def test_batch_completion_can_be_undone_safely(capture_service, repos):
    first, second = await _two_tasks(repos)
    service = _service(capture_service, repos)
    await service.task_list_context_repo.save(
        "chat-1", [first.id, second.id], "tasks"
    )
    await service.handle_text(
        CaptureRequest(raw_text="xong hết task", source_chat_id="chat-1")
    )
    completed = await capture_service.clarification_service.answer_pending(
        "chat-1", "xác nhận"
    )
    undo_button = completed.reply_markup.inline_keyboard[-1][0]
    work_actions = WorkActionService(
        repos["tasks"],
        repos["reminders"],
        service.event_service,
        task_operation_service=service.task_operation_service,
    )

    undone = await work_actions.handle(undo_button.callback_data)

    assert undone is not None
    assert undone.title == "Đã hoàn tác batch"
    assert str((await repos["tasks"].get_by_id(first.id)).status) != "done"
    assert str((await repos["tasks"].get_by_id(second.id)).status) != "done"


async def test_batch_undo_skips_task_changed_after_completion(
    capture_service, repos
):
    first, second = await _two_tasks(repos)
    service = _service(capture_service, repos)
    batch = await service.task_operation_service.complete_many(
        [first.id, second.id],
        transition="test_batch",
    )
    await repos["tasks"].update_priority(second.id, "high")
    work_actions = WorkActionService(
        repos["tasks"],
        repos["reminders"],
        service.event_service,
        task_operation_service=service.task_operation_service,
    )

    undone = await work_actions.handle(f"work:u:e:{batch.batch_event_id}")

    assert undone is not None
    assert "khôi phục 1 task" in undone.summary
    assert "Bỏ qua 1 task" in undone.summary
    assert str((await repos["tasks"].get_by_id(first.id)).status) != "done"
    assert str((await repos["tasks"].get_by_id(second.id)).status) == "done"


async def test_batch_undo_removes_created_recurrence_occurrence(
    capture_service, repos
):
    note = await repos["notes"].create(Note(raw_text="undo recurring batch"))
    recurring = await repos["tasks"].create(
        Task(
            title="Daily batch task",
            due_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
            source_note_id=note.id,
            recurrence_rule="daily",
            recurrence_series_id="undo-recurring-batch",
            recurrence_occurrence_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
        )
    )
    plain = await repos["tasks"].create(
        Task(title="Plain batch task", source_note_id=note.id)
    )
    service = _service(capture_service, repos)
    batch = await service.task_operation_service.complete_many(
        [recurring.id, plain.id],
        transition="test_recurring_batch",
        now=datetime(2026, 6, 28, 0, 0, tzinfo=UTC),
    )

    undone = await service.task_operation_service.undo_batch(batch.batch_event_id)
    active = await repos["tasks"].list_active()

    assert set(undone.restored_task_ids) == {recurring.id, plain.id}
    assert {task.id for task in active} == {recurring.id, plain.id}
    assert not any(
        task.recurrence_occurrence_at == datetime(2026, 7, 2, 10, 0, tzinfo=UTC)
        for task in active
    )


async def test_interval_recurrence_completion_creates_next_occurrence(capture_service, repos):
    note = await repos["notes"].create(Note(raw_text="interval recurrence"))
    task = await repos["tasks"].create(
        Task(
            title="Water plants",
            due_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
            source_note_id=note.id,
            recurrence_rule="interval:2d",
            recurrence_series_id="plants",
            recurrence_occurrence_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
        )
    )
    service = _service(capture_service, repos)

    result = await service.task_operation_service.complete(
        task.id,
        transition="test_interval_recurrence",
        now=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
    )

    assert result.next_created is True
    assert result.next_task is not None
    assert result.next_task.due_at == datetime(2026, 7, 3, 9, 0, tzinfo=UTC)
    assert result.next_task.recurrence_rule == "interval:2d"


@pytest.mark.parametrize(
    "answer",
    [
        "ừ",
        "ừ xong rồi",
        "xác nhận",
        "xác nhận xong",
        "done",
        "ok xong",
        "chuẩn",
        "đồng ý",
    ],
)
async def test_done_confirmation_accepts_natural_variants(
    answer, capture_service, repos
):
    note = await repos["notes"].create(Note(raw_text=f"confirm {answer}"))
    task = await repos["tasks"].create(
        Task(title=f"Task {answer}", source_note_id=note.id)
    )
    await repos["clarifications"].create(
        ClarificationRequest(
            source_chat_id=f"chat-{answer}",
            entity_type="task",
            entity_id=task.id,
            field_name="status|done",
            question="Anh muốn đánh dấu xong task này phải không?",
        )
    )

    result = await capture_service.clarification_service.answer_pending(
        f"chat-{answer}", answer
    )
    updated = await repos["tasks"].get_by_id(task.id)

    assert result.handled is True
    assert updated is not None and str(updated.status) == "done"


async def test_done_recurring_task_self_heals_missing_next_occurrence(repos):
    note = await repos["notes"].create(Note(raw_text="self heal daily"))
    due_at = datetime(2026, 6, 21, 17, 0, tzinfo=UTC)
    task = await repos["tasks"].create(
        Task(
            title="Daily task missing next",
            status=TaskStatus.DONE,
            due_at=due_at,
            source_note_id=note.id,
            recurrence_rule="daily",
            recurrence_series_id="self-heal-daily",
            recurrence_occurrence_at=due_at,
        )
    )

    _, next_task, created = await repos["tasks"].complete_and_schedule_next(
        task.id
    )
    _, same_next, created_again = (
        await repos["tasks"].complete_and_schedule_next(task.id)
    )

    assert created is True
    assert next_task is not None
    assert next_task.due_at == due_at + timedelta(days=1)
    assert created_again is False
    assert same_next is not None and same_next.id == next_task.id


async def test_recurrence_status_question_is_query_not_correction(
    capture_service, repos
):
    note = await repos["notes"].create(Note(raw_text="daily status"))
    due_at = datetime(2026, 6, 22, 17, 0, tzinfo=UTC)
    await repos["tasks"].create(
        Task(
            title="Tạo kịch bản audio sảng văn",
            status=TaskStatus.OPEN,
            due_at=due_at,
            source_note_id=note.id,
            recurrence_rule="daily",
            recurrence_series_id="daily-status",
            recurrence_occurrence_at=due_at,
        )
    )
    service = _service(capture_service, repos)

    result = await service.handle_text(
        CaptureRequest(raw_text="Task sảng văn của tôi không phải task định kỳ à")
    )

    assert result.intent == "query_task_recurrence"
    assert "là task hằng ngày" in result.reply
    assert "tạo kỳ kế tiếp" in result.reply


async def test_recurrence_time_followup_explains_utc_instead_of_querying_context(
    capture_service, repos
):
    note = await repos["notes"].create(Note(raw_text="daily time explanation"))
    await repos["tasks"].create(
        Task(
            title="Tạo kịch bản audio sảng văn",
            status=TaskStatus.OPEN,
            due_at=datetime(2026, 6, 23, 16, 59, tzinfo=UTC),
            source_note_id=note.id,
            recurrence_rule="daily",
            recurrence_series_id="daily-time-explanation",
            recurrence_occurrence_at=datetime(2026, 6, 23, 16, 59, tzinfo=UTC),
        )
    )
    service = _service(capture_service, repos)

    result = await service.handle_text(
        CaptureRequest(
            raw_text="tại sao lại là 16:59? tôi nhớ là định kỳ sẽ là 23h:59?"
        )
    )

    assert result.intent == "query_task_recurrence"
    assert "16:59 là giờ UTC" in result.reply
    assert "23:59 23/06/2026" in result.reply
    assert "person hoặc project" not in result.reply


async def test_tomorrow_query_supersedes_pending_clarification(capture_service, repos):
    service = _service(capture_service, repos)

    assert service.is_explicit_new_action("Mai tôi cần làm gì") is True


async def test_daily_completion_creates_one_next_occurrence(repos):
    note = await repos["notes"].create(Note(raw_text="daily"))
    task = await repos["tasks"].create(
        Task(
            title="Daily task",
            status=TaskStatus.OPEN,
            due_at=datetime(2026, 6, 21, 16, 59, tzinfo=UTC),
            source_note_id=note.id,
            recurrence_rule="daily",
            recurrence_series_id="series-daily",
            recurrence_occurrence_at=datetime(2026, 6, 21, 16, 59, tzinfo=UTC),
        )
    )

    completed, next_task, created = await repos["tasks"].complete_and_schedule_next(task.id)
    _, same_next, created_again = await repos["tasks"].complete_and_schedule_next(task.id)

    assert completed is not None and str(completed.status) == "done"
    assert created is True
    assert next_task is not None
    assert next_task.due_at == task.due_at + timedelta(days=1)
    assert created_again is False
    assert same_next is not None and same_next.id == next_task.id


async def test_weekly_completion_preserves_links_and_priority(repos):
    note = await repos["notes"].create(Note(raw_text="weekly"))
    project = await repos["projects"].find_or_create("MemoCore")
    task = await repos["tasks"].create(
        Task(
            title="Weekly task",
            status=TaskStatus.OPEN,
            priority="high",
            project_id=project.id,
            due_at=datetime(2026, 6, 21, 3, 0, tzinfo=UTC),
            source_note_id=note.id,
            recurrence_rule="weekly",
            recurrence_series_id="series-weekly",
            recurrence_occurrence_at=datetime(2026, 6, 21, 3, 0, tzinfo=UTC),
        )
    )

    _, next_task, _ = await repos["tasks"].complete_and_schedule_next(task.id)

    assert next_task is not None
    assert next_task.due_at == task.due_at + timedelta(weeks=1)
    assert next_task.priority == "high"
    assert next_task.project_id == project.id


async def test_number_reference_supports_priority_done_and_cancel(capture_service, repos):
    first, second = await _two_tasks(repos)
    service = _service(capture_service, repos)
    await service.remember_task_list(
        "chat-1",
        "1. Task đầu — hạn hôm nay\n2. Tạo kịch bản audio sảng văn — hạn hôm nay",
        "today",
    )

    priority = await service.handle_text(
        CaptureRequest(raw_text="đổi priority số 2 thành cao", source_chat_id="chat-1")
    )
    done = await service.handle_text(
        CaptureRequest(raw_text="hoàn thành cái thứ 2", source_chat_id="chat-1")
    )
    cancelled = await service.handle_text(
        CaptureRequest(raw_text="bỏ task 1", source_chat_id="chat-1")
    )

    assert priority.intent == "update_task_priority"
    assert done.intent == "mark_task_done"
    assert cancelled.intent == "cancel_task"
    assert (await repos["tasks"].get_by_id(second.id)).priority == "high"
    assert str((await repos["tasks"].get_by_id(second.id)).status) == "done"
    assert str((await repos["tasks"].get_by_id(first.id)).status) == "cancelled"


async def test_today_context_only_remembers_five_visible_actionable_tasks(
    capture_service, repos
):
    now = datetime(2030, 1, 2, 10, 0, tzinfo=UTC)
    note = await repos["notes"].create(Note(raw_text="today context"))
    tasks = []
    for index in range(7):
        tasks.append(
            await repos["tasks"].create(
                Task(
                    title=f"Task visible order {index + 1}",
                    due_at=now - timedelta(hours=index + 1),
                    source_note_id=note.id,
                )
            )
        )
    waiting = await repos["tasks"].create(
        Task(
            title="Waiting must not be numbered",
            status=TaskStatus.WAITING,
            due_at=now - timedelta(hours=1),
            source_note_id=note.id,
        )
    )
    service = _service(capture_service, repos, now=now)

    await service.remember_task_list(
        "chat-today",
        await service.secretary_service.today(now),
        "today",
        now_utc=now,
    )
    context = await service.task_list_context_repo.get_context(
        "chat-today",
        now=now,
    )

    expected = [task.id for task in sorted(tasks, key=lambda item: item.due_at)[:5]]
    assert context is not None
    assert list(context.task_ids) == expected
    assert tasks[0].id not in context.task_ids
    assert tasks[1].id not in context.task_ids
    assert waiting.id not in context.task_ids


async def test_task_name_answer_is_consumed_by_selection_clarification(
    capture_service, repos
):
    first, second = await _two_tasks(repos)
    clarification = ClarificationRequest(
        source_chat_id="chat-1",
        entity_type="task_selection_due_update",
        entity_id=f"{first.id},{second.id}",
        field_name="due_at|2026-06-22T16:59:00+00:00",
        question="Anh chọn task nào?",
    )
    await repos["clarifications"].create(clarification)

    result = await capture_service.clarification_service.answer_pending(
        "chat-1", "Tạo kịch bản audio sảng văn"
    )
    updated = await repos["tasks"].get_by_id(second.id)

    assert result.handled is True
    assert updated is not None and updated.due_at == datetime(
        2026, 6, 22, 16, 59, tzinfo=UTC
    )
    assert await repos["notes"].find_by_source_message(
        "telegram", "chat-1", None
    ) is None
