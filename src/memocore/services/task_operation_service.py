from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from memocore.adapters.storage.repositories import TaskRepository
from memocore.domain.models import EventType, Task, TaskStatus
from memocore.services.event_service import EventService

if TYPE_CHECKING:
    from memocore.services.activity_reconciliation_service import (
        ActivityReconciliationService,
    )


@dataclass(frozen=True)
class TaskOperationResult:
    task: Task | None
    next_task: Task | None = None
    next_created: bool = False
    event_id: str | None = None
    linked_artifacts_updated: int = 0


class TaskOperationService:
    """Single mutation boundary for task state across text, callbacks and confirmations."""

    def __init__(
        self,
        task_repo: TaskRepository,
        event_service: EventService,
        activity_reconciliation_service: "ActivityReconciliationService | None" = None,
    ):
        self.task_repo = task_repo
        self.event_service = event_service
        self.activity_reconciliation_service = activity_reconciliation_service

    async def complete(
        self, task_id: str, *, transition: str, source_note_id: str | None = None
    ) -> TaskOperationResult:
        task, next_task, created = await self.task_repo.complete_and_schedule_next(
            task_id
        )
        if task is None:
            return TaskOperationResult(None)
        await self.event_service.append_event(
            EventType.TASK_DONE,
            "task",
            task.id,
            {
                "source_note_id": source_note_id,
                "transition": transition,
                "recurrence_rule": task.recurrence_rule,
                "next_task_id": next_task.id if next_task else None,
            },
        )
        if next_task is not None and created:
            await self.event_service.append_event(
                EventType.TASK_RECURRENCE_SCHEDULED,
                "task",
                next_task.id,
                {
                    "previous_task_id": task.id,
                    "recurrence_rule": task.recurrence_rule,
                    "due_at": (
                        next_task.due_at.isoformat() if next_task.due_at else None
                    ),
                },
            )
        return TaskOperationResult(task, next_task, created)

    async def cancel(
        self, task_id: str, *, source_note_id: str | None = None
    ) -> TaskOperationResult:
        await self.task_repo.update_status(task_id, TaskStatus.CANCELLED.value)
        task = await self.task_repo.get_by_id(task_id)
        await self.event_service.append_event(
            EventType.WORK_ITEM_CHANGED,
            "task",
            task_id,
            {"action": "cancel", "source_note_id": source_note_id},
        )
        return TaskOperationResult(task)

    async def change_due(
        self, task_id: str, due_at: datetime, *, source_note_id: str | None = None
    ) -> TaskOperationResult:
        await self.task_repo.update_due_at(task_id, due_at)
        task = await self.task_repo.get_by_id(task_id)
        await self.event_service.append_event(
            EventType.WORK_ITEM_CHANGED,
            "task",
            task_id,
            {
                "action": "change_due",
                "source_note_id": source_note_id,
                "due_at": due_at.isoformat(),
            },
        )
        return TaskOperationResult(task)

    async def change_priority(
        self, task_id: str, priority: str, *, source_note_id: str | None = None
    ) -> TaskOperationResult:
        await self.task_repo.update_priority(task_id, priority)
        task = await self.task_repo.get_by_id(task_id)
        await self.event_service.append_event(
            EventType.WORK_ITEM_CHANGED,
            "task",
            task_id,
            {
                "action": "change_priority",
                "source_note_id": source_note_id,
                "priority": priority,
            },
        )
        return TaskOperationResult(task)

    async def change_recurrence(
        self, task_id: str, rule: str | None, *, source_note_id: str | None = None
    ) -> TaskOperationResult:
        await self.task_repo.update_recurrence(task_id, rule)
        task = await self.task_repo.get_by_id(task_id)
        await self.event_service.append_event(
            EventType.WORK_ITEM_CHANGED,
            "task",
            task_id,
            {
                "action": "change_recurrence",
                "source_note_id": source_note_id,
                "recurrence_rule": rule,
            },
        )
        return TaskOperationResult(task)

    async def rename(
        self,
        task_id: str,
        title: str,
        *,
        source_note_id: str | None = None,
        transition: str = "renamed_from_conversation",
    ) -> TaskOperationResult:
        if self.activity_reconciliation_service is not None:
            result = await self.activity_reconciliation_service.rename_task(
                task_id,
                title,
                source_note_id=source_note_id,
                transition=transition,
            )
            if result is None:
                return TaskOperationResult(None)
            return TaskOperationResult(
                result.task,
                event_id=result.event_id,
                linked_artifacts_updated=result.linked_meetings_updated,
            )

        task = await self.task_repo.get_by_id(task_id)
        if task is None:
            return TaskOperationResult(None)
        await self.task_repo.update_title(task_id, title)
        updated = await self.task_repo.get_by_id(task_id)
        event = await self.event_service.append_event(
            EventType.WORK_ITEM_CHANGED,
            "task",
            task_id,
            {
                "action": "rename_task",
                "transition": transition,
                "source_note_id": source_note_id,
                "before": {
                    "id": task.id,
                    "title": task.title,
                    "person_id": task.person_id,
                    "project_id": task.project_id,
                },
                "after": {
                    "id": updated.id,
                    "title": updated.title,
                    "person_id": updated.person_id,
                    "project_id": updated.project_id,
                }
                if updated
                else {},
                "linked_meetings": [],
            },
        )
        return TaskOperationResult(updated, event_id=event.id)

    async def undo_event(self, event_id: str) -> TaskOperationResult:
        event = await self.event_service.get_event(event_id)
        if event is None or await self.event_service.was_undone(event_id):
            return TaskOperationResult(None)
        if (
            event.payload.get("action") == "rename_task"
            and self.activity_reconciliation_service is not None
        ):
            task = await self.activity_reconciliation_service.undo_event(event)
            return TaskOperationResult(task)
        return TaskOperationResult(None)
