from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from memocore.adapters.llm.base import (
    ChatRequest,
    ChatResponse,
    ExtractionError,
    ProviderInfo,
    StructuredOutputMode,
)

logger = logging.getLogger(__name__)


class OllamaProvider:
    def __init__(self, base_url: str, model: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            provider_name="ollama",
            model_name=self.model,
            supports_structured_output=StructuredOutputMode.JSON_MODE,
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout, 5.0)) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                return True
        except httpx.HTTPError:
            return False

    async def chat(self, request: ChatRequest) -> ChatResponse:
        body = await self._chat(request)
        content = _extract_content(body)
        return ChatResponse(content=content, model=self.model, raw_response=body)

    async def _chat(self, request: ChatRequest) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": request.temperature}
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "options": options,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
        }
        if request.response_format in {
            StructuredOutputMode.JSON_MODE,
            StructuredOutputMode.JSON_SCHEMA,
        }:
            payload["format"] = "json"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise ExtractionError(f"Ollama request failed: {exc}") from exc


def _extract_content(body: dict[str, Any]) -> str:
    message = body.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"]
    if isinstance(body.get("response"), str):
        return body["response"]
    raise ExtractionError("Ollama response did not include message.content")
