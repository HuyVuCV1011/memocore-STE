from __future__ import annotations

from collections.abc import Awaitable, Callable

from memocore.services.conversation_executor import ExecutorResult


MemoryHandler = Callable[[], Awaitable[ExecutorResult | str]]


class MemoryLifecycleExecutor:
    """Dispatch boundary for scoped writes, corrections, deletion and rollback."""

    INTENTS = {
        "update_knowledge", "rollback_knowledge_update", "memory_delete",
        "correction_feedback", "memory_correction",
    }

    async def execute(
        self, intent: str, handlers: dict[str, MemoryHandler]
    ) -> ExecutorResult | None:
        handler = handlers.get(intent)
        if intent not in self.INTENTS or handler is None:
            return None
        result = await handler()
        if isinstance(result, ExecutorResult):
            return result
        if hasattr(result, "reply") and hasattr(result, "intent"):
            return ExecutorResult(
                result.intent,
                result.reply,
                captured=getattr(result, "captured", False),
                reply_markup=getattr(result, "reply_markup", None),
            )
        return ExecutorResult(intent, result)
