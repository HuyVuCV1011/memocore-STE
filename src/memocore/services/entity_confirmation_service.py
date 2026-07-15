from __future__ import annotations

from datetime import UTC, datetime, timedelta

from memocore.adapters.storage.repositories import (
    NoteRepository,
    PersonRepository,
    ProjectRepository,
    normalize_lookup,
)
from memocore.domain.models import EventLog, EventType, FeedbackSignal
from memocore.domain.schemas import AssistantAction, AssistantResponse
from memocore.services.event_service import EventService


class EntityConfirmationService:
    def __init__(
        self,
        person_repo: PersonRepository,
        project_repo: ProjectRepository,
        event_service: EventService,
        note_repo: NoteRepository | None = None,
    ):
        self.person_repo = person_repo
        self.project_repo = project_repo
        self.event_service = event_service
        self.note_repo = note_repo

    async def prompt(self, event_id: str) -> AssistantResponse | None:
        event = await self.event_service.get_event(event_id)
        if (
            event is None
            or event.event_type != EventType.ENTITY_ALIAS_SUGGESTED
            or await self._is_resolved(event_id)
        ):
            return None
        alias = event.payload.get("alias")
        canonical = event.payload.get("canonical_name")
        if not alias or not canonical:
            return None
        return AssistantResponse(
            title="Xác nhận biệt danh",
            summary=f"“{alias}” có phải là cách anh gọi “{canonical}” không?",
            actions=[
                AssistantAction(
                    label="Đúng, ghi nhớ",
                    action_id=f"entity:x:{event_id}",
                    row=0,
                ),
                AssistantAction(label="Không", action_id=f"entity:n:{event_id}", row=0),
            ],
        )

    async def review(self, entity_type: str) -> AssistantResponse:
        recent_since = datetime.now(UTC) - timedelta(days=30)
        resolved_suggestion_ids = await self._resolved_suggestion_ids(recent_since)
        candidates = [
            event
            for event in await self.event_service.list_recent(
                EventType.ENTITY_ALIAS_SUGGESTED,
                since=recent_since,
                limit=50,
            )
            if event.entity_type == entity_type
            and event.id not in resolved_suggestion_ids
        ]
        events = []
        seen: set[tuple[str, str]] = set()
        for event in candidates:
            alias = str(event.payload.get("alias", "")).strip()
            if not alias or await self._alias_is_already_known(event.entity_type, event.entity_id, alias):
                continue
            key = (event.entity_id, normalize_lookup(alias))
            if key in seen:
                continue
            seen.add(key)
            events.append(event)
        if not events:
            label = "people" if entity_type == "person" else "projects"
            return AssistantResponse(
                title=f"{label.title()} review",
                summary="Chưa có gợi ý alias/merge cần xác nhận.",
            )
        lines = []
        actions = []
        for index, event in enumerate(events[:5], 1):
            alias = event.payload.get("alias")
            canonical = event.payload.get("canonical_name")
            lines.append(f"{index}. “{alias}” có phải là “{canonical}” không?")
            actions.extend(
                [
                    AssistantAction(label="Gộp", action_id=f"entity:x:{event.id}", row=index),
                    AssistantAction(label="Bỏ qua", action_id=f"entity:i:{event.id}", row=index),
                    AssistantAction(label="Không phải", action_id=f"entity:n:{event.id}", row=index),
                ]
            )
        return AssistantResponse(
            title="People review" if entity_type == "person" else "Projects review",
            summary="Em chỉ lưu alias khi anh xác nhận.",
            sections=[],
            footer="\n".join(lines),
            actions=actions,
        )

    async def _alias_is_already_known(
        self, entity_type: str, entity_id: str, alias: str
    ) -> bool:
        if entity_type == "person":
            entity = await self.person_repo.get_by_id(entity_id)
            values = [entity.display_name, *entity.aliases] if entity else []
        elif entity_type == "project":
            entity = await self.project_repo.get_by_id(entity_id)
            values = [entity.name, *entity.aliases] if entity else []
        else:
            return True
        normalized_alias = normalize_lookup(alias)
        return not entity or normalized_alias in {normalize_lookup(value) for value in values}

    async def confirm(self, event_id: str) -> AssistantResponse | None:
        event = await self.event_service.get_event(event_id)
        if (
            event is None
            or event.event_type != EventType.ENTITY_ALIAS_SUGGESTED
            or await self._is_resolved(event_id)
        ):
            return None
        alias = str(event.payload.get("alias", "")).strip()
        if not alias:
            return None
        if event.entity_type == "person":
            entity = await self.person_repo.get_by_id(event.entity_id)
            if entity is None:
                return None
            aliases = _append_alias(entity.aliases, entity.display_name, alias)
            await self.person_repo.update_aliases(entity.id, aliases)
            canonical = entity.display_name
        elif event.entity_type == "project":
            entity = await self.project_repo.get_by_id(event.entity_id)
            if entity is None:
                return None
            aliases = _append_alias(entity.aliases, entity.name, alias)
            await self.project_repo.update_aliases(entity.id, aliases)
            canonical = entity.name
        else:
            return None
        await self.event_service.append_event(
            EventType.ENTITY_ALIAS_CONFIRMED,
            event.entity_type,
            event.entity_id,
            {
                "alias": alias,
                "suggestion_event_id": event_id,
                "source_note_id": event.payload.get("source_note_id"),
                "status": "resolved",
            },
        )
        await self._record_feedback(event, FeedbackSignal.ACCEPTED, "confirm_alias")
        return AssistantResponse(
            title="Đã ghi nhớ biệt danh",
            summary=f"Từ giờ “{alias}” sẽ được hiểu là “{canonical}”.",
        )

    async def reject(self, event_id: str) -> AssistantResponse | None:
        event = await self._unresolved_suggestion(event_id)
        if event is None:
            return None
        await self.event_service.append_event(
            EventType.ENTITY_ALIAS_REJECTED,
            event.entity_type,
            event.entity_id,
            {
                "alias": event.payload.get("alias"),
                "suggestion_event_id": event.id,
                "source_note_id": event.payload.get("source_note_id"),
                "status": "resolved",
            },
        )
        await self._record_feedback(event, FeedbackSignal.REJECTED, "reject_alias")
        return AssistantResponse(
            title="Đã từ chối biệt danh",
            summary="Em sẽ không đề xuất lại liên kết tên này trong hàng chờ hiện tại.",
        )

    async def ignore(self, event_id: str) -> AssistantResponse | None:
        event = await self._unresolved_suggestion(event_id)
        if event is None:
            return None
        await self.event_service.append_event(
            EventType.ENTITY_ALIAS_IGNORED,
            event.entity_type,
            event.entity_id,
            {
                "alias": event.payload.get("alias"),
                "suggestion_event_id": event.id,
                "source_note_id": event.payload.get("source_note_id"),
                "status": "resolved",
            },
        )
        await self._record_feedback(event, FeedbackSignal.IGNORED, "ignore_alias")
        return AssistantResponse(
            title="Đã bỏ qua",
            summary="Em đã đóng gợi ý này và không lưu biệt danh.",
        )

    async def _unresolved_suggestion(self, event_id: str) -> EventLog | None:
        event = await self.event_service.get_event(event_id)
        if (
            event is None
            or event.event_type != EventType.ENTITY_ALIAS_SUGGESTED
            or await self._is_resolved(event_id)
        ):
            return None
        return event

    async def _is_resolved(self, event_id: str) -> bool:
        return event_id in await self._resolved_suggestion_ids(
            datetime.now(UTC) - timedelta(days=30)
        )

    async def _resolved_suggestion_ids(self, since: datetime) -> set[str]:
        resolved: set[str] = set()
        for event_type in (
            EventType.ENTITY_ALIAS_CONFIRMED,
            EventType.ENTITY_ALIAS_REJECTED,
            EventType.ENTITY_ALIAS_IGNORED,
        ):
            for event in await self.event_service.list_recent(
                event_type,
                since=since,
                limit=200,
            ):
                suggestion_id = event.payload.get("suggestion_event_id")
                if suggestion_id:
                    resolved.add(suggestion_id)
        return resolved

    async def _record_feedback(
        self,
        event: EventLog,
        signal: FeedbackSignal,
        action: str,
    ) -> None:
        source_note_id = event.payload.get("source_note_id")
        source_chat_id = None
        source_message_id = None
        if self.note_repo is not None and source_note_id:
            note = await self.note_repo.get_by_id(source_note_id)
            if note is not None:
                source_chat_id = note.source_chat_id
                source_message_id = note.source_message_id
        await self.event_service.record_feedback(
            signal,
            event.entity_type,
            event.entity_id,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            source_note_id=source_note_id,
            action=action,
            details={"suggestion_event_id": event.id},
        )


def _append_alias(existing: list[str], canonical: str, alias: str) -> list[str]:
    known = {value.casefold() for value in [canonical, *existing]}
    if alias.casefold() in known:
        return existing
    return [*existing, alias]
