from memocore.adapters.storage.repositories import MemoryItemRepository
from memocore.domain.models import EventType, MemoryItem, MemoryStatus
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
    ) -> list[MemoryItem]:
        created: list[MemoryItem] = []
        for candidate in candidates:
            item = MemoryItem(
                bucket=candidate.bucket,
                kind=candidate.kind,
                content=candidate.content,
                source_note_id=source_note_id,
                project_id=project_id if candidate.bucket == "project" else None,
                confidence=candidate.confidence,
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

    async def list_active(self) -> list[MemoryItem]:
        return await self.memory_repo.list_active()

    async def activate(self, item_id: str) -> None:
        await self.memory_repo.update_status(item_id, MemoryStatus.ACTIVE.value)
        await self.event_service.append_event(EventType.MEMORY_ACTIVATED, "memory_item", item_id)

    async def forget(self, item_id: str) -> None:
        await self.memory_repo.update_status(item_id, MemoryStatus.SUPERSEDED.value)
        await self.event_service.append_event(EventType.MEMORY_SUPERSEDED, "memory_item", item_id)
