import pytest
from datetime import UTC, datetime
from memocore.domain.models import Note, Task
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
