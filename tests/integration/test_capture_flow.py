from memocore.domain.models import MemoryBucket, MemoryKind, Note, NoteStatus, Task
from memocore.domain.schemas import CaptureRequest, MemoryCandidate, NoteExtraction


async def test_capture_flow_persists_extracted_objects(capture_service, repos):
    response = await capture_service.capture(
        CaptureRequest(raw_text="Remind me tomorrow to call Alex", source_chat_id="123")
    )

    note = await repos["notes"].get_by_id(response.note_id)
    tasks = await repos["tasks"].list_by_note(response.note_id)
    reminders = await repos["reminders"].list_by_note(response.note_id)
    events = await repos["events"].list_by_entity("note", response.note_id)

    assert note.status == NoteStatus.PROCESSED
    assert tasks[0].title == "Call Alex about the budget"
    assert reminders[0].status == "scheduled"
    assert response.tasks_created == 1
    assert [event.event_type for event in events]


async def test_memory_capture_reports_semantic_duplicate_without_merging(
    capture_service, fake_provider, repos
):
    note = await repos["notes"].create(Note(raw_text="memory source"))
    await capture_service.memory_service.persist_candidates(
        [
            MemoryCandidate(
                bucket=MemoryBucket.PROFILE,
                kind=MemoryKind.PREFERENCE,
                content="Vũ thích câu trả lời ngắn gọn và trực tiếp",
                confidence=0.9,
            )
        ],
        note.id,
    )
    fake_provider.response = NoteExtraction(
        summary="Ưu tiên câu trả lời trực tiếp",
        memories=[
            MemoryCandidate(
                bucket=MemoryBucket.PROFILE,
                kind=MemoryKind.PREFERENCE,
                content="Vũ thích câu trả lời trực tiếp và ngắn gọn",
                confidence=0.9,
            )
        ],
    )

    response = await capture_service.capture(
        CaptureRequest(raw_text="Tôi thích câu trả lời trực tiếp và ngắn gọn #mem")
    )

    assert response.duplicate_suggestions
    assert "Mình chưa tự gộp" in response.duplicate_suggestions[0]


async def test_linkedin_capture_suggests_similar_existing_note(
    capture_service, fake_provider, repos
):
    existing = await repos["notes"].create(
        Note(
            raw_text="Ba bài học quản lý nhân sự từ dự án STE #li",
            summary="Ba bài học quản lý nhân sự",
            tags=["li", "linkedin"],
            status=NoteStatus.PROCESSED,
        )
    )
    fake_provider.response = NoteExtraction(
        summary="Bài học quản lý nhân sự ở STE",
        tags=["li", "linkedin"],
    )

    response = await capture_service.capture(
        CaptureRequest(raw_text="Ba bài học quản lý nhân sự trong dự án STE #li")
    )

    assert response.note_id != existing.id
    assert response.duplicate_suggestions
    assert "note LinkedIn đã có" in response.duplicate_suggestions[0]


async def test_project_state_memory_has_lifecycle_metadata(capture_service, repos):
    note = await repos["notes"].create(Note(raw_text="project memory source"))
    created = await capture_service.memory_service.persist_candidates(
        [
            MemoryCandidate(
                bucket=MemoryBucket.PROJECT,
                kind=MemoryKind.PROJECT_STATE,
                content="STE Dashboard đang ở giai đoạn beta",
                confidence=0.9,
            )
        ],
        note.id,
    )

    item = created[0]
    assert item.source_type == "user_note"
    assert item.observed_at is not None
    assert item.valid_from is not None
    assert item.valid_until is not None
    assert item.last_confirmed_at is not None


async def test_remind_tag_injects_candidate_when_model_omits_it(
    capture_service, fake_provider, repos
):
    fake_provider.response = NoteExtraction(summary="Chuẩn bị họp", tags=[])

    response = await capture_service.capture(
        CaptureRequest(
            raw_text="Chuẩn bị họp với team #remind",
            source_chat_id="chat-1",
            source_message_id="message-1",
        )
    )

    reminders = await repos["reminders"].list_by_note(response.note_id)
    assert response.reminders_created == 1
    assert reminders[0].title == "Chuẩn bị họp với team"
    assert response.clarification_question is not None


async def test_completion_note_marks_matching_task_done_without_memory(
    capture_service, fake_provider, repos
):
    source_note = await repos["notes"].create(Note(raw_text="today I need to finish memocore"))
    task = await repos["tasks"].create(
        Task(title="Hoàn thành project AI memocore version 1", source_note_id=source_note.id)
    )
    fake_provider.response = NoteExtraction(
        summary="Tôi đã hoàn thành dự án AI memocore phiên bản 1",
        memories=[
            MemoryCandidate(
                bucket=MemoryBucket.PROJECT,
                kind=MemoryKind.PROJECT_STATE,
                content="phiên bản 1 đã hoàn thành",
                confidence=0.9,
            )
        ],
    )

    response = await capture_service.capture(
        CaptureRequest(raw_text="tôi đã làm xong project AI memocore version 1")
    )

    active_tasks = await repos["tasks"].list_active()
    memories = await repos["memory"].list_by_note(response.note_id)

    assert response.tasks_completed == 1
    assert task.id not in {item.id for item in active_tasks}
    assert memories == []


async def test_state_query_does_not_create_memory(capture_service, fake_provider, repos):
    fake_provider.response = NoteExtraction(
        summary="Tìm lại những gì đã lưu trong bản thân",
        memories=[
            MemoryCandidate(
                bucket=MemoryBucket.PROFILE,
                kind=MemoryKind.FACT,
                content="tôi đã lưu gì ở bản thân",
                confidence=0.8,
            )
        ],
    )

    response = await capture_service.capture(CaptureRequest(raw_text="tôi đã lưu gì ở bản thân"))
    memories = await repos["memory"].list_by_note(response.note_id)

    assert "memory query" in response.summary
    assert memories == []
    assert fake_provider.calls == []


async def test_memory_delete_request_hard_deletes_matching_memory(capture_service, repos):
    note = await repos["notes"].create(Note(raw_text="memory source"))
    await capture_service.memory_service.persist_candidates(
        [
            MemoryCandidate(
                bucket=MemoryBucket.PROFILE,
                kind=MemoryKind.PREFERENCE,
                content="thích ăn cơm tấm",
                confidence=0.9,
            ),
            MemoryCandidate(
                bucket=MemoryBucket.PROFILE,
                kind=MemoryKind.PREFERENCE,
                content="vợ thích ăn pizza",
                confidence=0.9,
            ),
            MemoryCandidate(
                bucket=MemoryBucket.PROFILE,
                kind=MemoryKind.PREFERENCE,
                content="không thích ăn pizza",
                confidence=0.9,
            )
        ],
        note.id,
    )

    response = await capture_service.capture(
        CaptureRequest(raw_text="xoá memory thích ăn cơm tấm")
    )
    memories = await repos["memory"].list_active()

    assert response.memories_deleted == 1
    assert all("cơm tấm" not in item.content for item in memories)

    response = await capture_service.capture(
        CaptureRequest(raw_text="xóa memory liên quan đến pizza")
    )
    memories = await repos["memory"].list_active()

    assert response.memories_deleted == 2
    assert all("pizza" not in item.content for item in memories)


async def test_memory_correction_supersedes_related_old_memory(
    capture_service, fake_provider, repos
):
    source_note = await repos["notes"].create(Note(raw_text="STE cofounders"))
    await capture_service.memory_service.persist_candidates(
        [
            MemoryCandidate(
                bucket=MemoryBucket.PROJECT,
                kind=MemoryKind.FACT,
                content="STE có 5 co-founder ngoài tôi ra",
                confidence=0.9,
            )
        ],
        source_note.id,
    )
    fake_provider.response = NoteExtraction(
        summary="Có 4 co-founder ở STE",
        memories=[
            MemoryCandidate(
                bucket=MemoryBucket.PROJECT,
                kind=MemoryKind.FACT,
                content="Có 4 co-founder ở STE",
                confidence=0.9,
            )
        ],
    )

    response = await capture_service.capture(
        CaptureRequest(raw_text="cập nhật lại, có 4 co-founder ở STE thôi")
    )
    memories = await repos["memory"].list_all()
    statuses = {item.content: item.status for item in memories}
    created_tasks = await repos["tasks"].list_by_note(response.note_id)

    assert statuses["STE có 5 co-founder ngoài tôi ra"] == "superseded"
    assert statuses["Có 4 co-founder ở STE"] == "candidate"
    assert created_tasks == []


async def test_new_conflicting_memory_supersedes_old_without_correction_phrase(
    capture_service, fake_provider, repos
):
    source_note = await repos["notes"].create(Note(raw_text="STE has 3 cofounders"))
    await capture_service.memory_service.persist_candidates(
        [
            MemoryCandidate(
                bucket=MemoryBucket.PROJECT,
                kind=MemoryKind.FACT,
                content="ste có 3 co founder",
                confidence=0.9,
            )
        ],
        source_note.id,
    )
    fake_provider.response = NoteExtraction(
        summary="Ste có 4 co founder",
        memories=[
            MemoryCandidate(
                bucket=MemoryBucket.PROJECT,
                kind=MemoryKind.FACT,
                content="Ste có 4 co founder",
                confidence=0.9,
            )
        ],
    )

    await capture_service.capture(CaptureRequest(raw_text="ste có 4 co founder"))
    memories = await repos["memory"].list_all()
    statuses = {item.content: item.status for item in memories}

    assert statuses["ste có 3 co founder"] == "superseded"
    assert statuses["Ste có 4 co founder"] == "candidate"


async def test_relationship_name_correction_supersedes_old_memory(
    capture_service, fake_provider, repos
):
    source_note = await repos["notes"].create(Note(raw_text="wife name"))
    await capture_service.memory_service.persist_candidates(
        [
            MemoryCandidate(
                bucket=MemoryBucket.PROFILE,
                kind=MemoryKind.FACT,
                content="vợ tôi là Châu Châu",
                confidence=0.9,
            )
        ],
        source_note.id,
    )
    fake_provider.response = NoteExtraction(
        summary="Sửa lại tên vợ tôi thành Chow Chow",
        memories=[
            MemoryCandidate(
                bucket=MemoryBucket.PROFILE,
                kind=MemoryKind.FACT,
                content="Tên vợ tôi là Chow Chow",
                confidence=0.9,
            )
        ],
    )

    await capture_service.capture(CaptureRequest(raw_text="sửa lại tên vợ tôi thành Chow Chow"))
    memories = await repos["memory"].list_all()
    statuses = {item.content: item.status for item in memories}

    assert statuses["vợ tôi là Châu Châu"] == "superseded"
    assert statuses["Tên vợ tôi là Chow Chow"] == "candidate"


async def test_change_name_update_does_not_create_task_and_supersedes_old_memory(
    capture_service, fake_provider, repos
):
    source_note = await repos["notes"].create(Note(raw_text="wife name"))
    await capture_service.memory_service.persist_candidates(
        [
            MemoryCandidate(
                bucket=MemoryBucket.PROFILE,
                kind=MemoryKind.FACT,
                content="Tên vợ tôi là Chow Chow",
                confidence=0.9,
            )
        ],
        source_note.id,
    )
    fake_provider.response = NoteExtraction(
        summary="Đổi tên vợ tôi thành Võ Trần Hoàng Châu",
        tasks=[],
        memories=[
            MemoryCandidate(
                bucket=MemoryBucket.PROFILE,
                kind=MemoryKind.FACT,
                content="Tên vợ tôi là Võ Trần Hoàng Châu",
                confidence=0.9,
            )
        ],
    )

    response = await capture_service.capture(
        CaptureRequest(raw_text="đổi tên vợ tôi thành Võ Trần Hoàng Châu")
    )
    memories = await repos["memory"].list_all()
    statuses = {item.content: item.status for item in memories}
    tasks = await repos["tasks"].list_by_note(response.note_id)

    assert tasks == []
    assert statuses["Tên vợ tôi là Chow Chow"] == "superseded"
    assert statuses["Tên vợ tôi là Võ Trần Hoàng Châu"] == "candidate"
