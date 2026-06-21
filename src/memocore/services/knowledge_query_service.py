from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
import unicodedata

from pydantic import ValidationError

from memocore.adapters.llm.base import (
    ChatMessage,
    ChatRequest,
    ExtractionError,
    ModelProvider,
    StructuredOutputMode,
)
from memocore.adapters.storage.repositories import (
    CommitmentRepository,
    FollowUpRepository,
    MeetingRepository,
    MemoryItemRepository,
    PersonRepository,
    ProjectRepository,
    ReminderRepository,
    TaskRepository,
)
from memocore.adapters.storage.knowledge_repositories import (
    DecisionRepository,
    OrganizationRepository,
)
from memocore.domain.models import MemoryKind
from memocore.domain.schemas import KnowledgeQueryPlan


@dataclass(frozen=True)
class KnowledgeEvidence:
    record_type: str
    record_id: str
    text: str
    entity_names: tuple[str, ...] = ()
    confidence: float = 1.0
    occurred_at: datetime | None = None
    related_entity_ids: tuple[str, ...] = ()


class KnowledgeQueryService:
    def __init__(
        self,
        provider: ModelProvider,
        memory_repo: MemoryItemRepository,
        project_repo: ProjectRepository,
        person_repo: PersonRepository,
        task_repo: TaskRepository,
        followup_repo: FollowUpRepository,
        commitment_repo: CommitmentRepository,
        meeting_repo: MeetingRepository,
        reminder_repo: ReminderRepository,
        organization_repo: OrganizationRepository | None = None,
        decision_repo: DecisionRepository | None = None,
    ):
        self.provider = provider
        self.memory_repo = memory_repo
        self.project_repo = project_repo
        self.person_repo = person_repo
        self.task_repo = task_repo
        self.followup_repo = followup_repo
        self.commitment_repo = commitment_repo
        self.meeting_repo = meeting_repo
        self.reminder_repo = reminder_repo
        self.organization_repo = organization_repo
        self.decision_repo = decision_repo

    async def answer(
        self,
        raw_text: str,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
        entity_name: str | None = None,
    ) -> str:
        if _is_self_identity_query(raw_text):
            return await self._answer_self_identity()
        person_answer = await self._answer_person_identity(raw_text)
        if person_answer is not None:
            return person_answer
        plan = await self._plan(raw_text)
        if entity_name and entity_name not in plan.entities:
            plan.entities.insert(0, entity_name)
        evidence = await self._retrieve(
            raw_text,
            plan,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        if not evidence:
            return _empty_answer(plan)
        return await self._compose(raw_text, plan, evidence)

    async def _plan(self, raw_text: str) -> KnowledgeQueryPlan:
        prompt = (
            "Phân tích câu hỏi thành kế hoạch truy xuất dữ liệu cho trợ lý cá nhân.\n"
            "- entities: tên tổ chức, dự án, người, khu vực hoặc sản phẩm được nhắc tới.\n"
            "- topics: chủ đề cần hỏi, bỏ các từ hỏi chung như 'nói về', 'là ai', 'của tôi'.\n"
            "- record_types: loại dữ liệu cần tra. Có thể chọn nhiều loại.\n"
            "- follow/follow việc nên tra task, followup và commitment.\n"
            "- câu hỏi về lịch nên tra meeting, reminder và task.\n"
            "- câu hỏi kiến thức tổ chức/nhân sự nên tra memory, person và project.\n"
            "- câu hỏi về dự án đang xây gì nên tra memory, project, task và followup.\n"
            "Trả về JSON đúng schema, không giải thích.\n\n"
            f"Câu hỏi: {raw_text}"
        )
        mode = self.provider.info.supports_structured_output
        request = ChatRequest(
            messages=[
                ChatMessage(
                    role="system",
                    content="Bạn là query planner cho hệ thống truy xuất dữ liệu cá nhân.",
                ),
                ChatMessage(role="user", content=prompt),
            ],
            temperature=0.0,
            response_format=mode,
            json_schema=(
                KnowledgeQueryPlan.model_json_schema()
                if mode == StructuredOutputMode.JSON_SCHEMA
                else None
            ),
            max_tokens=450,
        )
        try:
            response = await self.provider.chat(request)
            return KnowledgeQueryPlan.model_validate(_decode_json(response.content))
        except (ExtractionError, ValidationError, json.JSONDecodeError, TypeError):
            return _fallback_plan(raw_text)

    async def _retrieve(
        self,
        raw_text: str,
        plan: KnowledgeQueryPlan,
        limit: int = 14,
        *,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ) -> list[KnowledgeEvidence]:
        evidence = await self._all_evidence()
        query_tokens = _meaningful_tokens(raw_text)
        entity_tokens = _meaningful_tokens(" ".join(plan.entities))
        topic_tokens = _meaningful_tokens(" ".join(plan.topics))
        requested_types = set(plan.record_types)
        broad_entity_query = not topic_tokens or topic_tokens <= entity_tokens
        scored: list[tuple[float, KnowledgeEvidence]] = []
        for item in evidence:
            if entity_id and not (
                (item.record_type == entity_type and item.record_id == entity_id)
                or entity_id in item.related_entity_ids
            ):
                continue
            if requested_types and item.record_type not in requested_types:
                continue
            normalized_text = _normalize(" ".join((*item.entity_names, item.text)))
            text_tokens = set(normalized_text.split())
            entity_score = _token_overlap(entity_tokens, text_tokens)
            topic_score = _token_overlap(topic_tokens, text_tokens)
            query_score = _token_overlap(query_tokens, text_tokens)
            phrase_bonus = sum(
                2.5 for entity in plan.entities if _normalize(entity) in normalized_text
            )
            exact_entity_bonus = sum(
                8.0
                for entity in plan.entities
                if any(
                    _normalize(entity) == _normalize(entity_name)
                    for entity_name in item.entity_names
                )
            )
            if entity_tokens and entity_score == 0 and phrase_bonus == 0:
                continue
            score = (
                entity_score * 4.0
                + topic_score * 3.0
                + query_score
                + phrase_bonus
                + exact_entity_bonus
                + (3.0 if broad_entity_query and item.record_type == "project" else 0.0)
                + item.confidence
            )
            if score >= 1.5:
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    async def _all_evidence(self) -> list[KnowledgeEvidence]:
        projects = await self.project_repo.list_all()
        people = await self.person_repo.list_all()
        project_names = {project.id: project.name for project in projects}
        person_names = {person.id: person.display_name for person in people}
        result: list[KnowledgeEvidence] = []

        organization_names: dict[str, str] = {}
        if self.organization_repo is not None:
            for organization in await self.organization_repo.list_all():
                organization_names[organization.id] = organization.name
                result.append(
                    KnowledgeEvidence(
                        "organization",
                        organization.id,
                        f"{organization.name}. {organization.summary}".strip(),
                        (organization.name, *organization.aliases),
                        occurred_at=organization.updated_at,
                        related_entity_ids=(organization.id,),
                    )
                )
        if self.decision_repo is not None:
            for decision in await self.decision_repo.list_all():
                entities = tuple(
                    value
                    for value in (
                        project_names.get(decision.project_id or ""),
                        person_names.get(decision.person_id or ""),
                        organization_names.get(decision.organization_id or ""),
                    )
                    if value
                )
                result.append(
                    KnowledgeEvidence(
                        "decision",
                        decision.id,
                        f"{decision.title}. {decision.summary}".strip(),
                        entities,
                        confidence=decision.confidence,
                        occurred_at=decision.decided_at,
                        related_entity_ids=tuple(
                            value
                            for value in (
                                decision.project_id,
                                decision.person_id,
                                decision.organization_id,
                            )
                            if value
                        ),
                    )
                )

        for project in projects:
            result.append(
                KnowledgeEvidence(
                    "project",
                    project.id,
                    f"{project.name}. {project.summary}".strip(),
                    (project.name,),
                    occurred_at=project.updated_at,
                    related_entity_ids=(project.id,),
                )
            )
        for person in people:
            result.append(
                KnowledgeEvidence(
                    "person",
                    person.id,
                    f"{person.display_name}. {person.relationship}. {person.notes}".strip(),
                    (person.display_name, *person.aliases),
                    occurred_at=person.updated_at,
                    related_entity_ids=(person.id,),
                )
            )
        for item in await self.memory_repo.list_active():
            if str(item.kind) == MemoryKind.CORRECTION.value:
                continue
            entities = tuple(
                value
                for value in (
                    project_names.get(item.project_id or ""),
                    person_names.get(item.person_id or ""),
                    organization_names.get(item.organization_id or ""),
                )
                if value
            )
            result.append(
                KnowledgeEvidence(
                    "memory",
                    item.id,
                    item.content,
                    entities,
                    confidence=item.confidence,
                    occurred_at=item.updated_at,
                    related_entity_ids=tuple(
                        value
                        for value in (
                            item.project_id,
                            item.person_id,
                            item.organization_id,
                            item.decision_id,
                        )
                        if value
                    ),
                )
            )
        for task in await self.task_repo.list_active():
            entities = _linked_names(task.project_id, task.person_id, project_names, person_names)
            result.append(
                KnowledgeEvidence(
                    "task",
                    task.id,
                    f"{task.title}. {task.description}. Trạng thái: {task.status}. Hạn: {task.due_at}",
                    entities,
                    confidence=task.confidence,
                    occurred_at=task.due_at or task.updated_at,
                    related_entity_ids=tuple(
                        value
                        for value in (task.project_id, task.person_id)
                        if value
                    ),
                )
            )
        for followup in await self.followup_repo.list_open():
            entities = _linked_names(
                followup.project_id, followup.person_id, project_names, person_names
            )
            result.append(
                KnowledgeEvidence(
                    "followup",
                    followup.id,
                    f"{followup.title}. {followup.notes}. Hạn: {followup.due_at}",
                    entities,
                    occurred_at=followup.due_at or followup.updated_at,
                    related_entity_ids=tuple(
                        value
                        for value in (followup.project_id, followup.person_id)
                        if value
                    ),
                )
            )
        for commitment in await self.commitment_repo.list_open():
            entities = _linked_names(
                commitment.project_id, commitment.person_id, project_names, person_names
            )
            result.append(
                KnowledgeEvidence(
                    "commitment",
                    commitment.id,
                    (
                        f"{commitment.title}. {commitment.notes}. "
                        f"Chiều cam kết: {commitment.direction}. Hạn: {commitment.due_at}"
                    ),
                    entities,
                    occurred_at=commitment.due_at or commitment.updated_at,
                    related_entity_ids=tuple(
                        value
                        for value in (
                            commitment.project_id,
                            commitment.person_id,
                        )
                        if value
                    ),
                )
            )
        for meeting in await self.meeting_repo.list_all():
            entities = _linked_names(
                meeting.project_id, meeting.person_id, project_names, person_names
            )
            result.append(
                KnowledgeEvidence(
                    "meeting",
                    meeting.id,
                    f"{meeting.title}. {meeting.notes}. Bắt đầu: {meeting.starts_at}",
                    entities,
                    occurred_at=meeting.starts_at or meeting.updated_at,
                    related_entity_ids=tuple(
                        value
                        for value in (meeting.project_id, meeting.person_id)
                        if value
                    ),
                )
            )
        for reminder in await self.reminder_repo.list_recent(limit=100):
            result.append(
                KnowledgeEvidence(
                    "reminder",
                    reminder.id,
                    f"{reminder.title}. Trạng thái: {reminder.status}. Lúc: {reminder.remind_at}",
                    occurred_at=reminder.remind_at or reminder.updated_at,
                )
            )
        return result

    async def _compose(
        self,
        raw_text: str,
        plan: KnowledgeQueryPlan,
        evidence: list[KnowledgeEvidence],
    ) -> str:
        evidence_text = "\n".join(
            f"[{index}] ({item.record_type}) {item.text}"
            for index, item in enumerate(evidence, 1)
        )
        prompt = (
            "Trả lời câu hỏi của Vũ chỉ bằng các bằng chứng được cung cấp.\n"
            "- Trả lời trực tiếp đúng điều được hỏi, không in dashboard chung.\n"
            "- Đặt góc nhìn là trợ lý cá nhân của Vũ: trợ lý xưng 'em' và gọi Vũ là 'anh'.\n"
            "- Dùng sắc thái miền Nam tự nhiên: có thể dùng 'dạ' khi xác nhận và 'nha' khi làm mềm đề nghị; tuyệt đối không dùng từ 'nhé'.\n"
            "- Không nhồi tiểu từ vào mọi câu; ưu tiên ấm áp, gọn và trực tiếp.\n"
            "- Dùng tiếng Việt tự nhiên, rõ ràng, chuyên nghiệp; tránh giọng hệ thống hoặc báo cáo database.\n"
            "- Tự sửa lỗi gõ hiển nhiên trong câu hỏi; không lặp lại từ bị gõ sai.\n"
            "- Không mở đầu bằng cách nhắc lại nguyên văn câu hỏi; dùng tên entity chuẩn trong kế hoạch.\n"
            "- Với câu hỏi tổng quan, chỉ tóm tắt vai trò, cấu trúc và mảng chính; "
            "không liệt kê toàn bộ người hoặc bản ghi nếu người dùng không hỏi danh sách.\n"
            "- Phân biệt dữ kiện đã xác nhận với hướng tiềm năng/chưa xác nhận.\n"
            "- Với dữ kiện chưa chắc, nói bằng ngôn ngữ tự nhiên như 'chưa đủ chắc', "
            "'cần xác nhận', hoặc 'hiện chỉ nên xem là ý tưởng'; không đọc raw confidence trừ khi được hỏi.\n"
            "- Nếu hỏi ai, nêu tên và vai trò. Nếu hỏi đang làm/build gì, nhóm theo chủ đề.\n"
            "- Nếu hỏi việc đang follow, chỉ liệt kê task/follow-up/commitment phù hợp entity.\n"
            "- Nếu bằng chứng chỉ là memory định hướng, không nói rằng đó là task đang mở.\n"
            "- Không dùng thuật ngữ backend như memory_items, status, bucket, record, retrieval, hoặc database trong câu trả lời thường.\n"
            "- Không bịa và không nhắc đến quy trình truy xuất.\n"
            "- Câu trả lời nên ngắn gọn, tối đa khoảng 12 dòng.\n\n"
            f"Câu hỏi: {raw_text}\n"
            f"Kế hoạch: {plan.model_dump_json()}\n\n"
            f"Bằng chứng:\n{evidence_text}"
        )
        try:
            response = await self.provider.chat(
                ChatRequest(
                    messages=[
                        ChatMessage(
                            role="system",
                            content=(
                                "Em là trợ lý cá nhân của anh Vũ. Trả lời bằng tiếng Việt tự nhiên, "
                                "ngắn gọn, đúng bằng chứng, mang sắc thái miền Nam; dùng 'dạ'/'nha' "
                                "vừa phải và không dùng từ 'nhé'."
                            ),
                        ),
                        ChatMessage(role="user", content=prompt),
                    ],
                    temperature=0.0,
                    max_tokens=700,
                )
            )
            if response.content.strip():
                return response.content.strip()
        except Exception:
            pass
        return _fallback_compose(raw_text, evidence)

    async def _answer_self_identity(self) -> str:
        memories = [
            item
            for item in await self.memory_repo.list_active()
            if str(item.bucket) == "profile" and str(item.kind) != MemoryKind.CORRECTION.value
        ]
        if not memories:
            return "Em chưa có đủ hồ sơ cá nhân canonical về anh. Hiện em chỉ nên trả lời khi có thêm memory profile rõ hơn."
        return (
            "Anh là Vũ. Những gì em đang nhớ chắc nhất là:\n"
            "- Anh có bối cảnh công việc tại MindX.\n"
            "- Anh sáng lập/vận hành STE và muốn tách rõ STE khỏi MindX.\n"
            "- Anh quan tâm đến dữ liệu, AI, giáo dục, hệ thống vận hành và cách quản trị memory có nguồn rõ.\n"
            "- Các nhận định về tính cách, phong cách huấn luyện hoặc định vị nghề nghiệp chỉ nên xem là inference nếu chưa được anh xác nhận."
        )

    async def _answer_person_identity(self, raw_text: str) -> str | None:
        query = _person_identity_query(raw_text)
        if not query:
            return None
        matches = await self.person_repo.find_matches(query)
        if not matches:
            return None
        if len(matches) > 1:
            names = "\n".join(f"- {person.display_name}" for person in matches)
            return f"Em thấy nhiều người cùng khớp. Anh chọn tên đầy đủ giúp em:\n{names}"
        person = matches[0]
        lines = [f"{person.display_name} là người liên quan đến bối cảnh công việc của anh."]
        summary = _relationship_summary(person.relationship)
        if summary:
            lines.append(f"- Vai trò chính: {summary}")
        lines.extend(_person_note_lines(person.notes))
        return "\n".join(lines)


def _fallback_plan(raw_text: str) -> KnowledgeQueryPlan:
    normalized = _normalize(raw_text)
    record_types: list[str] = ["memory", "project", "person"]
    entities = _known_entities(raw_text)
    if any(token in normalized for token in ("follow", "theo doi", "dang cho", "cam ket")):
        record_types = ["task", "followup", "commitment", "memory"]
    elif any(token in normalized for token in ("lich", "hop", "meeting", "nhac")):
        record_types = ["meeting", "reminder", "task"]
    elif any(token in normalized for token in ("task", "viec", "can lam")):
        record_types = ["task", "followup", "commitment"]
    return KnowledgeQueryPlan(
        entities=entities,
        topics=list(_meaningful_tokens_without_entities(raw_text, entities)),
        record_types=record_types,
        answer_style="direct",
    )


def _fallback_compose(raw_text: str, evidence: list[KnowledgeEvidence]) -> str:
    normalized = _normalize(raw_text)
    clean = [_clean_evidence_text(item.text) for item in evidence if _clean_evidence_text(item.text)]
    if not clean:
        return "Em chưa tìm thấy dữ liệu đủ rõ để trả lời câu này."
    if any(token in normalized for token in ("cu the", "chi tiet", "build", "xay", "phat trien")):
        bullets = "\n".join(f"- {line}" for line in clean[:5])
        return f"Em thấy các ý cụ thể nhất là:\n{bullets}"
    bullets = "\n".join(f"- {line}" for line in clean[:4])
    return f"Em đang nhớ các ý chính sau:\n{bullets}"


def _clean_evidence_text(text: str) -> str:
    text = re.sub(r"\s*Chu de:\s*.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*Chủ đề:\s*.*$", "", text, flags=re.IGNORECASE)
    return " ".join(text.split()).strip(" .")


def _empty_answer(plan: KnowledgeQueryPlan) -> str:
    entities = ", ".join(plan.entities)
    entity_suffix = f" liên quan đến {entities}" if entities else ""
    requested = set(plan.record_types)
    if requested and requested <= {"task", "followup", "commitment"}:
        return (
            f"Hiện em chưa thấy task, follow-up hoặc commitment đang mở{entity_suffix}. "
            "Các memory mô tả định hướng hoặc năng lực không được tính là việc đang follow."
        )
    if requested and requested <= {"meeting", "reminder", "task"}:
        return f"Hiện em chưa thấy lịch, reminder hoặc task phù hợp{entity_suffix}."
    return (
        "Em chưa tìm thấy dữ liệu đủ liên quan để trả lời câu hỏi này. "
        "Anh có thể bổ sung tên dự án, người hoặc chủ đề cụ thể hơn."
    )


def _decode_json(content: str) -> object:
    try:
        return json.loads(content)
    except json.JSONDecodeError as original:
        decoder = json.JSONDecoder()
        for index, char in enumerate(content):
            if char != "{":
                continue
            try:
                decoded, _ = decoder.raw_decode(content[index:])
                return decoded
            except json.JSONDecodeError:
                continue
        raise original


def _linked_names(
    project_id: str | None,
    person_id: str | None,
    project_names: dict[str, str],
    person_names: dict[str, str],
) -> tuple[str, ...]:
    return tuple(
        value
        for value in (
            project_names.get(project_id or ""),
            person_names.get(person_id or ""),
        )
        if value
    )


def _normalize(value: str) -> str:
    lowered = value.lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    ascii_text = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text))


def _meaningful_tokens(value: str) -> set[str]:
    stopwords = {
        "anh",
        "ban",
        "cua",
        "dang",
        "gi",
        "la",
        "noi",
        "toi",
        "ve",
        "co",
        "khong",
        "cho",
        "nhung",
        "nao",
        "minh",
    }
    return {token for token in _normalize(value).split() if token not in stopwords}


def _known_entities(value: str) -> list[str]:
    normalized = _normalize(value)
    entities = []
    if re.search(r"\bste\b", normalized):
        entities.append("STE")
    if re.search(r"\bmindx\b", normalized):
        entities.append("MindX")
    return entities


def _meaningful_tokens_without_entities(value: str, entities: list[str]) -> set[str]:
    tokens = _meaningful_tokens(value)
    for entity in entities:
        tokens -= set(_normalize(entity).split())
    return tokens


def _is_self_identity_query(value: str) -> bool:
    normalized = _normalize(value)
    return normalized in {
        "toi la ai",
        "ban biet toi la ai khong",
        "ban nho toi la ai khong",
    }


def _person_identity_query(value: str) -> str | None:
    normalized = _normalize(value)
    match = re.fullmatch(r"(.+?) la ai", normalized)
    if not match:
        return None
    query = match.group(1).strip()
    if query in {"toi", "ban", "memocore", "memo core"}:
        return None
    return query or None


def _relationship_summary(value: str) -> str:
    labels = {
        "mindx_tegl_plus_direct": "TEGL+ trực tiếp trong nhánh MindX của anh.",
        "mindx_tegl_plus_direct_and_ste_collaborator": "TEGL+ trực tiếp tại MindX và cộng tác viên kỹ thuật/sản phẩm tin cậy trong bối cảnh STE.",
        "mindx_tom_direct_and_ste_collaborator": "nhân sự trực tiếp trong nhánh TOM tại MindX và cộng tác viên thực thi quan trọng của STE.",
        "mindx_tom_layer2_and_ste_support": "nhân sự lớp dưới TOM tại MindX; có tín hiệu hỗ trợ vận hành/project trong STE nhưng vai trò STE cần xác nhận.",
        "mindx_success_ss_under_hieu": "thuộc nhóm Success/SS và báo cáo cho Nguyễn Trung Hiếu, không thuộc nhánh TEGL+/TOM của anh.",
        "mindx_direct_manager": "quản lý trực tiếp của anh tại MindX.",
        "mindx_cross_functional_counterpart": "đối tác phối hợp liên phòng ban tại MindX, không phải direct report của anh.",
        "ste_collaborator_historical_mindx": "cộng tác viên STE; từng có bối cảnh MindX lịch sử.",
        "ste_external_project_reference": "người liên quan đến project/tham chiếu bên ngoài của STE.",
    }
    if value in labels:
        return labels[value]
    return value.replace("_", " ") if value else ""


def _person_note_lines(notes: str) -> list[str]:
    if not notes:
        return []
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", notes) if part.strip()]
    lines: list[str] = []
    for sentence in sentences:
        line = _translate_person_note(sentence)
        if line:
            lines.append(f"- {line}")
    return lines


def _translate_person_note(note: str) -> str:
    normalized = note.lower()
    replacements = (
        ("mindx: tegl hcm 2 & hcm 3 under vu's tegl+ role.", "MindX: phụ trách TEGL HCM 2 và HCM 3 trong nhánh TEGL+ của anh."),
        ("mindx: tegl hcm 1 & hcm 4 under vu's tegl+ role.", "MindX: phụ trách TEGL HCM 1 và HCM 4 trong nhánh TEGL+ của anh."),
        ("mindx: leader team ho / teaching development leader under vu's tom role.", "MindX: thuộc team HO/Teaching Development Leader trong nhánh TOM của anh."),
        ("ste: major execution collaborator.", "STE: cộng tác viên thực thi quan trọng."),
        ("ste: high-trust technical/product collaborator.", "STE: cộng tác viên kỹ thuật/sản phẩm có độ tin cậy cao."),
        ("keep contexts separate.", "Cần tách rõ bối cảnh MindX và STE khi dùng thông tin này."),
        ("mindx:", "MindX:"),
        ("ste:", "STE:"),
        ("under vu's", "trong nhánh của anh"),
        ("not vu's direct report", "không phải direct report của anh"),
        ("direct manager of vu at mindx", "quản lý trực tiếp của anh tại MindX"),
    )
    for source, target in replacements:
        if normalized == source:
            return target
    cleaned = note.replace("Vu's", "của anh").replace("Vu", "anh")
    cleaned = cleaned.replace("under", "thuộc").replace("Current", "Hiện là")
    return cleaned.strip()


def _token_overlap(query_tokens: set[str], text_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    return len(query_tokens & text_tokens) / len(query_tokens)
