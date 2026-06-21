import json
from pathlib import Path

from memocore.domain.models import Person
from memocore.domain.schemas import CaptureRequest
from memocore.services.conversation_service import ConversationService
from memocore.services.reference_resolver import ReferenceResolver
from memocore.services.secretary_service import SecretaryService


class TranscriptKnowledge:
    async def answer(self, raw_text, **kwargs):
        return f"Context {kwargs.get('entity_name')}"


async def test_transcript_fixtures(capture_service, repos):
    fixture_dir = Path(__file__).parent / "transcripts"
    await repos["projects"].find_or_create("MemoCore")
    person = await repos["people"].create(Person(display_name="Văn Nghĩa Trần"))
    secretary = SecretaryService(
        repos["tasks"],
        repos["reminders"],
        repos["followups"],
        repos["projects"],
        repos["memory"],
        person_repo=repos["people"],
    )
    service = ConversationService(
        capture_service,
        secretary,
        repos["notes"],
        repos["tasks"],
        capture_service.memory_service,
        capture_service.event_service,
        knowledge_query_service=TranscriptKnowledge(),
        reference_resolver=ReferenceResolver(
            repos["chat_contexts"], repos["projects"], repos["people"], repos["tasks"]
        ),
    )

    for transcript_path in sorted(fixture_dir.glob("*.json")):
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        for index, step in enumerate(transcript["steps"], 1):
            result = await service.handle_text(
                CaptureRequest(
                    raw_text=step["text"],
                    source_chat_id=f"evaluation:{transcript['name']}",
                    source_message_id=str(index),
                )
            )
            assert result.intent == step["expected_intent"], transcript["name"]
            if expected := step.get("expected_reply_contains"):
                assert expected in result.reply

    assert await repos["memory"].list_active_by_person(person.id) == []
