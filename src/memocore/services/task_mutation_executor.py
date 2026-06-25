from __future__ import annotations

from collections.abc import Awaitable, Callable

from memocore.services.conversation_executor import ExecutorResult


TaskHandler = Callable[[], Awaitable[str | tuple[str, object]]]


class TaskMutationExecutor:
    """Single dispatch boundary for durable task mutations."""

    INTENTS = {
        "mark_task_done", "update_task_priority", "update_task_recurrence",
        "assign_task_to_person", "create_task_check_reminder", "delete_all_tasks",
        "cancel_task", "update_task", "update_task_due", "rename_task",
        "merge_tasks",
        "undo_last_action",
    }
    CAPTURED_INTENTS = {"assign_task_to_person", "create_task_check_reminder"}

    async def execute(
        self, intent: str, handlers: dict[str, TaskHandler]
    ) -> ExecutorResult | None:
        handler = handlers.get(intent)
        if intent not in self.INTENTS or handler is None:
            return None
        result = await handler()
        if isinstance(result, tuple):
            reply, markup = result
            return ExecutorResult(intent, reply, reply_markup=markup)
        return ExecutorResult(intent, result, captured=intent in self.CAPTURED_INTENTS)
