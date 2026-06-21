import json

from memocore.adapters.llm.base import (
    ChatResponse,
    ProviderInfo,
    StructuredOutputMode,
)
from memocore.domain.models import (
    FollowUp,
    MemoryBucket,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    Note,
    Person,
    Task,
)
from memocore.domain.schemas import KnowledgeQueryPlan
from memocore.services.knowledge_query_service import KnowledgeQueryService


class ScriptedQueryProvider:
    def __init__(self, plan: KnowledgeQueryPlan, answer: str):
        self.plan = plan
        self.answer = answer
        self.calls = []

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo("fake", "fake-model", StructuredOutputMode.JSON_MODE)

    async def health_check(self) -> bool:
        return True

    async def chat(self, request):
        self.calls.append(request)
        if len(self.calls) == 1:
            return ChatResponse(
                content=json.dumps(self.plan.model_dump(), ensure_ascii=False),
                model="fake-model",
            )
        return ChatResponse(content=self.answer, model="fake-model")


def _service(repos, provider) -> KnowledgeQueryService:
    return KnowledgeQueryService(
        provider,
        repos["memory"],
        repos["projects"],
        repos["people"],
        repos["tasks"],
        repos["followups"],
        repos["commitments"],
        repos["meetings"],
        repos["reminders"],
    )


async def _memory(repos, note, content, project_id=None):
    return await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROJECT if project_id else MemoryBucket.PROFILE,
            kind=MemoryKind.PROJECT_STATE if project_id else MemoryKind.FACT,
            content=content,
            source_note_id=note.id,
            project_id=project_id,
            confidence=0.9,
            status=MemoryStatus.ACTIVE,
        )
    )


async def test_knowledge_query_retrieves_ste_ai_evidence(repos):
    note = await repos["notes"].create(Note(raw_text="seed"))
    ste = await repos["projects"].find_or_create("STE")
    await _memory(
        repos,
        note,
        "STE đang phát triển AI agent và tự động hóa quy trình vận hành.",
        ste.id,
    )
    await _memory(repos, note, "MindX có đội ngũ TEGL tại các khu vực.")
    provider = ScriptedQueryProvider(
        KnowledgeQueryPlan(
            entities=["STE"],
            topics=["AI", "đang phát triển"],
            record_types=["memory", "project", "task", "followup"],
        ),
        "STE đang phát triển AI agent.",
    )

    answer = await _service(repos, provider).answer("STE đang build AI gì?")

    assert answer == "STE đang phát triển AI agent."
    composer_prompt = provider.calls[1].messages[-1].content
    assert "AI agent" in composer_prompt
    assert "đội ngũ TEGL" not in composer_prompt


async def test_knowledge_query_answers_mindx_and_tegl_from_memory(repos):
    note = await repos["notes"].create(Note(raw_text="seed"))
    await _memory(
        repos,
        note,
        "Tại MindX, Phan Ngọc Hoàng Anh là TEGL phụ trách HCM 1 và HCM 4.",
    )
    provider = ScriptedQueryProvider(
        KnowledgeQueryPlan(
            entities=["MindX", "HCM 1", "HCM 4"],
            topics=["TEGL", "nhân sự"],
            record_types=["memory", "person", "project"],
        ),
        "TEGL phụ trách HCM 1 và HCM 4 là Phan Ngọc Hoàng Anh.",
    )

    answer = await _service(repos, provider).answer("TEGL của HCM 1 và 4 là ai")

    assert "Phan Ngọc Hoàng Anh" in answer
    assert "Phan Ngọc Hoàng Anh" in provider.calls[1].messages[-1].content


async def test_knowledge_query_filters_unrelated_tasks_for_ste_followup(repos):
    note = await repos["notes"].create(Note(raw_text="seed"))
    ste = await repos["projects"].find_or_create("STE")
    await repos["tasks"].create(Task(title="Call dưa hấu", source_note_id=note.id))
    await repos["followups"].create(
        FollowUp(
            title="Xác nhận phạm vi AI automation",
            source_note_id=note.id,
            project_id=ste.id,
        )
    )
    provider = ScriptedQueryProvider(
        KnowledgeQueryPlan(
            entities=["STE"],
            topics=["follow việc"],
            record_types=["task", "followup", "commitment"],
            answer_style="list",
        ),
        "Bạn đang follow việc xác nhận phạm vi AI automation của STE.",
    )

    answer = await _service(repos, provider).answer(
        "tôi có đang follow việc gì ở STE không"
    )

    assert "AI automation" in answer
    composer_prompt = provider.calls[1].messages[-1].content
    assert "Xác nhận phạm vi AI automation" in composer_prompt
    assert "Call dưa hấu" not in composer_prompt


async def test_self_identity_query_does_not_match_ste_ai(repos):
    note = await repos["notes"].create(Note(raw_text="seed"))
    ste = await repos["projects"].find_or_create("STE")
    await _memory(
        repos,
        note,
        "STE đang phát triển AI agent và tự động hóa quy trình vận hành.",
        ste.id,
    )
    await _memory(
        repos,
        note,
        "Vũ muốn trợ lý phân biệt rõ MindX và STE khi lưu và truy xuất memory.",
    )
    provider = ScriptedQueryProvider(
        KnowledgeQueryPlan(entities=["STE"], topics=["AI"], record_types=["memory"]),
        "wrong",
    )

    answer = await _service(repos, provider).answer("tôi là ai")

    assert "Anh là Vũ" in answer
    assert "STE đang phát triển AI agent" not in answer
    assert provider.calls == []


async def test_fallback_compose_does_not_dump_raw_metadata(repos):
    class FailingComposeProvider(ScriptedQueryProvider):
        async def chat(self, request):
            self.calls.append(request)
            if len(self.calls) == 1:
                return ChatResponse(
                    content=json.dumps(self.plan.model_dump(), ensure_ascii=False),
                    model="fake-model",
                )
            raise Exception("compose failed")

    note = await repos["notes"].create(Note(raw_text="seed"))
    ste = await repos["projects"].find_or_create("STE")
    await _memory(
        repos,
        note,
        "STE đang phát triển AI agent và tự động hóa quy trình vận hành.",
        ste.id,
    )
    provider = FailingComposeProvider(
        KnowledgeQueryPlan(entities=["STE"], topics=["AI"], record_types=["memory", "project"]),
        "unused",
    )

    answer = await _service(repos, provider).answer("STE đang build AI gì")

    assert "Em thấy" in answer
    assert "Chủ đề" not in answer
    assert "memory_items" not in answer


async def test_person_identity_query_uses_people_before_project_memory(repos):
    note = await repos["notes"].create(Note(raw_text="seed"))
    ste = await repos["projects"].find_or_create("STE")
    await repos["people"].create(
        Person(
            display_name="Phan Ngọc Hoàng Anh",
            aliases=["Hoàng Anh"],
            relationship="mindx_tegl_plus_direct_and_ste_collaborator",
            notes="MindX: TEGL HCM 1 & HCM 4. STE: high-trust technical/product collaborator.",
        )
    )
    await _memory(
        repos,
        note,
        "STE đang phát triển AI agent và tự động hóa quy trình vận hành.",
        ste.id,
    )
    provider = ScriptedQueryProvider(
        KnowledgeQueryPlan(entities=["STE"], topics=["AI"], record_types=["memory"]),
        "wrong",
    )

    answer = await _service(repos, provider).answer("Hoàng Anh là ai")

    assert "Phan Ngọc Hoàng Anh" in answer
    assert "TEGL HCM 1" in answer
    assert "Vai trò chính" in answer
    assert "mindx_tegl_plus" not in answer
    assert "under Vu" not in answer
    assert "Keep contexts separate" not in answer
    assert "STE đang phát triển AI agent" not in answer
    assert provider.calls == []
