from datetime import UTC, datetime, time, timedelta

from memocore.domain.models import MemoryBucket, MemoryKind, Note, Task
from memocore.domain.schemas import CaptureRequest, IntentClassification, MemoryCandidate
from memocore.services.conversation_service import ConversationService, classify_intent
from memocore.services.secretary_service import SecretaryService


class FakeIntentClassifierService:
    def __init__(self, mapping: dict[str, IntentClassification]):
        self.mapping = mapping

    async def classify(self, raw_text: str) -> IntentClassification:
        return self.mapping[raw_text]


def _conversation_service(capture_service, repos, classifier=None) -> ConversationService:
    secretary_service = SecretaryService(
        repos["tasks"],
        repos["reminders"],
        repos["followups"],
        repos["projects"],
        repos["memory"],
    )
    return ConversationService(
        capture_service,
        secretary_service,
        repos["notes"],
        repos["tasks"],
        capture_service.memory_service,
        capture_service.event_service,
        classifier,
    )


def test_classifies_vietnamese_and_english_examples():
    assert classify_intent("hôm nay tôi cần làm gì") == "query_today"
    assert classify_intent("mai tôi cần làm gì") == "query_tomorrow"
    assert classify_intent("tôi đã lưu gì về bản thân") == "query_memory"
    assert classify_intent("project MemoCore còn gì chưa xong") == "query_projects"
    assert classify_intent("tôi đã làm xong việc mua pc") == "mark_task_done"
    assert classify_intent("Đã mua pc xong") == "mark_task_done"
    assert classify_intent("Đổi lại giờ soạn giáo án thành hạn chót là 17h") == "update_task_due"
    assert classify_intent("cái này không phải task") == "memory_correction"
    assert classify_intent("đừng lưu cái này") == "memory_delete"
    assert classify_intent("what have you saved about me?") == "query_memory"
    assert classify_intent("what open tasks do I have?") == "query_tasks"
    assert classify_intent("xoá toàn bộ task đang có") == "delete_all_tasks"


def test_classifies_planning_checklist_with_xong_chua_as_capture():
    raw_text = (
        "hôm nay tôi cần check nhanh qua vấn đề liên quan đến mindX hiện tại, "
        "các dự án đã làm xong chưa, tiến đồ dự án, sắp xếp nhân sự tf các thứu như thế nào\n"
        "sau đó tôi cần check nhanh qua bài tập của các bạn học viên mindX đã nộp, "
        "check qua cv\n"
        "check qua pc mới"
    )

    assert classify_intent(raw_text) == "capture_note"


async def test_today_query_answers_agenda_without_extraction(
    capture_service, fake_provider, repos
):
    note = await repos["notes"].create(Note(raw_text="task source"))
    await repos["tasks"].create(
        Task(
            title="Finish MemoCore V2 router",
            source_note_id=note.id,
            due_at=datetime(2026, 6, 3, 6, 0, tzinfo=UTC),
        )
    )
    service = _conversation_service(capture_service, repos)

    result = await service.handle_text(CaptureRequest(raw_text="hôm nay tôi cần làm gì"))

    assert result.intent == "query_today"
    assert "Finish MemoCore V2 router" in result.reply
    assert fake_provider.calls == []


async def test_tomorrow_query_answers_agenda_without_extraction(
    capture_service, fake_provider, repos
):
    note = await repos["notes"].create(Note(raw_text="task source"))
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    await repos["tasks"].create(
        Task(
            title="Call dưa hấu",
            source_note_id=note.id,
            due_at=datetime.combine(tomorrow, time(6, 0), tzinfo=UTC),
        )
    )
    service = _conversation_service(capture_service, repos)

    result = await service.handle_text(CaptureRequest(raw_text="mai tôi cần làm gì"))

    assert result.intent == "query_tomorrow"
    assert "Call dưa hấu" in result.reply
    assert fake_provider.calls == []


async def test_preference_statement_captures_memory_without_intent_classifier(
    capture_service, fake_provider, repos
):
    service = _conversation_service(capture_service, repos)

    result = await service.handle_text(CaptureRequest(raw_text="tôi thích ăn cơm tấm"))

    assert result.intent == "capture_memory"
    assert result.captured is True
    assert fake_provider.calls


async def test_task_moi_followup_captures_previous_message(
    capture_service, fake_provider, repos
):
    previous_text = (
        "hôm nay tôi cần check nhanh qua vấn đề liên quan đến mindX hiện tại, "
        "các dự án đã làm xong chưa, tiến độ dự án, sắp xếp nhân sự tf các thứ như thế nào\n"
        "sau đó tôi cần check nhanh qua bài tập của các bạn học viên mindX đã nộp, "
        "check qua cv\n"
        "check qua pc mới"
    )
    await repos["notes"].create(
        Note(
            raw_text=previous_text,
            source_chat_id="chat-mindx",
            source_message_id="1",
            summary="Conversation intent: mark_task_done",
            tags=["mark_task_done"],
        )
    )
    service = _conversation_service(capture_service, repos)

    result = await service.handle_text(
        CaptureRequest(
            raw_text="task mới",
            source_chat_id="chat-mindx",
            source_message_id="2",
        )
    )

    note = await repos["notes"].find_by_source_message("telegram", "chat-mindx", "2")
    assert result.intent == "capture_previous_as_task"
    assert result.captured is True
    assert note is not None
    assert note.raw_text == previous_text
    assert fake_provider.calls


async def test_status_question_is_captured_even_if_classifier_says_done(
    capture_service, fake_provider, repos
):
    source_note = await repos["notes"].create(Note(raw_text="bad old task source"))
    task = await repos["tasks"].create(Task(title="task mới", source_note_id=source_note.id))
    raw_text = "các dự án đã làm xong chưa"
    classifier = FakeIntentClassifierService(
        {
            raw_text: IntentClassification(
                intent="mark_task_done",
                confidence=0.99,
                target_entity_hints="task mới",
            )
        }
    )
    service = _conversation_service(capture_service, repos, classifier=classifier)

    result = await service.handle_text(CaptureRequest(raw_text=raw_text))

    still_active = await repos["tasks"].list_active()
    assert result.intent == "capture_note"
    assert result.captured is True
    assert task.id in {item.id for item in still_active}
    assert fake_provider.calls


async def test_delete_all_tasks_cancels_active_tasks_without_capture(
    capture_service, fake_provider, repos
):
    note = await repos["notes"].create(Note(raw_text="task source"))
    task_one = await repos["tasks"].create(Task(title="Soạn giáo án cho aptech", source_note_id=note.id))
    task_two = await repos["tasks"].create(Task(title="call Alex at 9", source_note_id=note.id))
    service = _conversation_service(capture_service, repos)

    result = await service.handle_text(CaptureRequest(raw_text="xoá toàn bộ task đang có"))

    active = await repos["tasks"].list_active()
    updated_one = await repos["tasks"].get_by_id(task_one.id)
    updated_two = await repos["tasks"].get_by_id(task_two.id)
    assert result.intent == "delete_all_tasks"
    assert "Đã hủy 2 task đang mở" in result.reply
    assert active == []
    assert updated_one.status == "cancelled"
    assert updated_two.status == "cancelled"
    assert fake_provider.calls == []


async def test_memory_query_answers_profile_memory_without_extraction(
    capture_service, fake_provider, repos
):
    note = await repos["notes"].create(Note(raw_text="memory source"))
    await capture_service.memory_service.persist_candidates(
        [
            MemoryCandidate(
                bucket=MemoryBucket.PROFILE,
                kind=MemoryKind.FACT,
                content="tôi thích làm việc buổi sáng",
                confidence=0.9,
            ),
            MemoryCandidate(
                bucket=MemoryBucket.PROJECT,
                kind=MemoryKind.PROJECT_STATE,
                content="MemoCore đang ở V2",
                confidence=0.9,
            ),
        ],
        note.id,
    )
    service = _conversation_service(capture_service, repos)

    result = await service.handle_text(CaptureRequest(raw_text="tôi đã lưu gì về bản thân"))

    assert result.intent == "query_memory"
    assert "tôi thích làm việc buổi sáng" in result.reply
    assert "MemoCore đang ở V2" not in result.reply
    assert fake_provider.calls == []


async def test_project_query_returns_project_tasks_without_creating_note_objects(
    capture_service, fake_provider, repos
):
    note = await repos["notes"].create(Note(raw_text="project source"))
    project = await repos["projects"].find_or_create("MemoCore")
    await repos["tasks"].create(
        Task(
            title="Implement conversation routing",
            source_note_id=note.id,
            project_id=project.id,
        )
    )
    service = _conversation_service(capture_service, repos)

    result = await service.handle_text(
        CaptureRequest(raw_text="project MemoCore còn gì chưa xong")
    )

    assert result.intent == "query_projects"
    assert "Implement conversation routing" in result.reply
    assert fake_provider.calls == []


async def test_mark_task_done_marks_matching_task_without_extraction(
    capture_service, fake_provider, repos
):
    note = await repos["notes"].create(Note(raw_text="task source"))
    task = await repos["tasks"].create(Task(title="mua pc", source_note_id=note.id))
    service = _conversation_service(capture_service, repos)

    result = await service.handle_text(CaptureRequest(raw_text="tôi đã làm xong việc mua pc"))

    active = await repos["tasks"].list_active()
    assert result.intent == "mark_task_done"
    assert "Đã đánh dấu xong" in result.reply
    assert task.id not in {item.id for item in active}
    assert fake_provider.calls == []


async def test_mark_task_done_supports_da_object_xong_pattern(
    capture_service, fake_provider, repos
):
    note = await repos["notes"].create(Note(raw_text="task source"))
    task = await repos["tasks"].create(Task(title="Đi mua pc với Sơn", source_note_id=note.id))
    service = _conversation_service(capture_service, repos)

    result = await service.handle_text(CaptureRequest(raw_text="Đã mua pc xong"))

    active = await repos["tasks"].list_active()
    memories = await repos["memory"].list_active()
    assert result.intent == "mark_task_done"
    assert "Đã đánh dấu xong" in result.reply
    assert task.id not in {item.id for item in active}
    assert all("mua pc" not in item.content.lower() for item in memories)
    assert fake_provider.calls == []


async def test_update_task_due_updates_matching_task_without_extraction(
    capture_service, fake_provider, repos
):
    note = await repos["notes"].create(Note(raw_text="task source"))
    task = await repos["tasks"].create(
        Task(
            title="Soạn giáo án cho aptech",
            source_note_id=note.id,
            due_at=datetime(2026, 6, 4, 23, 59, tzinfo=UTC),
        )
    )
    service = _conversation_service(capture_service, repos)

    result = await service.handle_text(
        CaptureRequest(raw_text="Đổi lại giờ soạn giáo án thành hạn chót là 17h")
    )

    active = await repos["tasks"].list_active()
    updated = next(item for item in active if item.id == task.id)
    assert result.intent == "update_task_due"
    assert "Đã đổi hạn" in result.reply
    assert updated.title == "Soạn giáo án cho aptech"
    assert updated.due_at is not None
    assert updated.due_at.hour == 17
    assert updated.due_at.minute == 0
    assert len(active) == 1
    assert fake_provider.calls == []


async def test_update_task_due_asks_when_multiple_tasks_match(
    capture_service, fake_provider, repos
):
    note = await repos["notes"].create(Note(raw_text="task source"))
    await repos["tasks"].create(Task(title="Soạn giáo án cho aptech", source_note_id=note.id))
    await repos["tasks"].create(Task(title="Soạn giáo án cho mindx", source_note_id=note.id))
    service = _conversation_service(capture_service, repos)

    result = await service.handle_text(
        CaptureRequest(raw_text="Đổi lại giờ soạn giáo án thành hạn chót là 17h")
    )

    active = await repos["tasks"].list_active()
    assert result.intent == "update_task_due"
    assert "Bạn muốn đổi hạn task nào" in result.reply
    assert "Soạn giáo án cho aptech" in result.reply
    assert "Soạn giáo án cho mindx" in result.reply
    assert all(item.due_at is None for item in active)
    assert fake_provider.calls == []


async def test_recent_task_correction_cancels_clear_recent_task(
    capture_service, fake_provider, repos
):
    note = await repos["notes"].create(Note(raw_text="task source"))
    task = await repos["tasks"].create(Task(title="Buy snacks", source_note_id=note.id))
    service = _conversation_service(capture_service, repos)

    result = await service.handle_text(CaptureRequest(raw_text="cái này không phải task"))

    active = await repos["tasks"].list_active()
    assert result.intent == "memory_correction"
    assert "Đã hủy task gần nhất" in result.reply
    assert task.id not in {item.id for item in active}
    assert fake_provider.calls == []


async def test_contextual_memory_delete_asks_when_ambiguous(
    capture_service, fake_provider, repos
):
    note = await repos["notes"].create(Note(raw_text="memory source"))
    await capture_service.memory_service.persist_candidates(
        [
            MemoryCandidate(
                bucket=MemoryBucket.PROFILE,
                kind=MemoryKind.FACT,
                content="tôi thích trà",
            ),
            MemoryCandidate(
                bucket=MemoryBucket.PROFILE,
                kind=MemoryKind.FACT,
                content="tôi thích cà phê",
            ),
        ],
        note.id,
    )
    service = _conversation_service(capture_service, repos)

    result = await service.handle_text(CaptureRequest(raw_text="đừng lưu cái này"))

    memories = await repos["memory"].list_active()
    assert result.intent == "memory_delete"
    assert "Bạn muốn mình xoá memory nào" in result.reply
    assert len(memories) == 2
    assert fake_provider.calls == []
