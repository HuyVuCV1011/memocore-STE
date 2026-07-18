from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
import unicodedata

from memocore.adapters.storage.repositories import (
    CommitmentRepository,
    FollowUpRepository,
    PersonRepository,
    TaskRepository,
)
from memocore.domain.models import CommitmentStatus, EventType, FollowUpStatus
from memocore.services.event_service import EventService


@dataclass(frozen=True)
class LifecycleResult:
    handled: bool
    reply: str = ""
    entity_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class _LifecycleCandidate:
    kind: str
    id: str
    title: str


class CommitmentLifecycleService:
    """Close obvious waiting/follow-up/commitment loops from natural language."""

    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        followup_repo: FollowUpRepository,
        commitment_repo: CommitmentRepository,
        person_repo: PersonRepository,
        event_service: EventService,
        display_timezone: tzinfo = UTC,
    ):
        self.task_repo = task_repo
        self.followup_repo = followup_repo
        self.commitment_repo = commitment_repo
        self.person_repo = person_repo
        self.event_service = event_service
        self.display_timezone = display_timezone

    async def handle_text(
        self,
        text: str,
        *,
        source_chat_id: str | None = None,
        source_message_id: str | None = None,
        now: datetime | None = None,
    ) -> LifecycleResult:
        mode = _closure_mode(text)
        if mode is None:
            return LifecycleResult(False)
        person = await self._person_in_text(text)
        if person is None:
            return LifecycleResult(False)
        candidates = await self._candidates_for_person(person.id)
        if not candidates:
            return LifecycleResult(
                True,
                f"Dạ, em chưa thấy open loop nào đang chờ {person.display_name}.",
            )
        if len(candidates) > 1:
            lines = [
                f"{index}. {_kind_label(candidate.kind)}: {candidate.title}"
                for index, candidate in enumerate(candidates[:5], 1)
            ]
            return LifecycleResult(
                True,
                (
                    f"Dạ, em thấy nhiều open loop với {person.display_name}. "
                    "Anh nói rõ mục nào đã xong giúp em nha:\n" + "\n".join(lines)
                ),
                tuple(candidate.id for candidate in candidates[:5]),
        )
        candidate = candidates[0]
        before = await self._snapshot(candidate)
        next_task_id = await self._apply(candidate, mode)
        await self.event_service.append_event(
            _event_type(candidate.kind),
            candidate.kind,
            candidate.id,
            {
                "person_id": person.id,
                "source_chat_id": source_chat_id,
                "source_message_id": source_message_id,
                "mode": mode,
                "before": before,
                "next_task_id": next_task_id,
            },
            created_at=now,
        )
        verb = "đóng" if mode == "done" else "hủy"
        return LifecycleResult(
            True,
            f"Dạ, em đã {verb} {_kind_label(candidate.kind).lower()} “{candidate.title}”.",
            (candidate.id,),
        )

    async def _person_in_text(self, text: str):
        normalized = _normalize_text(text)
        matches = []
        for person in await self.person_repo.list_all():
            names = [person.display_name, *person.aliases]
            if any(_name_in_text(name, normalized) for name in names if name):
                matches.append(person)
        return matches[0] if len(matches) == 1 else None

    async def _candidates_for_person(self, person_id: str) -> list[_LifecycleCandidate]:
        tasks = [
            _LifecycleCandidate("task", task.id, task.title)
            for task in await self.task_repo.list_active_by_person(person_id)
            if str(task.status) in {"waiting", "blocked"}
        ]
        followups = [
            _LifecycleCandidate("followup", item.id, item.title)
            for item in await self.followup_repo.list_open_by_person(person_id)
        ]
        commitments = [
            _LifecycleCandidate("commitment", item.id, item.title)
            for item in await self.commitment_repo.list_open_by_person(person_id)
        ]
        return [*tasks, *followups, *commitments]

    async def _apply(self, candidate: _LifecycleCandidate, mode: str) -> str | None:
        if candidate.kind == "task":
            if mode == "done":
                _, next_task, created = await self.task_repo.complete_and_schedule_next(
                    candidate.id
                )
                return next_task.id if next_task is not None and created else None
            else:
                await self.task_repo.update_status(candidate.id, "cancelled")
            return None
        if candidate.kind == "followup":
            await self.followup_repo.update_status(
                candidate.id,
                FollowUpStatus.DONE if mode == "done" else FollowUpStatus.CANCELLED,
            )
            return None
        await self.commitment_repo.update_status(
            candidate.id,
            CommitmentStatus.DONE if mode == "done" else CommitmentStatus.CANCELLED,
        )
        return None

    async def _snapshot(self, candidate: _LifecycleCandidate) -> dict:
        if candidate.kind == "task":
            task = await self.task_repo.get_by_id(candidate.id)
            if task is None:
                return {}
            return {
                "title": task.title,
                "status": str(task.status),
                "priority": task.priority,
                "due_at": task.due_at.isoformat() if task.due_at else None,
            }
        if candidate.kind == "followup":
            followup = await self.followup_repo.get_by_id(candidate.id)
            if followup is None:
                return {}
            return {
                "title": followup.title,
                "status": str(followup.status),
                "due_at": followup.due_at.isoformat() if followup.due_at else None,
            }
        commitment = await self.commitment_repo.get_by_id(candidate.id)
        if commitment is None:
            return {}
        return {
            "title": commitment.title,
            "status": str(commitment.status),
            "due_at": commitment.due_at.isoformat() if commitment.due_at else None,
        }


def _closure_mode(text: str) -> str | None:
    normalized = _normalize_text(text)
    cancel_signals = (
        "khong can theo nua",
        "khong can nua",
        "bo qua",
        "huy theo doi",
        "khong theo nua",
    )
    if any(signal in normalized for signal in cancel_signals):
        return "cancelled"
    done_signals = (
        "da gui",
        "gui roi",
        "da tra loi",
        "tra loi roi",
        "da xong",
        "xong roi",
        "done roi",
        "da nhan",
        "nhan roi",
    )
    if any(signal in normalized for signal in done_signals):
        return "done"
    return None


def _name_in_text(name: str, normalized_text: str) -> bool:
    normalized_name = _normalize_text(name)
    if not normalized_name:
        return False
    return normalized_name in normalized_text


def _normalize_text(value: str) -> str:
    lowered = value.casefold().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    ascii_text = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join("".join(char if char.isalnum() else " " for char in ascii_text).split())


def _kind_label(kind: str) -> str:
    return {
        "task": "Task",
        "followup": "Follow-up",
        "commitment": "Commitment",
    }.get(kind, kind)


def _event_type(kind: str) -> EventType:
    if kind == "followup":
        return EventType.FOLLOWUP_DONE
    if kind == "commitment":
        return EventType.COMMITMENT_DONE
    return EventType.TASK_DONE
