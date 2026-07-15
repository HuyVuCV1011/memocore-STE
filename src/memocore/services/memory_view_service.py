from __future__ import annotations

import unicodedata
from collections import Counter
from datetime import UTC, datetime, timedelta

from memocore.adapters.storage.repositories import (
    MemoryItemRepository,
    NoteRepository,
    PersonRepository,
    ProjectRepository,
)
from memocore.domain.models import MemoryBucket, MemoryItem, MemoryKind
from memocore.domain.models import EventType, FeedbackSignal, MemoryStatus
from memocore.domain.schemas import AssistantAction, AssistantResponse, AssistantSection
from memocore.services.event_service import EventService


MEMORY_PAGE_SIZE = 4
_STALE_AFTER = timedelta(days=120)
MEMORY_TOPICS = (
    "review", "conflicts", "stale", "self", "goals", "people", "projects", "mindx", "ste"
)

TOPIC_LABELS = {
    "self": "Bản thân",
    "goals": "Mục tiêu",
    "mindx": "MindX",
    "ste": "STE",
    "people": "Con người",
    "projects": "Dự án",
    "review": "Cần xác nhận",
    "conflicts": "Đang xung đột",
    "stale": "Có thể lỗi thời",
}


class MemoryViewService:
    def __init__(
        self,
        memory_repo: MemoryItemRepository,
        project_repo: ProjectRepository,
        person_repo: PersonRepository,
        event_service: EventService | None = None,
        note_repo: NoteRepository | None = None,
    ):
        self.memory_repo = memory_repo
        self.project_repo = project_repo
        self.person_repo = person_repo
        self.event_service = event_service
        self.note_repo = note_repo

    async def overview(self) -> AssistantResponse:
        memories = await self._visible_memories()
        projects = await self.project_repo.list_all()
        people = await self.person_repo.list_all()
        kinds = Counter(str(item.kind) for item in memories)
        buckets = Counter(str(item.bucket) for item in memories)
        review_count = len(await self._review_memories())
        stale_count = len(await self._stale_memories())
        top_terms = _top_terms(memories, projects, people)
        tracked_projects = [project for project in projects if str(project.status) == "active"]

        return AssistantResponse(
            title="Ghi nhớ của anh",
            summary=(
                f"{len(memories)} memory đang dùng. "
                f"{review_count} cần duyệt, {stale_count} cần rà lại."
            ),
            sections=[
                AssistantSection(
                    heading="Triage",
                    lines=[
                        f"Review inbox: {review_count}",
                        f"Stale/needs refresh: {stale_count}",
                        f"Preference/boundary: {kinds[MemoryKind.PREFERENCE.value] + kinds[MemoryKind.BOUNDARY.value]}",
                        f"Goals: {kinds[MemoryKind.GOAL.value]}",
                    ],
                ),
                AssistantSection(
                    heading="Map",
                    lines=[
                        f"Profile: {buckets[MemoryBucket.PROFILE.value]}",
                        f"Project: {buckets[MemoryBucket.PROJECT.value]}",
                        f"Interaction: {buckets[MemoryBucket.INTERACTION.value]}",
                        f"People: {len(people)}",
                        f"Active projects: {len(tracked_projects)}",
                    ],
                ),
                AssistantSection(
                    heading="Top slices",
                    lines=top_terms or ["Chưa đủ dữ liệu để tạo slice."],
                ),
            ],
            footer="Dùng nút để mở từng lát nhỏ. Mỗi lát chỉ hiện vài mục để tránh quá tải.",
            actions=_topic_actions(),
        )

    async def topic(self, topic: str, page: int = 0) -> AssistantResponse | None:
        if topic not in MEMORY_TOPICS:
            return None
        selected = await self._topic_items(topic)
        total_pages = max(1, (len(selected) + MEMORY_PAGE_SIZE - 1) // MEMORY_PAGE_SIZE)
        page = min(max(page, 0), total_pages - 1)
        start = page * MEMORY_PAGE_SIZE
        visible_items = selected[start : start + MEMORY_PAGE_SIZE]
        note_map = {}
        if self.note_repo is not None:
            for item in visible_items:
                note = await self.note_repo.get_by_id(item.source_note_id)
                if note is not None:
                    note_map[item.source_note_id] = note
        page_items = _memory_card_lines(visible_items, topic, note_map)

        if not page_items:
            page_items = ["Chưa có ghi nhớ phù hợp trong chủ đề này."]

        actions: list[AssistantAction] = []
        nav_row = 0
        if page > 0:
            actions.append(
                AssistantAction(
                    label="‹ Trước",
                    action_id=f"mem:t:{topic}:{page - 1}",
                    row=nav_row,
                )
            )
        if page + 1 < total_pages:
            actions.append(
                AssistantAction(
                    label="Sau ›",
                    action_id=f"mem:t:{topic}:{page + 1}",
                    row=nav_row,
                )
            )
        actions.append(AssistantAction(label="Quay lại", action_id="mem:o", row=1))
        if topic in {"review", "conflicts", "stale"}:
            for index, item in enumerate(visible_items, 2):
                actions.append(
                    AssistantAction(
                        label=f"Giữ {index - 1}",
                        action_id=f"mem:k:{item.id}:{topic}:{page}",
                        row=index,
                    )
                )
                actions.append(
                    AssistantAction(
                        label="Bỏ",
                        action_id=f"mem:r:{item.id}:{topic}:{page}",
                        row=index,
                    )
                )
                actions.append(
                    AssistantAction(
                        label="Lỗi thời",
                        action_id=f"mem:s:{item.id}:{topic}:{page}",
                        row=index,
                    )
                )
                actions.append(
                    AssistantAction(label="Gộp", action_id=f"mem:g:{item.id}", row=index)
                )
                if item.conflict_state == "conflict":
                    actions.append(
                        AssistantAction(
                            label="Chọn chuẩn",
                            action_id=f"mem:x:{item.id}:{topic}:{page}",
                            row=index,
                        )
                    )

        return AssistantResponse(
            title=f"Ghi nhớ: {TOPIC_LABELS[topic]}",
            summary=f"{len(selected)} mục. Đang hiện {min(len(selected) - start, MEMORY_PAGE_SIZE) if selected else 0} mục.",
            sections=[AssistantSection(lines=page_items)],
            footer=f"Trang {page + 1}/{total_pages}",
            actions=actions,
        )

    async def _visible_memories(self) -> list[MemoryItem]:
        memories = await self.memory_repo.list_active()
        visible = [item for item in memories if str(item.kind) != MemoryKind.CORRECTION.value]
        return _deduplicate_memories(visible)

    async def _topic_items(self, topic: str) -> list[MemoryItem]:
        memories = await self._visible_memories()
        if topic == "review":
            return await self._review_memories()
        if topic == "conflicts":
            return [item for item in memories if item.conflict_state == "conflict"]
        if topic == "stale":
            return await self._stale_memories()
        projects = await self.project_repo.list_all()
        project_names = {project.id: project.name for project in projects}

        if topic == "self":
            return [
                item
                for item in memories
                if str(item.bucket) == MemoryBucket.PROFILE.value
                and "mindx" not in item.content.lower()
                and "ste" not in item.content.lower()
                and item.person_id is None
            ]

        if topic == "goals":
            return [
                item
                for item in memories
                if str(item.kind) == MemoryKind.GOAL.value or _looks_like_goal(item.content)
            ]

        if topic in {"mindx", "ste"}:
            needle = topic
            return [
                item
                for item in memories
                if needle in item.content.lower()
                or needle in project_names.get(item.project_id, "").lower()
            ]

        if topic == "people":
            return [item for item in memories if item.person_id is not None]

        return [item for item in memories if item.project_id is not None]

    async def confirm(self, item_id: str) -> AssistantResponse | None:
        item = await self.memory_repo.get_by_id(item_id)
        if item is None:
            return None
        await self.memory_repo.confirm(item_id, datetime.now(UTC))
        await self._log(EventType.MEMORY_ACTIVATED, item.id, {"source": "telegram_review"})
        await self._feedback(item, FeedbackSignal.ACCEPTED, "confirm_memory")
        return AssistantResponse(
            title="Đã xác nhận memory",
            summary=item.content,
            actions=[AssistantAction(label="Quay lại", action_id="mem:t:review:0")],
        )

    async def reject(self, item_id: str) -> AssistantResponse | None:
        item = await self.memory_repo.get_by_id(item_id)
        if item is None:
            return None
        await self.memory_repo.update_status(item_id, MemoryStatus.REJECTED.value)
        await self._log(EventType.MEMORY_REJECTED, item.id, {"source": "telegram_review"})
        await self._feedback(item, FeedbackSignal.REJECTED, "reject_memory")
        return AssistantResponse(
            title="Đã bỏ memory",
            summary=item.content,
            actions=[AssistantAction(label="Quay lại", action_id="mem:t:review:0")],
        )

    async def mark_stale(self, item_id: str) -> AssistantResponse | None:
        item = await self.memory_repo.get_by_id(item_id)
        if item is None:
            return None
        await self.memory_repo.update_status(item_id, MemoryStatus.SUPERSEDED.value)
        await self._log(EventType.MEMORY_SUPERSEDED, item.id, {"source": "telegram_review"})
        await self._feedback(item, FeedbackSignal.EDITED, "mark_memory_stale")
        return AssistantResponse(
            title="Đã đánh dấu lỗi thời",
            summary=item.content,
            actions=[AssistantAction(label="Quay lại", action_id="mem:t:stale:0")],
        )

    async def merge_prompt(self, item_id: str) -> AssistantResponse | None:
        item = await self.memory_repo.get_by_id(item_id)
        if item is None:
            return None
        return AssistantResponse(
            title="Gộp memory",
            summary=(
                "Em chưa tự gộp vì cần anh chọn memory canonical. "
                f"Hãy nhắn: sửa memory này thành phiên bản đúng hơn: {item.content}"
            ),
            actions=[AssistantAction(label="Quay lại", action_id="mem:t:review:0")],
        )

    async def select_canonical(self, item_id: str) -> AssistantResponse | None:
        item = await self.memory_repo.get_by_id(item_id)
        if item is None:
            return None
        all_items = await self.memory_repo.list_all()
        related_ids = [
            candidate.id
            for candidate in all_items
            if candidate.id == item.id
            or candidate.revision_of_id == item.id
            or item.revision_of_id == candidate.id
            or (
                item.revision_of_id is not None
                and candidate.revision_of_id == item.revision_of_id
            )
        ]
        await self.memory_repo.select_canonical(item.id, related_ids)
        await self._log(
            EventType.MEMORY_CANONICAL_SELECTED,
            item.id,
            {"related_item_ids": related_ids, "source": "telegram_review"},
        )
        await self._feedback(item, FeedbackSignal.EDITED, "select_canonical_memory")
        return AssistantResponse(
            title="Đã chọn memory chuẩn",
            summary=item.content,
            actions=[AssistantAction(label="Quay lại", action_id="mem:t:conflicts:0")],
        )

    async def stale(self, page: int = 0) -> AssistantResponse:
        response = await self.topic("stale", page)
        return response or AssistantResponse(title="Memory stale", summary="Không có memory stale.")

    async def _review_memories(self) -> list[MemoryItem]:
        now = datetime.now(UTC)
        return [
            item
            for item in await self._visible_memories()
            if str(item.status) == MemoryStatus.CANDIDATE.value
            or item.conflict_state == "conflict"
            or (
                (item.valid_until is None or item.valid_until > now)
                and (
                    str(item.status) == MemoryStatus.ACTIVE.value
                    and item.last_confirmed_at is None
                )
            )
        ]

    async def _stale_memories(self) -> list[MemoryItem]:
        now = datetime.now(UTC)
        return [
            item
            for item in await self._visible_memories()
            if str(item.status) == MemoryStatus.ACTIVE.value
            and (
                (item.valid_until is not None and item.valid_until <= now)
                or _freshness_date(item) < now - _STALE_AFTER
            )
        ]

    async def _log(self, event_type: EventType, item_id: str, payload: dict) -> None:
        if self.event_service is None:
            return
        await self.event_service.append_event(event_type, "memory", item_id, payload)

    async def _feedback(
        self,
        item: MemoryItem,
        signal: FeedbackSignal,
        action: str,
    ) -> None:
        if self.event_service is None:
            return
        source_chat_id = None
        source_message_id = None
        if self.note_repo is not None:
            note = await self.note_repo.get_by_id(item.source_note_id)
            if note is not None:
                source_chat_id = note.source_chat_id
                source_message_id = note.source_message_id
        await self.event_service.record_feedback(
            signal,
            "memory_item",
            item.id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            source_note_id=item.source_note_id,
            action=action,
        )


def _topic_actions() -> list[AssistantAction]:
    return [
        AssistantAction(
            label=TOPIC_LABELS[topic],
            action_id=f"mem:t:{topic}:0",
            row=index // 2,
        )
        for index, topic in enumerate(MEMORY_TOPICS)
    ]


def _memory_card_lines(items: list[MemoryItem], topic: str, note_map: dict | None = None) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(items, 1):
        lines.append(f"{index}. {_compact_content(item.content)}")
        if topic in {"review", "conflicts", "stale"}:
            note = (note_map or {}).get(item.source_note_id)
            source = _compact_content(note.raw_text, 90) if note is not None else item.source_note_id
            lines.append(f"   Nguồn: {source} · Tin cậy: {round(item.confidence * 100)}%")
        if topic == "review":
            lines.append(f"   Cần duyệt vì {_review_reason(item)}.")
        elif topic == "conflicts":
            lines.append("   Xung đột với một claim cùng chủ đề; cần chọn phiên bản chuẩn.")
        elif topic == "stale":
            lines.append(f"   {_stale_reason(item)}.")
    return lines


def _freshness_date(item: MemoryItem) -> datetime:
    if item.last_confirmed_at:
        return item.last_confirmed_at
    return item.updated_at


def _review_reason(item: MemoryItem) -> str:
    now = datetime.now(UTC)
    if str(item.status) == MemoryStatus.CANDIDATE.value:
        if item.valid_until and item.valid_until <= now:
            return "ghi nhớ mới nhưng đã hết hiệu lực trước khi được duyệt"
        return "ghi nhớ mới chưa được xác nhận"
    return "ghi nhớ cũ chưa từng được anh xác nhận"


def _stale_reason(item: MemoryItem) -> str:
    now = datetime.now(UTC)
    if item.valid_until and item.valid_until <= now:
        return f"Đã hết hiệu lực từ {item.valid_until:%d/%m/%Y}"
    freshness = _freshness_date(item)
    if freshness < now - _STALE_AFTER:
        return f"Đã hơn 120 ngày chưa được rà lại (lần cuối: {freshness:%d/%m/%Y})"
    return f"Lần cập nhật gần nhất {item.updated_at:%d/%m/%Y}"


def _top_terms(
    memories: list[MemoryItem],
    projects,
    people,
) -> list[str]:
    counter: Counter[str] = Counter()
    project_ids = {project.id for project in projects}
    people_ids = {person.id for person in people}
    for item in memories:
        if item.project_id in project_ids:
            counter["linked to projects"] += 1
        if item.person_id in people_ids:
            counter["linked to people"] += 1
        text = _normalize(item.content)
        for token in ("ste", "mindx", "goal", "muc tieu", "preference", "meeting"):
            if token in text:
                counter[token] += 1
    return [f"{name}: {count}" for name, count in counter.most_common(5)]


def _compact_content(value: str, limit: int = 140) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def _deduplicate_memories(memories: list[MemoryItem]) -> list[MemoryItem]:
    result: list[MemoryItem] = []
    seen: set[str] = set()
    for item in memories:
        key = _normalize(item.content)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _normalize(value: str) -> str:
    lowered = value.lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    ascii_text = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join("".join(char if char.isalnum() else " " for char in ascii_text).split())


def _looks_like_goal(value: str) -> bool:
    normalized = _normalize(value)
    return any(token in normalized for token in ("goal", "muc tieu", "objective", "okr"))
