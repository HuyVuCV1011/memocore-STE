from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from memocore.adapters.storage.repositories import (
    ChatContextRepository,
    ClarificationRequestRepository,
    TaskListContextRepository,
    TaskRepository,
)
from memocore.domain.models import ClarificationRequest


@dataclass(frozen=True)
class ConversationTurnSnapshot:
    raw_text: str
    intent: str
    assistant_reply: str = ""
    result_entity_ids: tuple[str, ...] = ()
    plan: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskReference:
    id: str
    title: str


@dataclass(frozen=True)
class ConversationFrame:
    """Bounded state supplied to planning for one user turn."""

    source_chat_id: str | None
    recent_turns: tuple[ConversationTurnSnapshot, ...] = ()
    focused_entity_type: str | None = None
    focused_entity_id: str | None = None
    last_intent: str | None = None
    last_result_entity_ids: tuple[str, ...] = ()
    listed_tasks: tuple[TaskReference, ...] = ()
    pending_clarification: ClarificationRequest | None = None
    active_task_ids: frozenset[str] = frozenset()

    @property
    def previous_turn(self) -> ConversationTurnSnapshot | None:
        return self.recent_turns[0] if self.recent_turns else None

    def prompt_context(self) -> str:
        lines: list[str] = []
        if self.focused_entity_type and self.focused_entity_id:
            lines.append(
                f"Current focus: {self.focused_entity_type}:{self.focused_entity_id}"
            )
        if self.pending_clarification is not None:
            lines.append(
                "Pending clarification: "
                f"{self.pending_clarification.entity_type} / "
                f"{self.pending_clarification.question}"
            )
        if self.listed_tasks:
            listed = "; ".join(
                f"{index}. {task.title} [{task.id}]"
                for index, task in enumerate(self.listed_tasks, 1)
            )
            lines.append(f"Last visible tasks: {listed}")
        for turn in reversed(self.recent_turns[:6]):
            lines.append(f"User: {turn.raw_text}")
            if turn.assistant_reply:
                lines.append(f"Assistant: {turn.assistant_reply}")
        return "\n".join(lines) or "(no prior conversation context)"


class ConversationFrameBuilder:
    def __init__(
        self,
        context_repo: ChatContextRepository,
        clarification_repo: ClarificationRequestRepository,
        task_list_repo: TaskListContextRepository,
        task_repo: TaskRepository,
    ):
        self.context_repo = context_repo
        self.clarification_repo = clarification_repo
        self.task_list_repo = task_list_repo
        self.task_repo = task_repo

    async def build(self, source_chat_id: str | None) -> ConversationFrame:
        active_tasks = await self.task_repo.list_active()
        if not source_chat_id:
            return ConversationFrame(
                source_chat_id=None,
                active_task_ids=frozenset(task.id for task in active_tasks),
            )

        context = await self.context_repo.get(source_chat_id)
        pending = await self.clarification_repo.find_pending_for_chat(source_chat_id)
        task_ids = await self.task_list_repo.get(source_chat_id)
        listed_tasks: list[TaskReference] = []
        for task_id in task_ids:
            task = await self.task_repo.get_by_id(task_id)
            if task is not None:
                listed_tasks.append(TaskReference(task.id, task.title))

        turns = tuple(
            ConversationTurnSnapshot(
                raw_text=row["raw_text"],
                intent=row["intent"],
                assistant_reply=row.get("assistant_reply") or "",
                result_entity_ids=tuple(row.get("result_entity_ids") or []),
                plan=row.get("plan") or {},
            )
            for row in await self.context_repo.list_recent_turns(source_chat_id, limit=8)
        )
        return ConversationFrame(
            source_chat_id=source_chat_id,
            recent_turns=turns,
            focused_entity_type=context.focused_entity_type if context else None,
            focused_entity_id=context.focused_entity_id if context else None,
            last_intent=context.last_intent if context else None,
            last_result_entity_ids=tuple(
                context.last_result_entity_ids if context else []
            ),
            listed_tasks=tuple(listed_tasks),
            pending_clarification=pending,
            active_task_ids=frozenset(task.id for task in active_tasks),
        )
