import re
import unicodedata
from datetime import timedelta

from memocore.adapters.storage.repositories import MemoryItemRepository
from memocore.domain.models import EventType, MemoryItem, MemoryKind, MemoryStatus, utc_now
from memocore.domain.schemas import MemoryCandidate
from memocore.services.event_service import EventService


class MemoryService:
    def __init__(self, memory_repo: MemoryItemRepository, event_service: EventService):
        self.memory_repo = memory_repo
        self.event_service = event_service

    async def persist_candidates(
        self,
        candidates: list[MemoryCandidate],
        source_note_id: str,
        project_id: str | None = None,
        person_id: str | None = None,
        organization_id: str | None = None,
        decision_id: str | None = None,
        supersede_related: bool = False,
    ) -> list[MemoryItem]:
        created: list[MemoryItem] = []
        for candidate in candidates:
            related = await self.related_items(candidate)
            if supersede_related or await self.has_related_conflict(candidate):
                await self.supersede_related(candidate)
            now = utc_now()
            item = MemoryItem(
                bucket=candidate.bucket,
                kind=candidate.kind,
                content=candidate.content,
                source_note_id=source_note_id,
                project_id=project_id if candidate.bucket == "project" else None,
                person_id=person_id,
                organization_id=organization_id,
                decision_id=decision_id,
                confidence=candidate.confidence,
                observed_at=now,
                valid_from=now,
                valid_until=(
                    now + timedelta(days=90)
                    if candidate.kind == MemoryKind.PROJECT_STATE
                    else None
                ),
                last_confirmed_at=None,
                revision_of_id=related[0].id if related else None,
            )
            created_item = await self.memory_repo.create(item)
            await self.event_service.append_event(
                EventType.MEMORY_CANDIDATE_CREATED,
                "memory_item",
                created_item.id,
                {"source_note_id": source_note_id},
            )
            created.append(created_item)
        return created

    async def find_similar(self, candidate: MemoryCandidate) -> list[MemoryItem]:
        existing = await self.memory_repo.list_by_bucket(candidate.bucket)
        return [
            item
            for item in existing
            if item.status in {MemoryStatus.CANDIDATE.value, MemoryStatus.ACTIVE.value}
            and item.kind == candidate.kind
            and item.content != candidate.content
            and _semantic_similarity(candidate.content, item.content) >= 0.65
        ][:3]

    async def list_active(self) -> list[MemoryItem]:
        return await self.memory_repo.list_active()

    async def activate(self, item_id: str) -> None:
        await self.memory_repo.update_status(item_id, MemoryStatus.ACTIVE.value)
        await self.event_service.append_event(EventType.MEMORY_ACTIVATED, "memory_item", item_id)

    async def forget(self, item_id: str) -> None:
        await self.memory_repo.update_status(item_id, MemoryStatus.SUPERSEDED.value)
        await self.event_service.append_event(EventType.MEMORY_SUPERSEDED, "memory_item", item_id)

    async def reject(self, item_id: str) -> None:
        await self.memory_repo.update_status(item_id, MemoryStatus.REJECTED.value)
        await self.event_service.append_event(EventType.MEMORY_REJECTED, "memory_item", item_id)

    async def delete(self, item_id: str) -> None:
        await self.memory_repo.delete(item_id)
        await self.event_service.append_event(EventType.MEMORY_DELETED, "memory_item", item_id)

    async def delete_matching(self, query: str) -> int:
        matches = self._match_items(await self.memory_repo.list_all(), query)
        for item in matches:
            await self.delete(item.id)
        return len(matches)

    async def supersede_related(self, candidate: MemoryCandidate) -> int:
        matches = await self.related_items(candidate)
        for item in matches:
            await self.forget(item.id)
        return len(matches)

    async def related_items(self, candidate: MemoryCandidate) -> list[MemoryItem]:
        existing = await self.memory_repo.list_by_bucket(candidate.bucket)
        return [
            item
            for item in existing
            if item.status in {MemoryStatus.CANDIDATE.value, MemoryStatus.ACTIVE.value}
            and item.kind == candidate.kind
            and item.content != candidate.content
            and _related_memory(candidate.content, item.content)
        ]

    async def has_related_conflict(self, candidate: MemoryCandidate) -> bool:
        existing = await self.memory_repo.list_by_bucket(candidate.bucket)
        return any(
            item.status in {MemoryStatus.CANDIDATE.value, MemoryStatus.ACTIVE.value}
            and item.kind == candidate.kind
            and item.content != candidate.content
            and _related_memory(candidate.content, item.content)
            for item in existing
        )

    def _match_items(self, items: list[MemoryItem], query: str) -> list[MemoryItem]:
        query_tokens = _meaningful_tokens(query)
        if not query_tokens:
            return []
        matches: list[MemoryItem] = []
        normalized_query = _normalize_text(query)
        for item in items:
            normalized_content = _normalize_text(item.content)
            content_tokens = _meaningful_tokens(item.content)
            if normalized_query in normalized_content:
                matches.append(item)
                continue
            if len(query_tokens) == 1 and query_tokens <= content_tokens:
                matches.append(item)
                continue
            if len(query_tokens & content_tokens) >= max(2, min(4, len(query_tokens))):
                matches.append(item)
        return matches


def _related_memory(new_content: str, existing_content: str) -> bool:
    new_tokens = _meaningful_tokens(new_content)
    existing_tokens = _meaningful_tokens(existing_content)
    if not new_tokens or not existing_tokens:
        return False
    if _memory_slot(new_content) and _memory_slot(new_content) == _memory_slot(existing_content):
        return True
    overlap = new_tokens & existing_tokens
    if _numbers(new_content) != _numbers(existing_content) and len(overlap) >= 2:
        return True
    return len(overlap) >= 2 and len(overlap) / min(len(new_tokens), len(existing_tokens)) >= 0.4


def _semantic_similarity(left: str, right: str) -> float:
    left_tokens = _meaningful_tokens(left)
    right_tokens = _meaningful_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / len(left_tokens | right_tokens)


def _memory_slot(value: str) -> str | None:
    normalized = _normalize_text(value)
    relationship_slots = {
        "vo": ("vo toi", "ten vo", "wife", "my wife", "wife name"),
        "chong": ("chong toi", "ten chong", "husband", "my husband", "husband name"),
        "con": ("con toi", "ten con", "child", "my child", "child name"),
        "cong ty": ("cong ty toi", "ten cong ty", "company", "my company", "company name"),
    }
    for slot, signals in relationship_slots.items():
        if any(signal in normalized for signal in signals):
            return f"{slot}:name"
    return None


def _numbers(value: str) -> set[str]:
    return set(re.findall(r"\b\d+\b", _normalize_text(value)))


def _meaningful_tokens(value: str) -> set[str]:
    stopwords = {
        "toi",
        "ban",
        "nguoi",
        "dung",
        "la",
        "co",
        "cua",
        "ve",
        "o",
        "cho",
        "thong",
        "tin",
        "memory",
        "nho",
        "rang",
        "sua",
        "lai",
        "thoi",
        "ngoai",
        "ra",
        "project",
    }
    return {
        token
        for token in _normalize_text(value).split()
        if len(token) > 1 and token not in stopwords
    }


def _normalize_text(value: str) -> str:
    lowered = value.lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    ascii_text = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join("".join(char if char.isalnum() else " " for char in ascii_text).split())
