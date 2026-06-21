from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import re
import unicodedata

from memocore.adapters.storage.repositories import (
    ChatContextRepository,
    PersonRepository,
    ProjectRepository,
    TaskRepository,
)
from memocore.adapters.storage.knowledge_repositories import OrganizationRepository
from memocore.domain.models import ChatContext


@dataclass(frozen=True)
class ResolvedReference:
    entity_type: str | None = None
    entity_id: str | None = None
    entity_name: str | None = None
    from_context: bool = False


class ReferenceResolver:
    def __init__(
        self,
        context_repo: ChatContextRepository,
        project_repo: ProjectRepository,
        person_repo: PersonRepository,
        task_repo: TaskRepository,
        organization_repo: OrganizationRepository | None = None,
        context_ttl: timedelta = timedelta(hours=6),
    ):
        self.context_repo = context_repo
        self.project_repo = project_repo
        self.person_repo = person_repo
        self.task_repo = task_repo
        self.organization_repo = organization_repo
        self.context_ttl = context_ttl

    async def resolve(
        self, source_chat_id: str | None, raw_text: str
    ) -> ResolvedReference:
        normalized = _normalize(raw_text)

        contextual_type = _contextual_reference_type(normalized)
        if source_chat_id and contextual_type:
            contextual = await self._context_reference(
                source_chat_id, expected_type=contextual_type
            )
            if contextual.entity_id is not None:
                return contextual

        organization_matches = []
        if self.organization_repo is not None:
            organization_matches = [
                organization
                for organization in await self.organization_repo.list_all()
                if any(
                    _contains_phrase(normalized, _normalize(name))
                    for name in (organization.name, *organization.aliases)
                )
            ]
            if len(organization_matches) == 1 and any(
                cue in normalized for cue in ("to chuc", "cong ty", "organization")
            ):
                organization = organization_matches[0]
                return ResolvedReference(
                    "organization", organization.id, organization.name
                )

        project_matches = [
            project
            for project in await self.project_repo.list_all()
            if any(
                _contains_phrase(normalized, _normalize(name))
                for name in (project.name, *project.aliases)
            )
        ]
        if len(project_matches) == 1:
            project = project_matches[0]
            return ResolvedReference("project", project.id, project.name)

        person_matches = [
            person
            for person in await self.person_repo.list_all()
            if any(
                _contains_phrase(normalized, _normalize(name))
                for name in (person.display_name, *person.aliases)
            )
        ]
        if len(person_matches) == 1:
            person = person_matches[0]
            return ResolvedReference("person", person.id, person.display_name)

        if len(organization_matches) == 1:
            organization = organization_matches[0]
            return ResolvedReference("organization", organization.id, organization.name)

        task = await self._matching_task(raw_text)
        if task is not None:
            if _asks_related_project(normalized) and task.project_id:
                project = await self.project_repo.get_by_id(task.project_id)
                if project is not None:
                    return ResolvedReference("project", project.id, project.name)
            return ResolvedReference("task", task.id, task.title)

        if source_chat_id and _has_contextual_reference(normalized):
            contextual = await self._context_reference(source_chat_id)
            if contextual.entity_id is not None:
                return contextual
        return ResolvedReference()

    async def _context_reference(
        self, source_chat_id: str, expected_type: str | None = None
    ) -> ResolvedReference:
        context = await self.context_repo.get(source_chat_id)
        if (
            context is None
            or not context.focused_entity_type
            or not context.focused_entity_id
            or (expected_type and context.focused_entity_type != expected_type)
            or (
                context.expires_at is not None
                and context.expires_at <= datetime.now(UTC)
            )
        ):
            return ResolvedReference()
        name = await self._entity_name(
            context.focused_entity_type, context.focused_entity_id
        )
        return ResolvedReference(
            context.focused_entity_type,
            context.focused_entity_id,
            name,
            from_context=True,
        )

    async def remember(
        self,
        source_chat_id: str | None,
        *,
        intent: str,
        reference: ResolvedReference,
        raw_text: str,
        source_message_id: str | None,
        result_entity_ids: list[str] | None = None,
    ) -> None:
        if not source_chat_id:
            return
        now = datetime.now(UTC)
        existing = await self.context_repo.get(source_chat_id)
        entity_type = reference.entity_type or (
            existing.focused_entity_type if existing else None
        )
        entity_id = reference.entity_id or (
            existing.focused_entity_id if existing else None
        )
        context = ChatContext(
            source_chat_id=source_chat_id,
            focused_entity_type=entity_type,
            focused_entity_id=entity_id,
            last_intent=intent,
            last_result_entity_ids=result_entity_ids or [],
            updated_at=now,
            expires_at=now + self.context_ttl if entity_id else None,
        )
        await self.context_repo.save(context)
        await self.context_repo.append_turn(
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            raw_text=raw_text,
            intent=intent,
            focused_entity_type=entity_type,
            focused_entity_id=entity_id,
            result_entity_ids=result_entity_ids,
        )

    async def _matching_task(self, raw_text: str):
        normalized = _normalize(raw_text)
        tasks = [*await self.task_repo.list_active()]
        tasks.extend(
            await self.task_repo.list_done_since(datetime.now(UTC) - timedelta(days=30))
        )
        scored = []
        query_tokens = set(normalized.split()) - _TASK_STOPWORDS
        for task in tasks:
            title_tokens = set(_normalize(task.title).split())
            overlap = query_tokens & title_tokens
            if len(overlap) >= 2:
                scored.append((len(overlap) / max(1, len(title_tokens)), task))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return None
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        return scored[0][1]

    async def _entity_name(self, entity_type: str, entity_id: str) -> str | None:
        if entity_type == "project":
            entity = await self.project_repo.get_by_id(entity_id)
            return entity.name if entity else None
        if entity_type == "person":
            entity = await self.person_repo.get_by_id(entity_id)
            return entity.display_name if entity else None
        if entity_type == "task":
            entity = await self.task_repo.get_by_id(entity_id)
            return entity.title if entity else None
        if entity_type == "organization" and self.organization_repo is not None:
            entity = await self.organization_repo.get_by_id(entity_id)
            return entity.name if entity else None
        return None


def _has_contextual_reference(normalized: str) -> bool:
    return any(
        re.search(pattern, normalized)
        for pattern in (
            r"\bdu an do\b",
            r"\bdu an nay\b",
            r"\btask do\b",
            r"\btask nay\b",
            r"\bviec do\b",
            r"\bviec nay\b",
            r"\bnguoi do\b",
            r"\bnguoi nay\b",
            r"\bno\b",
            r"\bcai do\b",
            r"\bthat project\b",
            r"\bit\b",
        )
    )


def _contextual_reference_type(normalized: str) -> str | None:
    typed_patterns = {
        "project": (r"\bdu an do\b", r"\bdu an nay\b", r"\bthat project\b"),
        "task": (r"\btask do\b", r"\btask nay\b", r"\bviec do\b", r"\bviec nay\b"),
        "person": (r"\bnguoi do\b", r"\bnguoi nay\b", r"\bthat person\b"),
        "organization": (
            r"\bto chuc do\b",
            r"\bto chuc nay\b",
            r"\bcong ty do\b",
            r"\bcong ty nay\b",
            r"\bthat organization\b",
        ),
    }
    for entity_type, patterns in typed_patterns.items():
        if any(re.search(pattern, normalized) for pattern in patterns):
            return entity_type
    return None


def _asks_related_project(normalized: str) -> bool:
    return "lien quan du an" in normalized or "thuoc du an" in normalized


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(phrase) and re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def _normalize(value: str) -> str:
    lowered = value.casefold().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    ascii_text = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    return " ".join(
        "".join(char if char.isalnum() else " " for char in ascii_text).split()
    )


_TASK_STOPWORDS = {
    "task",
    "viec",
    "cua",
    "toi",
    "lien",
    "quan",
    "du",
    "an",
    "gi",
    "dang",
    "nhu",
    "the",
    "nao",
}
