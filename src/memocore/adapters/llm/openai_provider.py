from __future__ import annotations

import json
from typing import Any

import httpx

from memocore.adapters.llm.base import (
    ChatRequest,
    ChatResponse,
    ExtractionError,
    ProviderInfo,
    StructuredOutputMode,
)


class OpenAICompatibleProvider:
    def __init__(
        self,
        provider_name: str,
        base_url: str,
        model: str,
        api_key: str,
        timeout: float = 60.0,
        structured_output_mode: StructuredOutputMode = StructuredOutputMode.JSON_MODE,
    ):
        self.provider_name = provider_name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.structured_output_mode = structured_output_mode

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            provider_name=self.provider_name,
            model_name=self.model,
            supports_structured_output=self.structured_output_mode,
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout, 5.0)) as client:
                response = await client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                )
                response.raise_for_status()
                return True
        except httpx.HTTPError:
            return False

    async def chat(self, request: ChatRequest) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        response_format = request.response_format
        if response_format == StructuredOutputMode.JSON_SCHEMA and request.json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "note_extraction",
                    "strict": True,
                    "schema": request.json_schema,
                },
            }
        elif response_format == StructuredOutputMode.JSON_MODE:
            payload["response_format"] = {"type": "json_object"}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise ExtractionError(f"{self.provider_name} request failed: {exc}") from exc

        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ExtractionError(f"{self.provider_name} response did not include choices")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ExtractionError(f"{self.provider_name} response did not include message.content")

        usage = body.get("usage") if isinstance(body.get("usage"), dict) else None
        usage_counts = {key: int(value) for key, value in usage.items()} if usage else None
        return ChatResponse(
            content=message["content"],
            model=str(body.get("model") or self.model),
            usage=usage_counts,
            raw_response=body,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
