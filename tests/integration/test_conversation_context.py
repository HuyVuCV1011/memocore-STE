from datetime import UTC, datetime

from memocore.domain.models import MemoryBucket, MemoryItem, MemoryKind, Note, Person, Task
from memocore.domain.schemas import CaptureRequest, KnowledgeQueryPlan
from memocore.services.conversation_service import ConversationService
from memocore.services.knowledge_query_service import KnowledgeQueryService
from memocore.services.reference_resolver import ReferenceResolver
from memocore.services.secretary_service import SecretaryService


class EntityAwareKnowledge:
    def __init__(self):
        self.calls = []

    async def answer(self, raw_text, **kwargs):
        self.calls.append((raw_text, kwargs))
        return f"entity={kwargs.get('entity_type')}:{kwargs.get('entity_id')}"


def _service(capture_service, repos, knowledge):
    secretary = SecretaryService(
        repos["tasks"],
        repos["reminders"],
        repos["followups"],
        repos["projects"],
        repos["memory"],
        person_repo=repos["people"],
    )
    resolver = ReferenceResolver(
        repos["chat_contexts"],
        repos["projects"],
        repos["people"],
        repos["tasks"],
    )
    return ConversationService(
        capture_service,
        secretary,
        repos["notes"],
        repos["tasks"],
        capture_service.memory_service,
        capture_service.event_service,
        knowledge_query_service=knowledge,
        reference_resolver=resolver,
    )


async def test_project_followup_resolves_canonical_entity_id(
    capture_service, repos
):
    note = await repos["notes"].create(Note(raw_text="MemoCore task"))
    project = await repos["projects"].find_or_create("MemoCore")
    await repos["tasks"].create(
        Task(
            title="Hoàn thiện ver 4.0 của memocore",
            project_id=project.id,
            source_note_id=note.id,
        )
    )
    knowledge = EntityAwareKnowledge()
    service = _service(capture_service, repos, knowledge)

    first = await service.handle_text(
        CaptureRequest(
            raw_text="Hoàn thiện ver 4.0 của memocore liên quan dự án gì?",
            source_chat_id="chat-context",
            source_message_id="1",
        )
    )
    second = await service.handle_text(
        CaptureRequest(
            raw_text="dự án đó đang như thế nào",
            source_chat_id="chat-context",
            source_message_id="2",
        )
    )
    context = await repos["chat_contexts"].get("chat-context")

    assert first.intent == "query_context"
    assert second.intent == "query_context"
    assert knowledge.calls[0][1]["entity_id"] == project.id
    assert knowledge.calls[1][1]["entity_id"] == project.id
    assert context is not None
    assert context.focused_entity_type == "project"
    assert context.focused_entity_id == project.id


async def test_entity_constrained_retrieval_excludes_other_projects(
    repos, fake_provider
):
    note = await repos["notes"].create(Note(raw_text="projects"))
    memocore = await repos["projects"].find_or_create("MemoCore")
    walmart = await repos["projects"].find_or_create("Lộc Walmart")
    await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROJECT,
            kind=MemoryKind.PROJECT_STATE,
            content="MemoCore đang hoàn thiện phiên bản 4.0.",
            project_id=memocore.id,
            source_note_id=note.id,
            status="active",
        )
    )
    await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROJECT,
            kind=MemoryKind.PROJECT_STATE,
            content="Dự án Lộc Walmart đang tìm nguồn cung ứng.",
            project_id=walmart.id,
            source_note_id=note.id,
            status="active",
        )
    )
    service = KnowledgeQueryService(
        fake_provider,
        repos["memory"],
        repos["projects"],
        repos["people"],
        repos["tasks"],
        repos["followups"],
        repos["commitments"],
        repos["meetings"],
        repos["reminders"],
    )
    plan = KnowledgeQueryPlan(
        entities=["MemoCore"],
        topics=["trạng thái"],
        record_types=["memory", "project"],
    )

    evidence = await service._retrieve(
        "dự án đó đang như thế nào",
        plan,
        entity_type="project",
        entity_id=memocore.id,
    )

    assert evidence
    assert all(
        item.record_id == memocore.id or memocore.id in item.related_entity_ids
        for item in evidence
    )
    assert all("Walmart" not in item.text for item in evidence)


async def test_conversation_turns_record_resolved_focus(capture_service, repos):
    project = await repos["projects"].find_or_create("MemoCore")
    knowledge = EntityAwareKnowledge()
    service = _service(capture_service, repos, knowledge)

    await service.handle_text(
        CaptureRequest(
            raw_text="MemoCore đang như thế nào?",
            source_chat_id="chat-turn",
            source_message_id="10",
        )
    )
    conn = await repos["chat_contexts"].database.connection()
    turn = await (
        await conn.execute(
            "SELECT * FROM conversation_turns WHERE source_chat_id = ?",
            ("chat-turn",),
        )
    ).fetchone()

    assert turn is not None
    assert turn["focused_entity_type"] == "project"
    assert turn["focused_entity_id"] == project.id
    assert turn["assistant_reply"]
    assert turn["plan_json"] is not None


async def test_project_overview_then_multiline_update_uses_conversation_focus(
    capture_service, repos, fake_provider
):
    project = await repos["projects"].find_or_create("MemoCore")
    person = await repos["people"].create(Person(display_name="Văn Nghĩa Trần"))
    knowledge = EntityAwareKnowledge()
    service = _service(capture_service, repos, knowledge)

    overview = await service.handle_text(
        CaptureRequest(
            raw_text="nói cho tôi biết về dự án MemoCore",
            source_chat_id="chat-memocore-update",
            source_message_id="101",
        )
    )
    update = await service.handle_text(
        CaptureRequest(
            raw_text=(
                "cập nhật thêm thông tin cho dự án này như sau nhé\n"
                "- đây là dự án xây dựng trợ lý cá nhân\n"
                "- trợ lý cá nhân nghĩa là có thể làm tất cả mọi công việc mà tôi giao trong tương lai\n"
                "- không cần code quá giỏi, nhưng cần đứng góc độ trợ lý thư ký cho tôi"
            ),
            source_chat_id="chat-memocore-update",
            source_message_id="102",
        )
    )

    memories = await repos["memory"].list_active_by_project(project.id)
    context = await repos["chat_contexts"].get("chat-memocore-update")

    assert overview.intent == "query_context"
    assert update.intent == "update_knowledge"
    assert update.captured is True
    assert update.reply == "Đã cập nhật 3 thông tin cho MemoCore."
    assert {item.content for item in memories} == {
        "không cần code quá giỏi, nhưng cần đứng góc độ trợ lý thư ký cho tôi",
        "trợ lý cá nhân nghĩa là có thể làm tất cả mọi công việc mà tôi giao trong tương lai",
        "đây là dự án xây dựng trợ lý cá nhân",
    }
    assert all(item.project_id == project.id for item in memories)
    assert all(item.person_id is None for item in memories)
    assert await repos["memory"].list_active_by_person(person.id) == []
    assert all(item.confidence == 1.0 for item in memories)
    assert context is not None
    assert context.focused_entity_id == project.id
    assert fake_provider.calls == []

    rollback = await service.handle_text(
        CaptureRequest(
            raw_text="xóa 3 thông tin đã cập nhật cho Văn Nghĩa Trần",
            source_chat_id="chat-memocore-update",
            source_message_id="103",
        )
    )

    assert rollback.intent == "rollback_knowledge_update"
    assert rollback.reply == "Đã xóa 3 thông tin từ lần cập nhật gần nhất."
    assert await repos["memory"].list_active_by_project(project.id) == []
    assert await repos["memory"].list_active_by_person(person.id) == []


async def test_bare_project_name_opens_scoped_context(capture_service, repos):
    project = await repos["projects"].find_or_create("MemoCore")
    knowledge = EntityAwareKnowledge()
    service = _service(capture_service, repos, knowledge)

    result = await service.handle_text(
        CaptureRequest(
            raw_text="MemoCore",
            source_chat_id="chat-bare-project",
            source_message_id="201",
        )
    )

    assert result.intent == "query_context"
    assert knowledge.calls[0][1]["entity_id"] == project.id
