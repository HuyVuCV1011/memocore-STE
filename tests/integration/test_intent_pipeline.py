import os
import json
from datetime import UTC, datetime
import pytest

from memocore.domain.models import MemoryBucket, MemoryKind, Note, Task, MemoryItem, EventType
from memocore.domain.schemas import CaptureRequest, MemoryCandidate, IntentClassification
from memocore.services.conversation_service import ConversationService
from memocore.services.secretary_service import SecretaryService
from memocore.services.clarification_service import ClarificationService


class FakeIntentClassifierService:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}
        self.calls = []

    async def classify(self, raw_text: str) -> IntentClassification:
        self.calls.append(raw_text)
        return self.mapping.get(raw_text, IntentClassification(
            intent="casual_or_noop",
            confidence=0.9
        ))


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
        intent_classifier_service=classifier,
    )


async def test_layer1_deterministic_routing_memory(capture_service, repos):
    service = _conversation_service(capture_service, repos)
    
    # "Memory" query
    result1 = await service.handle_text(CaptureRequest(raw_text="Memory"))
    assert result1.intent == "query_memory"
    assert result1.captured is False

    # "In ra các ghi nhớ về tôi"
    result2 = await service.handle_text(CaptureRequest(raw_text="In ra các ghi nhớ về tôi"))
    assert result2.intent == "query_memory"
    
    # "Tôi đã lưu gì về bản thân"
    result3 = await service.handle_text(CaptureRequest(raw_text="Tôi đã lưu gì về bản thân"))
    assert result3.intent == "query_memory"

    # "Hôm nay tôi cần làm gì"
    result4 = await service.handle_text(CaptureRequest(raw_text="Hôm nay tôi cần làm gì"))
    assert result4.intent == "query_today"


async def test_layer2_layer3_casual_noop(capture_service, repos):
    classifier = FakeIntentClassifierService({
        "Hôm nay trời đẹp": IntentClassification(
            intent="casual_or_noop",
            confidence=0.95
        )
    })
    service = _conversation_service(capture_service, repos, classifier=classifier)
    
    result = await service.handle_text(CaptureRequest(raw_text="Hôm nay trời đẹp"))
    assert result.intent == "casual_or_noop"
    assert "Em nghe rồi" in result.reply or "Got it" in result.reply
    assert result.captured is False
    
    # Verify no note or task was persisted
    notes = await repos["notes"].database.connection()
    row = await (await notes.execute("SELECT COUNT(*) as count FROM notes")).fetchone()
    assert row["count"] == 0


async def test_task_due_update_confirmation_single_weak_match(capture_service, repos):
    # Setup single matching task
    note = await repos["notes"].create(Note(raw_text="setup"))
    task = await repos["tasks"].create(Task(title="Soạn giáo án cho aptech", source_note_id=note.id))
    
    capture_service.clarification_service.task_repo = repos["tasks"]
    
    classifier = FakeIntentClassifierService({
        "Đổi lại giờ soạn bài thành hạn chót là 17h": IntentClassification(
            intent="update_task",
            confidence=0.9,
            target_entity_hints="soạn bài"
        )
    })
    service = _conversation_service(capture_service, repos, classifier=classifier)
    
    # Request update
    result = await service.handle_text(CaptureRequest(
        raw_text="Đổi lại giờ soạn bài thành hạn chót là 17h",
        source_chat_id="chat123"
    ))
    
    # Should trigger confirmation because "soạn bài" is not strong match (not subset of title)
    assert "Anh muốn đổi hạn task" in result.reply
    assert "Soạn giáo án cho aptech" in result.reply
    
    # Verify pending clarification is saved
    clar = await capture_service.clarification_service.find_pending_for_chat("chat123")
    assert clar is not None
    assert clar.entity_type == "task"
    assert clar.entity_id == task.id
    assert clar.field_name.startswith("due_at|")
    
    # Answer "yes" to confirm
    ans_res = await capture_service.clarification_service.answer_pending("chat123", "có")
    assert ans_res.handled is True
    assert "Đã rõ" in ans_res.message
    
    # Verify task was updated
    updated_task = await repos["tasks"].get_by_id(task.id)
    assert updated_task.due_at is not None
    assert updated_task.due_at.hour == 17
    
    # Verify clarification is resolved
    clar_after = await capture_service.clarification_service.find_pending_for_chat("chat123")
    assert clar_after is None


async def test_task_due_update_exact_match_no_confirmation(capture_service, repos):
    # Setup single matching task
    note = await repos["notes"].create(Note(raw_text="setup"))
    task = await repos["tasks"].create(Task(title="Soạn giáo án cho aptech", source_note_id=note.id))
    
    classifier = FakeIntentClassifierService({
        "Đổi lại giờ Soạn giáo án cho aptech thành hạn chót là 17h": IntentClassification(
            intent="update_task",
            confidence=0.9,
            target_entity_hints="Soạn giáo án cho aptech"
        )
    })
    service = _conversation_service(capture_service, repos, classifier=classifier)
    
    # Request update
    result = await service.handle_text(CaptureRequest(
        raw_text="Đổi lại giờ Soạn giáo án cho aptech thành hạn chót là 17h",
        source_chat_id="chat123"
    ))
    
    # Should update immediately since query matches title exactly and no ambiguity detected
    assert "Đã đổi hạn" in result.reply
    
    updated_task = await repos["tasks"].get_by_id(task.id)
    assert updated_task.due_at is not None
    assert updated_task.due_at.hour == 17


async def test_task_due_update_multiple_matches_selection(capture_service, repos):
    # Setup multiple matching tasks
    note = await repos["notes"].create(Note(raw_text="setup"))
    task1 = await repos["tasks"].create(Task(title="Soạn giáo án cho aptech", source_note_id=note.id))
    task2 = await repos["tasks"].create(Task(title="Soạn giáo án cho mindx", source_note_id=note.id))
    
    capture_service.clarification_service.task_repo = repos["tasks"]
    
    classifier = FakeIntentClassifierService({
        "Đổi lại giờ soạn giáo án thành hạn chót là 17h": IntentClassification(
            intent="update_task",
            confidence=0.9,
            target_entity_hints="soạn giáo án"
        )
    })
    service = _conversation_service(capture_service, repos, classifier=classifier)
    
    result = await service.handle_text(CaptureRequest(
        raw_text="Đổi lại giờ soạn giáo án thành hạn chót là 17h",
        source_chat_id="chat456"
    ))
    
    # Triggers selection list
    assert "Anh muốn đổi hạn task nào" in result.reply
    assert "Soạn giáo án cho aptech" in result.reply
    assert "Soạn giáo án cho mindx" in result.reply
    
    clar = await capture_service.clarification_service.find_pending_for_chat("chat456")
    assert clar is not None
    assert clar.entity_type == "task_selection_due_update"
    
    # Choose option 2
    ans_res = await capture_service.clarification_service.answer_pending("chat456", "2")
    assert ans_res.handled is True
    assert "Soạn giáo án cho mindx" in ans_res.message
    
    # Check updated tasks
    ut1 = await repos["tasks"].get_by_id(task1.id)
    ut2 = await repos["tasks"].get_by_id(task2.id)
    assert ut1.due_at is None
    assert ut2.due_at is not None
    assert ut2.due_at.hour == 17


async def test_mark_task_done_single_weak_match(capture_service, repos):
    note = await repos["notes"].create(Note(raw_text="setup"))
    task = await repos["tasks"].create(Task(title="Đi mua pc với Sơn", source_note_id=note.id))
    
    capture_service.clarification_service.task_repo = repos["tasks"]
    
    classifier = FakeIntentClassifierService({
        "Đã mua đồ": IntentClassification(
            intent="mark_task_done",
            confidence=0.95,
            target_entity_hints="mua đồ"
        )
    })
    service = _conversation_service(capture_service, repos, classifier=classifier)
    
    result = await service.handle_text(CaptureRequest(
        raw_text="Đã mua đồ",
        source_chat_id="chat_done"
    ))
    
    # Weak match triggers confirmation
    assert "Anh muốn đánh dấu xong task 'Đi mua pc với Sơn' phải không?" in result.reply
    
    # Answer "không" to cancel
    ans_res = await capture_service.clarification_service.answer_pending("chat_done", "không")
    assert ans_res.handled is True
    assert "Đã hủy bỏ" in ans_res.message or "Task completion cancelled" in ans_res.message
    
    # Verify task is still candidate/active
    t = await repos["tasks"].get_by_id(task.id)
    assert t.status == "candidate"


async def test_correction_feedback_single_task_cancel(
    capture_service, repos, isolate_feedback_log
):
    # Setup a recent task
    note = await repos["notes"].create(Note(raw_text="wrong capture"))
    task = await repos["tasks"].create(Task(title="Trời hôm nay đẹp", source_note_id=note.id))
    
    classifier = FakeIntentClassifierService({
        "Cái này không phải task": IntentClassification(
            intent="correction_feedback",
            confidence=0.99
        )
    })
    service = _conversation_service(capture_service, repos, classifier=classifier)
    
    result = await service.handle_text(CaptureRequest(
        raw_text="Cái này không phải task",
        source_chat_id="chat_corr"
    ))
    
    assert "Đã hủy task gần nhất: Trời hôm nay đẹp" in result.reply
    
    # Check task cancelled
    t = await repos["tasks"].get_by_id(task.id)
    assert t.status == "cancelled"
    
    # Check feedback written to the isolated test log, not the live runtime log.
    assert isolate_feedback_log.exists()
    with isolate_feedback_log.open("r", encoding="utf-8") as f:
        lines = f.readlines()
        last_line = json.loads(lines[-1])
        assert last_line["intent"] == "correction_feedback"
        assert last_line["context"]["cancelled_task_id"] == task.id
        assert last_line["context"]["title"] == "Trời hôm nay đẹp"

    # Check event created
    events = await repos["events"].list_by_entity("task", task.id)
    assert any(ev.event_type == EventType.USER_FEEDBACK_RECORDED for ev in events)


async def test_correction_feedback_multiple_tasks_selection(capture_service, repos):
    # Setup multiple recent tasks
    note = await repos["notes"].create(Note(raw_text="wrong capture"))
    task1 = await repos["tasks"].create(Task(title="Trời hôm nay đẹp", source_note_id=note.id))
    task2 = await repos["tasks"].create(Task(title="Học Python", source_note_id=note.id))
    
    capture_service.clarification_service.task_repo = repos["tasks"]
    
    classifier = FakeIntentClassifierService({
        "Cái này không phải task": IntentClassification(
            intent="correction_feedback",
            confidence=0.99
        )
    })
    service = _conversation_service(capture_service, repos, classifier=classifier)
    
    result = await service.handle_text(CaptureRequest(
        raw_text="Cái này không phải task",
        source_chat_id="chat_corr_multi"
    ))
    
    assert "Cái nào không phải task?" in result.reply
    assert "Học Python" in result.reply
    
    clar = await capture_service.clarification_service.find_pending_for_chat("chat_corr_multi")
    assert clar is not None
    assert clar.entity_type == "task_selection_cancel"
    
    # Answer "2" to cancel "Trời hôm nay đẹp"
    ans_res = await capture_service.clarification_service.answer_pending("chat_corr_multi", "2")
    assert ans_res.handled is True
    assert "Trời hôm nay đẹp" in ans_res.message
    
    t1 = await repos["tasks"].get_by_id(task1.id)
    t2 = await repos["tasks"].get_by_id(task2.id)
    assert t1.status == "cancelled"
    assert t2.status == "candidate"


async def test_correction_feedback_recent_memory_rejection(capture_service, repos):
    # Setup a recent memory
    note = await repos["notes"].create(Note(raw_text="wrong capture"))
    memory = await repos["memory"].create(MemoryItem(
        bucket=MemoryBucket.PROFILE,
        kind=MemoryKind.FACT,
        content="tôi thích pizza",
        source_note_id=note.id
    ))
    
    classifier = FakeIntentClassifierService({
        "cái này không phải memory": IntentClassification(
            intent="correction_feedback",
            confidence=0.99
        )
    })
    service = _conversation_service(capture_service, repos, classifier=classifier)
    
    result = await service.handle_text(CaptureRequest(
        raw_text="cái này không phải memory",
        source_chat_id="chat_corr_mem"
    ))
    
    assert "Đã bỏ memory gần nhất: tôi thích pizza" in result.reply
    
    # Check memory rejected
    m = (await repos["memory"].list_all())[0]
    assert m.status == "rejected"


async def test_no_classifier_fallback_safety(capture_service, repos):
    # Test behavior when no intent_classifier_service is configured
    service = _conversation_service(capture_service, repos, classifier=None)
    
    # 1. "Hôm nay trời đẹp" should not be captured (routes to casual_or_noop)
    res1 = await service.handle_text(CaptureRequest(raw_text="Hôm nay trời đẹp"))
    assert res1.intent == "casual_or_noop"
    assert res1.captured is False
    
    # 2. "Memory" should be query_memory
    res2 = await service.handle_text(CaptureRequest(raw_text="Memory"))
    assert res2.intent == "query_memory"
    assert res2.captured is False
    
    # 3. "In ra các ghi nhớ về tôi" should be query_memory
    res3 = await service.handle_text(CaptureRequest(raw_text="In ra các ghi nhớ về tôi"))
    assert res3.intent == "query_memory"
    assert res3.captured is False
    
    # 4. Unknown vague text should not be captured
    res4 = await service.handle_text(CaptureRequest(raw_text="vớ vẩn linh tinh"))
    assert res4.intent in {"casual_or_noop", "needs_clarification"}
    assert res4.captured is False

    # Check that database notes count is 2 (only the 2 query queries were recorded as notes)
    notes = await repos["notes"].database.connection()
    row = await (await notes.execute("SELECT COUNT(*) as count FROM notes")).fetchone()
    assert row["count"] == 2

    # Verify no tasks or memory items were created
    active_tasks = await repos["tasks"].list_active()
    assert len(active_tasks) == 0
    active_memories = await repos["memory"].list_active()
    assert len(active_memories) == 0


async def test_classifier_exception_fallback_safety(capture_service, repos):
    # Setup a classifier that raises an exception
    class ExceptionalClassifier:
        async def classify(self, raw_text: str):
            raise RuntimeError("Classifier service down")
            
    service = _conversation_service(capture_service, repos, classifier=ExceptionalClassifier())
    
    # "Hôm nay trời đẹp" should not be captured, fallback must be safe
    res1 = await service.handle_text(CaptureRequest(raw_text="Hôm nay trời đẹp"))
    assert res1.intent == "casual_or_noop"
    assert res1.captured is False
    
    # unknown vague text should not be captured
    res2 = await service.handle_text(CaptureRequest(raw_text="làm gì đấy"))
    assert res2.intent == "needs_clarification"
    assert res2.captured is False
    
    # Check that database notes count is 0
    notes = await repos["notes"].database.connection()
    row = await (await notes.execute("SELECT COUNT(*) as count FROM notes")).fetchone()
    assert row["count"] == 0


async def test_low_confidence_classifier_write_demotion(capture_service, repos):
    # Setup classifier returning write intent but low confidence (e.g. 0.4)
    classifier = FakeIntentClassifierService({
        "học bài": IntentClassification(
            intent="capture_task",
            confidence=0.4
        )
    })
    service = _conversation_service(capture_service, repos, classifier=classifier)
    
    res = await service.handle_text(CaptureRequest(raw_text="học bài"))
    # Should demote to needs_clarification
    assert res.intent == "needs_clarification"
    assert res.captured is False


async def test_ambiguous_classifier_write_demotion(capture_service, repos):
    # Setup classifier returning write intent but ambiguity_detected is True
    classifier = FakeIntentClassifierService({
        "đã làm": IntentClassification(
            intent="mark_task_done",
            confidence=0.9,
            ambiguity_detected=True
        )
    })
    service = _conversation_service(capture_service, repos, classifier=classifier)
    
    res = await service.handle_text(CaptureRequest(raw_text="đã làm"))
    # Should demote to needs_clarification
    assert res.intent == "needs_clarification"
    assert res.captured is False


def test_intent_schema_accepts_all_runtime_v2_intents():
    runtime_intents = {
        "query_today",
        "query_tomorrow",
        "query_memory",
        "query_tasks",
        "query_tasks_due",
        "query_reminders",
        "query_projects",
        "capture_task",
        "capture_reminder",
        "capture_memory",
        "update_task",
        "update_task_due",
        "mark_task_done",
        "delete_all_tasks",
        "memory_delete",
        "memory_correction",
        "correction_feedback",
        "clarification_answer",
        "casual_or_noop",
        "needs_clarification",
    }

    for intent in runtime_intents:
        parsed = IntentClassification(intent=intent, confidence=0.9)
        assert parsed.intent == intent


async def test_classifier_project_query_is_executed_without_extraction(capture_service, fake_provider, repos):
    note = await repos["notes"].create(Note(raw_text="project setup"))
    project = await repos["projects"].find_or_create("MemoCore")
    await repos["tasks"].create(
        Task(
            title="Ship V2 conversation hardening",
            source_note_id=note.id,
            project_id=project.id,
        )
    )
    raw_text = "what is still open in project MemoCore?"
    classifier = FakeIntentClassifierService({
        raw_text: IntentClassification(intent="query_projects", confidence=0.95)
    })
    service = _conversation_service(capture_service, repos, classifier=classifier)

    result = await service.handle_text(CaptureRequest(raw_text=raw_text))

    assert result.intent == "query_projects"
    assert "Ship V2 conversation hardening" in result.reply
    assert fake_provider.calls == []


async def test_conversation_created_clarifications_are_audited(capture_service, repos):
    note = await repos["notes"].create(Note(raw_text="setup"))
    await repos["tasks"].create(Task(title="Prepare Aptech lesson", source_note_id=note.id))
    await repos["tasks"].create(Task(title="Prepare MindX lesson", source_note_id=note.id))
    capture_service.clarification_service.task_repo = repos["tasks"]
    raw_text = "change prepare lesson to 17h"
    classifier = FakeIntentClassifierService({
        raw_text: IntentClassification(
            intent="update_task_due",
            confidence=0.95,
            target_entity_hints="prepare lesson",
        )
    })
    service = _conversation_service(capture_service, repos, classifier=classifier)

    result = await service.handle_text(CaptureRequest(raw_text=raw_text, source_chat_id="audit-chat"))

    assert result.intent == "update_task_due"
    pending = await capture_service.clarification_service.find_pending_for_chat("audit-chat")
    assert pending is not None
    events = await repos["events"].list_by_entity("clarification_request", pending.id)
    assert any(event.event_type == EventType.CLARIFICATION_REQUESTED for event in events)
