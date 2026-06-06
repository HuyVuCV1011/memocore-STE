import pytest
from importlib.resources import files
from pydantic import ValidationError

from memocore.domain.models import MemoryBucket, MemoryKind
from memocore.domain.schemas import IntentClassification, MemoryCandidate, NoteExtraction, TaskCandidate


def test_note_extraction_defaults_and_schema():
    extraction = NoteExtraction(summary="Saved note")

    assert extraction.tasks == []
    assert "properties" in NoteExtraction.model_json_schema()


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        TaskCandidate(title="x", confidence=1.1)


def test_memory_enum_validation():
    memory = MemoryCandidate(
        bucket=MemoryBucket.PROFILE,
        kind=MemoryKind.PREFERENCE,
        content="Likes concise updates.",
        confidence=0.8,
    )

    assert memory.bucket == MemoryBucket.PROFILE


def test_intent_prompt_documents_runtime_intents():
    prompt = (
        files("memocore.prompts")
        .joinpath("system_intent_classification.md")
        .read_text(encoding="utf-8")
    )
    intent_literal = IntentClassification.model_fields["intent"].annotation
    runtime_intents = set(intent_literal.__args__)

    missing = sorted(intent for intent in runtime_intents if f"`{intent}`" not in prompt)

    assert missing == []
