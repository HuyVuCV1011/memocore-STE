from __future__ import annotations

import json
import logging
from datetime import datetime
from importlib.resources import files

from pydantic import ValidationError

from memocore.adapters.llm.base import (
    ChatMessage,
    ChatRequest,
    ExtractionError,
    ModelProvider,
    StructuredOutputMode,
)
from memocore.domain.schemas import IntentClassification

logger = logging.getLogger(__name__)


class IntentClassifierService:
    def __init__(self, provider: ModelProvider, temperature: float = 0.0):
        self.provider = provider
        self.temperature = temperature
        self.system_template = (
            files("memocore.prompts")
            .joinpath("system_intent_classification.md")
            .read_text(encoding="utf-8")
        )
        self.user_template = (
            files("memocore.prompts")
            .joinpath("user_intent_classification.md")
            .read_text(encoding="utf-8")
        )

    async def classify(
        self, raw_text: str, *, context: str = ""
    ) -> IntentClassification:
        errors: list[ExtractionError] = []
        providers = getattr(self.provider, "providers", (self.provider,))
        for provider in providers:
            for attempt in range(2):
                try:
                    response = await provider.chat(
                        self._build_request(raw_text, provider, context=context)
                    )
                    return self._validate(response.content)
                except ExtractionError as exc:
                    errors.append(exc)
        logger.warning("All classification attempts failed, falling back to casual_or_noop")
        return IntentClassification(
            intent="casual_or_noop",
            confidence=0.5,
            ambiguity_detected=True,
            clarification_question="I'm not sure what you mean. Could you please rephrase?",
        )

    def _build_request(
        self,
        raw_text: str,
        provider: ModelProvider | None = None,
        *,
        context: str = "",
    ) -> ChatRequest:
        now = datetime.now().astimezone()
        user_prompt = self.user_template.format(
            current_datetime=now.isoformat(),
            current_date=now.date().isoformat(),
            conversation_context=context or "(none)",
            raw_text=raw_text,
        )
        active_provider = provider or self.provider
        mode = active_provider.info.supports_structured_output
        schema = (
            IntentClassification.model_json_schema()
            if mode == StructuredOutputMode.JSON_SCHEMA
            else None
        )
        return ChatRequest(
            messages=[
                ChatMessage(role="system", content=self.system_template),
                ChatMessage(role="user", content=user_prompt),
            ],
            temperature=self.temperature,
            response_format=mode,
            json_schema=schema,
        )

    def _validate(self, content: str) -> IntentClassification:
        try:
            decoded = _decode_json_content(content)
            if (
                isinstance(decoded, dict)
                and "intent" not in decoded
                and ("$defs" in decoded or "properties" in decoded)
            ):
                raise ExtractionError("Model returned the JSON schema instead of classification data")
            return IntentClassification.model_validate(decoded)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            logger.warning("Invalid model classification output: %s", content, exc_info=exc)
            raise ExtractionError("Model returned invalid classification JSON") from exc


def _decode_json_content(content: str) -> any:
    try:
        return json.loads(content)
    except json.JSONDecodeError as original:
        decoder = json.JSONDecoder()
        for index, char in enumerate(content):
            if char not in "{[":
                continue
            try:
                decoded, _ = decoder.raw_decode(content[index:])
                return decoded
            except json.JSONDecodeError:
                continue
        raise original
