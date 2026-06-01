import json

import pytest

from memocore.adapters.llm.base import (
    ChatRequest,
    ChatResponse,
    ExtractionError,
    ProviderInfo,
    StructuredOutputMode,
)
from memocore.domain.schemas import NoteExtraction
from memocore.services.task_extraction_service import ExtractionService, _coerce_extraction


# ---------------------------------------------------------------------------
# Lightweight provider stub – just enough to construct ExtractionService
# ---------------------------------------------------------------------------

class _StubProvider:
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo("stub", "stub-model", StructuredOutputMode.JSON_MODE)

    async def health_check(self) -> bool:
        return True

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(content="{}", model="stub-model")


def _service() -> ExtractionService:
    return ExtractionService(_StubProvider())


# ---------------------------------------------------------------------------
# _coerce_extraction tests
# ---------------------------------------------------------------------------

def test_coerce_none_arrays():
    data = {"summary": "test", "tags": None, "tasks": None, "reminders": None,
            "projects": None, "memories": None}
    result = _coerce_extraction(data)
    for key in ("tags", "tasks", "reminders", "projects", "memories"):
        assert result[key] == []


def test_coerce_dict_to_list():
    data = {"summary": "test", "tasks": {"title": "Buy milk", "confidence": 0.8}}
    result = _coerce_extraction(data)
    assert isinstance(result["tasks"], list)
    assert len(result["tasks"]) == 1
    assert result["tasks"][0]["title"] == "Buy milk"


def test_coerce_numeric_priority():
    data = {"summary": "test", "tasks": [{"title": "A task", "priority": 1}]}
    result = _coerce_extraction(data)
    assert result["tasks"][0]["priority"] == "1"


def test_coerce_preserves_valid():
    data = {
        "summary": "test",
        "tags": ["a"],
        "tasks": [{"title": "Task", "priority": "high"}],
        "reminders": [],
        "projects": [],
        "memories": [],
    }
    original = json.loads(json.dumps(data))  # deep copy
    result = _coerce_extraction(data)
    assert result["summary"] == original["summary"]
    assert result["tags"] == original["tags"]
    assert result["tasks"][0]["priority"] == "high"


# ---------------------------------------------------------------------------
# ExtractionService._validate tests
# ---------------------------------------------------------------------------

def test_validate_rejects_schema_return():
    service = _service()
    schema_json = json.dumps({
        "$defs": {"TaskCandidate": {}},
        "properties": {"summary": {"type": "string"}},
    })
    with pytest.raises(ExtractionError, match="JSON schema"):
        service._validate(schema_json)


def test_validate_rejects_invalid_json():
    service = _service()
    with pytest.raises(ExtractionError, match="invalid extraction JSON"):
        service._validate("this is not json at all")


def test_validate_accepts_minimal():
    service = _service()
    result = service._validate('{"summary":"test"}')
    assert isinstance(result, NoteExtraction)
    assert result.summary == "test"
    assert result.tasks == []
    assert result.reminders == []
    assert result.projects == []
    assert result.memories == []
