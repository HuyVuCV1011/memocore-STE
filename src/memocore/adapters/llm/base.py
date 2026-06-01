from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

class StructuredOutputMode(StrEnum):
    PROMPT_ONLY = "prompt_only"
    JSON_MODE = "json"
    JSON_SCHEMA = "json_schema"


@dataclass(frozen=True)
class ProviderInfo:
    provider_name: str
    model_name: str
    supports_structured_output: StructuredOutputMode
    max_context_tokens: int | None = None


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ChatRequest:
    messages: list[ChatMessage]
    temperature: float = 0.0
    response_format: StructuredOutputMode = StructuredOutputMode.PROMPT_ONLY
    json_schema: dict[str, Any] | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class ChatResponse:
    content: str
    model: str
    usage: dict[str, int] | None = None
    raw_response: dict[str, Any] | None = None


class ExtractionError(RuntimeError):
    pass


class ModelProvider(Protocol):
    @property
    def info(self) -> ProviderInfo:
        ...

    async def chat(self, request: ChatRequest) -> ChatResponse:
        ...

    async def health_check(self) -> bool:
        ...
