import pytest
from datetime import UTC, datetime
from memocore.domain.models import EventLog, EventType, Meeting, Note, Person, Task
from memocore.domain.schemas import CaptureRequest
from tests.integration.test_conversation_service import _conversation_service

async def test_rename_task_success(capture_service, fake_provider, repos):
    note = await repos["notes"].create(Note(raw_text="old task source"))
    task = await repos["tasks"].create(Task(title="kiểm tra dữ liệu thưởng leader", source_note_id=note.id))
    
    service = _conversation_service(capture_service, repos)
    
    result = await service.handle_text(
        CaptureRequest(raw_text="sửa cho tôi task kiểm tra dữ liệu thưởng thành kiểm tra CR")
    )
    
    assert result.intent == "rename_task"
    assert "Đã sửa tiêu đề task" in result.reply or "Renamed task" in result.reply
    
    updated_task = await repos["tasks"].get_by_id(task.id)
    assert updated_task.title == "kiểm tra CR"

async def test_rename_task_with_tasks_suffix(capture_service, fake_provider, repos):
    note = await repos["notes"].create(Note(raw_text="old task source"))
    task = await repos["tasks"].create(Task(title="kiểm tra dữ liệu thưởng leader", source_note_id=note.id))
    
    service = _conversation_service(capture_service, repos)
    
    result = await service.handle_text(
        CaptureRequest(raw_text="sửa cho tôi task kiểm tra dữ liệu thưởng thành kiểm tra CR /tasks")
    )
    
    assert result.intent == "rename_task"
    assert "Tasks đang mở" in result.reply or "Active tasks" in result.reply
    
    updated_task = await repos["tasks"].get_by_id(task.id)
    assert updated_task.title == "kiểm tra CR"


async def test_rename_reconciles_linked_meeting_and_stale_person_then_undoes(
    capture_service, repos
):
    note = await repos["notes"].create(Note(raw_text="Gặp Khôi Nguyên lúc 18h"))
    person = await repos["people"].create(
        Person(display_name="Nguyễn Hoàng Khôi Nguyên", aliases=["Khôi Nguyên"])
    )
    due_at = datetime(2026, 6, 26, 11, 0, tzinfo=UTC)
    task = await repos["tasks"].create(
        Task(
            title="Gặp Khôi Nguyên lấy áo vest và uống bia",
            due_at=due_at,
            person_id=person.id,
            source_note_id=note.id,
        )
    )
    meeting = await repos["meetings"].create(
        Meeting(
            title="Gặp Khôi Nguyên",
            starts_at=due_at,
            person_id=person.id,
            source_note_id=note.id,
        )
    )
    await repos["meetings"].add_person(meeting.id, person.id)
    service = _conversation_service(capture_service, repos)

    result = await service.handle_text(
        CaptureRequest(
            raw_text=(
                "đổi task gặp Khôi Nguyên lấy áo vest và uống bia "
                "thành làm giám khảo lớp APM10"
            ),
            source_chat_id="rename-chat",
            source_message_id="rename-1",
        )
    )

    updated_task = await repos["tasks"].get_by_id(task.id)
    updated_meeting = await repos["meetings"].get_by_id(meeting.id)
    assert result.reply_markup is not None
    assert updated_task.title == "làm giám khảo lớp APM10"
    assert updated_task.person_id is None
    assert updated_meeting.title == "làm giám khảo lớp APM10"
    assert updated_meeting.person_id is None
    assert await repos["activity_links"].meeting_ids_for_task(task.id) == [meeting.id]

    undone = await service.handle_text(
        CaptureRequest(
            raw_text="hoàn tác thay đổi vừa rồi",
            source_chat_id="rename-chat",
            source_message_id="rename-2",
        )
    )
    restored_task = await repos["tasks"].get_by_id(task.id)
    restored_meeting = await repos["meetings"].get_by_id(meeting.id)
    assert undone.intent == "undo_last_action"
    assert restored_task.title == "Gặp Khôi Nguyên lấy áo vest và uống bia"
    assert restored_task.person_id == person.id
    assert restored_meeting.title == "Gặp Khôi Nguyên"
    assert restored_meeting.person_id == person.id


async def test_legacy_repair_reconciles_renamed_task_and_meeting_from_different_notes(
    capture_service, repos
):
    task_note = await repos["notes"].create(Note(raw_text="merged task"))
    meeting_note = await repos["notes"].create(Note(raw_text="original meeting"))
    person = await repos["people"].create(
        Person(display_name="Nguyễn Hoàng Khôi Nguyên", aliases=["Khôi Nguyên"])
    )
    due_at = datetime(2026, 6, 26, 11, 0, tzinfo=UTC)
    task = await repos["tasks"].create(
        Task(
            title="làm giám khảo lớp APM10",
            due_at=due_at,
            person_id=person.id,
            source_note_id=task_note.id,
        )
    )
    meeting = await repos["meetings"].create(
        Meeting(
            title="Gặp Khôi Nguyên",
            starts_at=due_at,
            person_id=person.id,
            source_note_id=meeting_note.id,
        )
    )
    await repos["events"].create(
        EventLog(
            event_type=EventType.NOTE_PROCESSED,
            entity_type="task",
            entity_id=task.id,
            payload={
                "conversation_intent": "rename_task",
                "old_title": "Gặp Khôi Nguyên lấy áo vest và uống bia",
                "new_title": task.title,
            },
        )
    )

    repaired = await capture_service.activity_reconciliation_service.repair_legacy_renames()

    repaired_task = await repos["tasks"].get_by_id(task.id)
    repaired_meeting = await repos["meetings"].get_by_id(meeting.id)
    assert repaired == 1
    assert repaired_task.person_id is None
    assert repaired_meeting.title == task.title
    assert repaired_meeting.person_id is None
    assert await repos["activity_links"].meeting_ids_for_task(task.id) == [meeting.id]
