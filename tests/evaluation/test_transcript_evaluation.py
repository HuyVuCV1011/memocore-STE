import json
from datetime import datetime
from pathlib import Path

import pytest

from memocore.domain.knowledge import Organization
from memocore.domain.models import MemoryItem, Note, Person, Task
from memocore.domain.schemas import CaptureRequest
from memocore.services.conversation_service import ConversationService
from memocore.services.reference_resolver import ReferenceResolver
from memocore.services.secretary_service import SecretaryService


EXPECTED_TRANSCRIPT_COUNT = 41
DURABLE_TABLES = (
    "tasks",
    "reminders",
    "memory_items",
    "projects",
    "people",
    "meetings",
    "meeting_people",
    "followups",
    "commitments",
    "organizations",
    "decisions",
    "knowledge_relations",
    "activity_links",
    "clarification_requests",
)


class TranscriptKnowledge:
    async def answer(self, raw_text, **kwargs):
        return f"Context {kwargs.get('entity_name')}"


def _load_transcripts():
    cases = []
    fixture_dir = Path(__file__).parent / "transcripts"
    for path in sorted(fixture_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload.get("transcripts", [payload])
        cases.extend(pytest.param(item, id=item["name"]) for item in items)
    return cases


def test_transcript_corpus_has_stabilization_scale():
    assert len(_load_transcripts()) == EXPECTED_TRANSCRIPT_COUNT


def test_transcript_count_matches_product_docs():
    repo_root = Path(__file__).parents[2]
    expected_phrases = {
        "README.md": (
            f"| Stability corpus | {EXPECTED_TRANSCRIPT_COUNT} isolated multi-turn conversations"
        ),
        "docs/conversation-stability-gates.md": (
            f"The offline corpus contains {EXPECTED_TRANSCRIPT_COUNT} isolated conversations."
        ),
    }
    for relative_path, expected in expected_phrases.items():
        text = (repo_root / relative_path).read_text(encoding="utf-8")
        assert expected in text


@pytest.mark.parametrize("transcript", _load_transcripts())
async def test_transcript_fixtures(transcript, capture_service, repos):
    await _seed(transcript.get("setup", {}), repos)
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
            repos["chat_contexts"],
            repos["projects"],
            repos["people"],
            repos["tasks"],
            repos["organizations"],
        ),
    )
    chat_id = f"evaluation:{transcript['name']}"
    previous = await _durable_snapshot(repos)
    for index, step in enumerate(transcript["steps"], 1):
        result = await service.handle_text(
            CaptureRequest(
                raw_text=step["text"],
                source_chat_id=chat_id,
                source_message_id=str(index),
            )
        )
        assert result.intent == step["expected_intent"]
        if expected := step.get("expected_reply_contains"):
            assert expected in result.reply
        current = await _durable_snapshot(repos)
        if step.get("expected_no_durable_writes"):
            assert current == previous
        if step.get("expected_durable_change"):
            assert current["durable_state"] != previous["durable_state"]
        for key, delta in step.get("expected_delta", {}).items():
            assert current[key] - previous[key] == delta
        if expected_focus := step.get("expected_focus"):
            context = await repos["chat_contexts"].get(chat_id)
            assert context is not None
            assert context.focused_entity_type == expected_focus["type"]
            entity = await _entity_by_name(expected_focus["type"], expected_focus["name"], repos)
            assert context.focused_entity_id == entity.id
        previous = current

    expected = transcript.get("expected_final", {})
    memories = await repos["memory"].list_all()
    tasks = await repos["tasks"].list_active()
    if "memory_contents" in expected:
        assert {item.content for item in memories} == set(expected["memory_contents"])
    if "memory_statuses" in expected:
        statuses = {item.content: str(item.status) for item in memories}
        assert expected["memory_statuses"].items() <= statuses.items()
    if "active_task_titles" in expected:
        assert {task.title for task in tasks} == set(expected["active_task_titles"])


async def _seed(setup, repos):
    projects = {}
    people = {}
    organizations = {}
    for name in setup.get("projects", []):
        projects[name] = await repos["projects"].find_or_create(name)
    for name in setup.get("people", []):
        people[name] = await repos["people"].create(Person(display_name=name))
    for name in setup.get("organizations", []):
        organizations[name] = await repos["organizations"].create(Organization(name=name))
    for index, item in enumerate(setup.get("tasks", []), 1):
        note = await repos["notes"].create(Note(raw_text=item["title"]))
        await repos["tasks"].create(
            Task(
                title=item["title"],
                due_at=(
                    datetime.fromisoformat(item["due_at"])
                    if item.get("due_at")
                    else None
                ),
                source_note_id=note.id,
                project_id=projects.get(item.get("project"), None).id
                if item.get("project")
                else None,
                person_id=people.get(item.get("person"), None).id
                if item.get("person")
                else None,
                confidence=1.0,
                recurrence_rule=item.get("recurrence_rule"),
                recurrence_series_id=item.get("recurrence_series_id"),
                recurrence_occurrence_at=(
                    datetime.fromisoformat(item["due_at"])
                    if item.get("recurrence_rule") and item.get("due_at")
                    else None
                ),
                duration_minutes=item.get("duration_minutes"),
            )
        )
    for item in setup.get("memories", []):
        note = await repos["notes"].create(Note(raw_text=item["content"]))
        await repos["memory"].create(
            MemoryItem(
                bucket=item.get("bucket", "project"),
                kind=item.get("kind", "fact"),
                content=item["content"],
                source_note_id=note.id,
                project_id=projects.get(item.get("project"), None).id
                if item.get("project")
                else None,
                person_id=people.get(item.get("person"), None).id
                if item.get("person")
                else None,
                organization_id=organizations.get(item.get("organization"), None).id
                if item.get("organization")
                else None,
                confidence=1.0,
            )
        )


async def _durable_snapshot(repos):
    tables = {
        table: await _rows(repos["tasks"], table)
        for table in DURABLE_TABLES
    }
    return {
        "tasks": len(await repos["tasks"].list_active()),
        "memories": len(await repos["memory"].list_all()),
        "reminders": len(await repos["reminders"].list_recent(1000)),
        "meetings": len(tables["meetings"]),
        "followups": len(tables["followups"]),
        "commitments": len(tables["commitments"]),
        "people": len(tables["people"]),
        "projects": len(tables["projects"]),
        "organizations": len(tables["organizations"]),
        "decisions": len(tables["decisions"]),
        "knowledge_relations": len(tables["knowledge_relations"]),
        "clarifications": len(tables["clarification_requests"]),
        "durable_state": {
            table: _row_fingerprint(rows)
            for table, rows in tables.items()
        },
    }


async def _rows(repo, table):
    conn = await repo.database.connection()
    return await (await conn.execute(f"SELECT * FROM {table}")).fetchall()


def _row_fingerprint(rows) -> tuple:
    return tuple(
        sorted(
            tuple((key, row[key]) for key in row.keys())
            for row in rows
        )
    )


async def _entity_by_name(entity_type, name, repos):
    if entity_type == "project":
        return await repos["projects"].find_by_name_or_alias(name)
    if entity_type == "person":
        return next(item for item in await repos["people"].list_all() if item.display_name == name)
    return next(item for item in await repos["organizations"].list_all() if item.name == name)
