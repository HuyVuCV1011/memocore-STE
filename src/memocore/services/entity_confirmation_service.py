from __future__ import annotations

from datetime import UTC, datetime, timedelta

from memocore.adapters.storage.repositories import (
    PersonRepository,
    ProjectRepository,
    normalize_lookup,
)
from memocore.domain.models import EventType
from memocore.domain.schemas import AssistantAction, AssistantResponse
from memocore.services.event_service import EventService


class EntityConfirmationService:
    def __init__(
        self,
        person_repo: PersonRepository,
        project_repo: ProjectRepository,
        event_service: EventService,
    ):
        self.person_repo = person_repo
        self.project_repo = project_repo
        self.event_service = event_service

    async def prompt(self, event_id: str) -> AssistantResponse | None:
        event = await self.event_service.get_event(event_id)
        if event is None or event.event_type != EventType.ENTITY_ALIAS_SUGGESTED:
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
        confirmed_suggestion_ids = {
            event.payload.get("suggestion_event_id")
            for event in await self.event_service.list_recent(
                EventType.ENTITY_ALIAS_CONFIRMED,
                since=recent_since,
                limit=200,
            )
            if event.payload.get("suggestion_event_id")
        }
        candidates = [
            event
            for event in await self.event_service.list_recent(
                EventType.ENTITY_ALIAS_SUGGESTED,
                since=recent_since,
                limit=50,
            )
            if event.entity_type == entity_type
            and event.id not in confirmed_suggestion_ids
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
                    AssistantAction(label="Bỏ qua", action_id=f"entity:n:{event.id}", row=index),
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
        if event is None or event.event_type != EventType.ENTITY_ALIAS_SUGGESTED:
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
            {"alias": alias, "suggestion_event_id": event_id},
        )
        return AssistantResponse(
            title="Đã ghi nhớ biệt danh",
            summary=f"Từ giờ “{alias}” sẽ được hiểu là “{canonical}”.",
        )


def _append_alias(existing: list[str], canonical: str, alias: str) -> list[str]:
    known = {value.casefold() for value in [canonical, *existing]}
    if alias.casefold() in known:
        return existing
    return [*existing, alias]
