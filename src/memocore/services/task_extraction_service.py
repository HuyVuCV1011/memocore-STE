from __future__ import annotations

import json
import logging
from typing import Any
from datetime import date, datetime, timedelta
from importlib.resources import files

from pydantic import ValidationError

from memocore.adapters.llm.base import (
    ChatMessage,
    ChatRequest,
    ExtractionError,
    ModelProvider,
    StructuredOutputMode,
)
from memocore.domain.schemas import NoteExtraction

logger = logging.getLogger(__name__)


class ExtractionService:
    def __init__(self, provider: ModelProvider, temperature: float = 0.0):
        self.provider = provider
        self.temperature = temperature
        self.system_template = (
            files("memocore.prompts").joinpath("system_extraction.md").read_text(encoding="utf-8")
        )
        self.user_template = (
            files("memocore.prompts").joinpath("user_extraction.md").read_text(encoding="utf-8")
        )

    async def extract(self, raw_text: str, context: str = "") -> NoteExtraction:
        errors: list[ExtractionError] = []
        providers = getattr(self.provider, "providers", (self.provider,))
        for provider in providers:
            for attempt in range(2):
                retry_context = context
                if attempt:
                    retry_context = (
                        f"{context}\n\n"
                        "The previous response was invalid. Return extraction data, not its schema."
                    )
                try:
                    response = await provider.chat(self._build_request(raw_text, retry_context, provider))
                    return self._validate(response.content)
                except ExtractionError as exc:
                    errors.append(exc)
        raise ExtractionError("All extraction attempts failed") from errors[-1]

    def _build_request(
        self, raw_text: str, context: str = "", provider: ModelProvider | None = None
    ) -> ChatRequest:
        now = datetime.now().astimezone()
        next_monday = _next_weekday(now.date(), weekday=0)
        user_prompt = self.user_template.format(
            context=context or "(none)",
            current_datetime=now.isoformat(),
            current_date=now.date().isoformat(),
            tomorrow_date=(now.date() + timedelta(days=1)).isoformat(),
            next_monday_date=next_monday.isoformat(),
            raw_text=raw_text,
        )
        active_provider = provider or self.provider
        mode = active_provider.info.supports_structured_output
        schema = NoteExtraction.model_json_schema() if mode == StructuredOutputMode.JSON_SCHEMA else None
        return ChatRequest(
            messages=[
                ChatMessage(role="system", content=self.system_template),
                ChatMessage(role="user", content=user_prompt),
            ],
            temperature=self.temperature,
            response_format=mode,
            json_schema=schema,
        )

    def _validate(self, content: str) -> NoteExtraction:
        try:
            decoded = _decode_json_content(content)
            if isinstance(decoded, dict) and "summary" not in decoded and (
                "$defs" in decoded or "properties" in decoded
            ):
                raise ExtractionError("Model returned the JSON schema instead of extraction data")
            return NoteExtraction.model_validate(_coerce_extraction(decoded))
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            logger.warning("Invalid model extraction output", exc_info=exc)
            raise ExtractionError("Model returned invalid extraction JSON") from exc


def _coerce_extraction(decoded: Any) -> Any:
    if not isinstance(decoded, dict):
        return decoded
    for key in ("tags", "tasks", "reminders", "projects", "memories"):
        value = decoded.get(key)
        if value is None:
            decoded[key] = []
        elif key != "tags" and isinstance(value, dict):
            decoded[key] = [value]
    for task in decoded.get("tasks", []):
        if isinstance(task, dict) and "priority" in task and not isinstance(task["priority"], str):
            task["priority"] = str(task["priority"])
    return decoded


def _decode_json_content(content: str) -> Any:
    try:
        return json.loads(content)
    except json.JSONDecodeError as original:
        decoder = json.JSONDecoder()
        for index, char in enumerate(content):
            if char not in "{[":
                continue
            try:
                decoded, _ = decoder.raw_decode(content[index:])
            except json.JSONDecodeError:
                continue
            return decoded
        raise original


def _next_weekday(current: date, weekday: int) -> date:
    days_ahead = (weekday - current.weekday()) % 7
    return current + timedelta(days=days_ahead or 7)
