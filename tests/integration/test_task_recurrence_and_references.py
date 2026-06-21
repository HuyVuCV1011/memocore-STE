from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from memocore.domain.models import ClarificationRequest, Note, Task, TaskStatus
from memocore.domain.schemas import CaptureRequest
from memocore.services.conversation_service import ConversationService
from memocore.services.secretary_service import SecretaryService


def _service(capture_service, repos) -> ConversationService:
    secretary = SecretaryService(
        repos["tasks"],
        repos["reminders"],
        repos["followups"],
        repos["projects"],
        repos["memory"],
        display_timezone=ZoneInfo("Asia/Ho_Chi_Minh"),
        person_repo=repos["people"],
    )
    return ConversationService(
        capture_service,
        secretary,
        repos["notes"],
        repos["tasks"],
        capture_service.memory_service,
        capture_service.event_service,
    )


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
