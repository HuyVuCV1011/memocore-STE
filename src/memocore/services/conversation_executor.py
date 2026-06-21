from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import TypeVar


T = TypeVar("T")


class ConversationExecutor:
    """Dispatches an already-resolved intent to an explicit action handler."""

    async def dispatch(
        self,
        intent: str,
        handlers: Mapping[str, Callable[[], Awaitable[T]]],
    ) -> T | None:
        handler = handlers.get(intent)
        return await handler() if handler is not None else None
