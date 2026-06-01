import json

import pytest

from memocore.adapters.llm.base import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ExtractionError,
    ProviderInfo,
    StructuredOutputMode,
)
from memocore.adapters.llm.provider_factory import FallbackProvider
from memocore.services.task_extraction_service import ExtractionService


class StubProvider:
    """Configurable fake provider for fallback tests."""

    def __init__(
        self,
        name: str = "stub",
        *,
        response_content: str = '{"summary":"stub"}',
        healthy: bool = True,
        raise_on_chat: bool = False,
    ):
        self._name = name
        self._response_content = response_content
        self._healthy = healthy
        self._raise_on_chat = raise_on_chat
        self.chat_calls: list[ChatRequest] = []

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(self._name, f"{self._name}-model", StructuredOutputMode.JSON_MODE)

    async def health_check(self) -> bool:
        return self._healthy

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.chat_calls.append(request)
        if self._raise_on_chat:
            raise ExtractionError(f"{self._name} failed")
        return ChatResponse(content=self._response_content, model=f"{self._name}-model")


def _make_request():
    return ChatRequest(messages=[ChatMessage(role="user", content="hello")])


async def test_fallback_uses_primary_on_success():
    primary = StubProvider("primary", response_content='{"summary":"primary-ok"}')
    fallback = StubProvider("fallback", response_content='{"summary":"fallback-ok"}')
    provider = FallbackProvider(primary, fallback)

    result = await provider.chat(_make_request())

    assert result.content == '{"summary":"primary-ok"}'
    assert len(primary.chat_calls) == 1
    assert len(fallback.chat_calls) == 0


async def test_fallback_uses_fallback_on_error():
    primary = StubProvider("primary", raise_on_chat=True)
    fallback = StubProvider("fallback", response_content='{"summary":"fallback-ok"}')
    provider = FallbackProvider(primary, fallback)

    result = await provider.chat(_make_request())

    assert result.content == '{"summary":"fallback-ok"}'
    assert len(primary.chat_calls) == 1
    assert len(fallback.chat_calls) == 1


async def test_fallback_health_check_true_if_either_healthy():
    primary = StubProvider("primary", healthy=False)
    fallback = StubProvider("fallback", healthy=True)
    provider = FallbackProvider(primary, fallback)

    assert await provider.health_check() is True


async def test_fallback_health_check_false_if_both_unhealthy():
    primary = StubProvider("primary", healthy=False)
    fallback = StubProvider("fallback", healthy=False)
    provider = FallbackProvider(primary, fallback)

    assert await provider.health_check() is False


async def test_fallback_info_returns_primary_info():
    primary = StubProvider("primary")
    fallback = StubProvider("fallback")
    provider = FallbackProvider(primary, fallback)

    info = provider.info
    assert info.provider_name == "primary"
    assert info.model_name == "primary-model"


async def test_extraction_falls_back_when_primary_returns_invalid_json():
    primary = StubProvider("primary", response_content="not-json")
    fallback = StubProvider(
        "fallback",
        response_content='{"summary":"fallback-ok","tags":[],"tasks":[],"reminders":[],"projects":[],"memories":[]}',
    )

    result = await ExtractionService(FallbackProvider(primary, fallback)).extract("hello")

    assert result.summary == "fallback-ok"
    assert len(primary.chat_calls) == 2
    assert len(fallback.chat_calls) == 1
