from datetime import UTC, datetime

from memocore.adapters.storage.repositories import EventLogRepository
from memocore.domain.models import (
    EventLog,
    EventType,
    FeedbackSignal,
    FeedbackStatus,
)


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

    async def get_event(self, event_id: str) -> EventLog | None:
        return await self.event_repo.get_by_id(event_id)

    async def list_recent(
        self,
        event_type: EventType | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[EventLog]:
        return await self.event_repo.list_recent(event_type=event_type, since=since, limit=limit)

    async def was_undone(self, event_id: str) -> bool:
        events = await self.event_repo.list_by_entity("work_event", event_id)
        return any(event.event_type == EventType.WORK_ITEM_UNDONE for event in events)

    async def exists_recent(
        self,
        event_type: EventType,
        entity_type: str,
        entity_id: str,
        since: datetime,
    ) -> bool:
        return await self.event_repo.exists_recent(event_type, entity_type, entity_id, since)

    async def record_feedback(
        self,
        signal: FeedbackSignal,
        artifact_type: str,
        artifact_id: str,
        *,
        source_chat_id: str | None = None,
        source_message_id: str | None = None,
        source_note_id: str | None = None,
        action: str | None = None,
        status: FeedbackStatus | None = None,
        details: dict | None = None,
    ) -> EventLog:
        feedback_status = status or (
            FeedbackStatus.OPEN
            if signal == FeedbackSignal.CORRECTION
            else FeedbackStatus.RESOLVED
        )
        payload = {
            "schema_version": 1,
            "signal": signal.value,
            "status": feedback_status.value,
            "artifact": {"type": artifact_type, "id": artifact_id},
            "turn": {
                "key": (
                    f"{source_chat_id}:{source_message_id}"
                    if source_chat_id and source_message_id
                    else None
                ),
                "source_chat_id": source_chat_id,
                "source_message_id": source_message_id,
            },
            "source_note_id": source_note_id,
        }
        if action:
            payload["action"] = action
        if details:
            payload["details"] = details
        return await self.append_event(
            EventType.USER_FEEDBACK_RECORDED,
            artifact_type,
            artifact_id,
            payload,
        )

    async def resolve_feedback(self, feedback_event_id: str) -> EventLog | None:
        feedback = await self.get_event(feedback_event_id)
        if (
            feedback is None
            or feedback.event_type != EventType.USER_FEEDBACK_RECORDED
            or feedback.payload.get("schema_version") != 1
        ):
            return None
        resolved = await self.list_recent(
            EventType.USER_FEEDBACK_RESOLVED,
            limit=500,
        )
        if any(
            event.payload.get("feedback_event_id") == feedback_event_id
            for event in resolved
        ):
            return next(
                event
                for event in resolved
                if event.payload.get("feedback_event_id") == feedback_event_id
            )
        return await self.append_event(
            EventType.USER_FEEDBACK_RESOLVED,
            feedback.entity_type,
            feedback.entity_id,
            {
                "schema_version": 1,
                "feedback_event_id": feedback_event_id,
                "signal": feedback.payload.get("signal"),
                "status": FeedbackStatus.RESOLVED.value,
            },
        )
