from datetime import UTC, datetime

from memocore.adapters.storage.repositories import EventLogRepository
from memocore.domain.models import EventLog, EventType


class EventService:
    def __init__(self, event_repo: EventLogRepository):
        self.event_repo = event_repo

    async def append_event(
        self,
        event_type: EventType,
        entity_type: str,
        entity_id: str,
        payload: dict | None = None,
        created_at: datetime | None = None,
    ) -> EventLog:
        event = EventLog(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload or {},
            created_at=created_at or datetime.now(UTC),
        )
        return await self.event_repo.create(event)

    async def list_events_for_entity(self, entity_type: str, entity_id: str) -> list[EventLog]:
        return await self.event_repo.list_by_entity(entity_type, entity_id)

    async def exists_recent(
        self,
        event_type: EventType,
        entity_type: str,
        entity_id: str,
        since: datetime,
    ) -> bool:
        return await self.event_repo.exists_recent(event_type, entity_type, entity_id, since)
