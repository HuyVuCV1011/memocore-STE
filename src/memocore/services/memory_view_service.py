from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from memocore.adapters.storage.repositories import (
    MemoryItemRepository,
    PersonRepository,
    ProjectRepository,
)
from memocore.domain.models import MemoryBucket, MemoryItem, MemoryKind
from memocore.domain.models import EventType, MemoryStatus
from memocore.domain.schemas import AssistantAction, AssistantResponse, AssistantSection
from memocore.services.event_service import EventService


MEMORY_PAGE_SIZE = 4
_STALE_AFTER = timedelta(days=120)
MEMORY_TOPICS = ("review", "stale", "self", "goals", "people", "projects", "mindx", "ste")

TOPIC_LABELS = {
    "self": "Bản thân",
    "goals": "Mục tiêu",
    "mindx": "MindX",
    "ste": "STE",
    "people": "Con người",
    "projects": "Dự án",
    "review": "Cần xác nhận",
    "stale": "Có thể lỗi thời",
}


class MemoryViewService:
    def __init__(
        self,
        memory_repo: MemoryItemRepository,
        project_repo: ProjectRepository,
        person_repo: PersonRepository,
        event_service: EventService | None = None,
    ):
        self.memory_repo = memory_repo
        self.project_repo = project_repo
        self.person_repo = person_repo
        self.event_service = event_service

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
            title="Ghi nhớ của bạn",
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
        item_lines = _memory_card_lines(selected)
        total_pages = max(1, (len(selected) + MEMORY_PAGE_SIZE - 1) // MEMORY_PAGE_SIZE)
        page = min(max(page, 0), total_pages - 1)
        start = page * MEMORY_PAGE_SIZE
        page_items = item_lines[start * 3 : (start + MEMORY_PAGE_SIZE) * 3]

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
        if topic in {"review", "stale"}:
            for index, item in enumerate(selected[start : start + MEMORY_PAGE_SIZE], 2):
                actions.append(
                    AssistantAction(
                        label=f"Giữ {index - 1}",
                        action_id=f"mem:k:{item.id}",
                        row=index,
                    )
                )
                actions.append(
                    AssistantAction(label="Bỏ", action_id=f"mem:r:{item.id}", row=index)
                )
                actions.append(
                    AssistantAction(label="Lỗi thời", action_id=f"mem:s:{item.id}", row=index)
                )
                actions.append(
                    AssistantAction(label="Gộp", action_id=f"mem:g:{item.id}", row=index)
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
                "Mình chưa tự gộp vì cần bạn chọn memory canonical. "
                f"Hãy nhắn: sửa memory này thành phiên bản đúng hơn: {item.content}"
            ),
            actions=[AssistantAction(label="Quay lại", action_id="mem:t:review:0")],
        )

    async def stale(self, page: int = 0) -> AssistantResponse:
        response = await self.topic("stale", page)
        return response or AssistantResponse(title="Memory stale", summary="Không có memory stale.")

    async def _review_memories(self) -> list[MemoryItem]:
        now = datetime.now(UTC)
        return [
            item
            for item in await self._visible_memories()
            if item.confidence < 0.85
            or item.last_confirmed_at is None
            or (item.valid_until is not None and item.valid_until <= now)
        ]

    async def _stale_memories(self) -> list[MemoryItem]:
        now = datetime.now(UTC)
        return [
            item
            for item in await self._visible_memories()
            if (item.valid_until is not None and item.valid_until <= now)
            or item.last_confirmed_at is None
            or item.updated_at < now - _STALE_AFTER
        ]

    async def _log(self, event_type: EventType, item_id: str, payload: dict) -> None:
        if self.event_service is None:
            return
        await self.event_service.append_event(event_type, "memory", item_id, payload)


def _topic_actions() -> list[AssistantAction]:
    return [
        AssistantAction(
            label=TOPIC_LABELS[topic],
            action_id=f"mem:t:{topic}:0",
            row=index // 2,
        )
        for index, topic in enumerate(MEMORY_TOPICS)
    ]


def _memory_card_lines(items: list[MemoryItem]) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(items, 1):
        lines.append(f"{index}. {_compact_content(item.content)}")
        lines.append(
            "   "
            + " | ".join(
                [
                    f"{item.bucket}/{item.kind}",
                    f"tin cậy {round(item.confidence * 100)}%",
                    _freshness_label(item),
                ]
            )
        )
        lines.append(f"   id:{item.id[:8]} source:{item.source_note_id[:8]}")
    return lines


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


def _freshness_label(item: MemoryItem) -> str:
    now = datetime.now(UTC)
    if item.valid_until and item.valid_until <= now:
        return f"hết hạn {item.valid_until:%d/%m/%Y}"
    if item.last_confirmed_at:
        return f"xác nhận {item.last_confirmed_at:%d/%m/%Y}"
    if item.updated_at < now - _STALE_AFTER:
        return f"cũ {item.updated_at:%d/%m/%Y}"
    return "chưa xác nhận"


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


def _deduplicate_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = _normalize(line)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(line)
    return result


def _normalize(value: str) -> str:
    lowered = value.lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    ascii_text = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join("".join(char if char.isalnum() else " " for char in ascii_text).split())


def _memory_review_line(item: MemoryItem) -> str:
    source = item.source_type.replace("_", " ")
    confidence = f"{round(item.confidence * 100)}%"
    if item.valid_until:
        validity = f"hết hạn {item.valid_until:%d/%m/%Y}"
    elif item.last_confirmed_at:
        validity = f"xác nhận {item.last_confirmed_at:%d/%m/%Y}"
    else:
        validity = "chưa xác nhận"
    revision = " · có lịch sử sửa" if item.revision_of_id else ""
    return f"{item.content} · nguồn {source} · tin cậy {confidence} · {validity}{revision}"


def _looks_like_goal(value: str) -> bool:
    normalized = _normalize(value)
    return any(token in normalized for token in ("goal", "muc tieu", "objective", "okr"))
