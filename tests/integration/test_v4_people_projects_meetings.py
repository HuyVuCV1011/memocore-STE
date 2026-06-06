from __future__ import annotations

from datetime import UTC, datetime, timedelta

from memocore.domain.models import (
    Commitment,
    CommitmentDirection,
    FollowUp,
    Meeting,
    MemoryBucket,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    Note,
    Person,
    Task,
)
from memocore.domain.schemas import CaptureRequest
from memocore.services.conversation_service import ConversationService, classify_intent
from memocore.services.secretary_service import SecretaryService


def _secretary(repos) -> SecretaryService:
    return SecretaryService(
        repos["tasks"],
        repos["reminders"],
        repos["followups"],
        repos["projects"],
        repos["memory"],
        meeting_repo=repos["meetings"],
        person_repo=repos["people"],
        commitment_repo=repos["commitments"],
    )


async def test_v41_person_context_links_tasks_commitments_followups_meetings_and_memory(repos):
    note = await repos["notes"].create(
        Note(raw_text="Alex context", source_chat_id="9001", source_message_id="v41")
    )
    person = await repos["people"].create(
        Person(display_name="Alex Nguyen", aliases=["Alex"], relationship="MindX reviewer")
    )
    project = await repos["projects"].find_or_create("MindX")
    await repos["tasks"].create(
        Task(title="Send Alex project brief", source_note_id=note.id, person_id=person.id, project_id=project.id)
    )
    await repos["commitments"].create(
        Commitment(
            title="Alex owes STE feedback",
            direction=CommitmentDirection.OWED_TO_USER,
            person_id=person.id,
            project_id=project.id,
            source_note_id=note.id,
        )
    )
    await repos["followups"].create(
        FollowUp(title="Ask Alex for review slot", person_id=person.id, project_id=project.id, source_note_id=note.id)
    )
    meeting = await repos["meetings"].create(
        Meeting(
            title="STE review with Alex",
            starts_at=datetime.now(UTC) + timedelta(days=1),
            person_id=person.id,
            project_id=project.id,
            source_note_id=note.id,
        )
    )
    await repos["meetings"].add_person(meeting.id, person.id, role="reviewer")
    await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROJECT,
            kind=MemoryKind.PROJECT_STATE,
            content="Alex prefers concise STE review notes.",
            source_note_id=note.id,
            person_id=person.id,
            project_id=project.id,
            status=MemoryStatus.ACTIVE,
            confidence=0.9,
        )
    )

    context = await _secretary(repos).person_context("alex")

    assert "Person Alex Nguyen" in context
    assert "MindX reviewer" in context
    assert "Send Alex project brief" in context
    assert "Alex owes STE feedback" in context
    assert "Ask Alex for review slot" in context
    assert "STE review with Alex" in context
    assert "Alex prefers concise STE review notes." in context


async def test_v42_project_context_and_meeting_prep_include_linked_operational_state(repos):
    note = await repos["notes"].create(
        Note(raw_text="STE context", source_chat_id="9001", source_message_id="v42")
    )
    project = await repos["projects"].find_or_create("MemoCore STE")
    person = await repos["people"].create(Person(display_name="Lan", aliases=["PM Lan"]))
    await repos["tasks"].create(
        Task(title="Finalize V4 schema", source_note_id=note.id, project_id=project.id, person_id=person.id)
    )
    await repos["commitments"].create(
        Commitment(
            title="Send V4 import plan to Lan",
            direction=CommitmentDirection.USER_OWES,
            person_id=person.id,
            project_id=project.id,
            source_note_id=note.id,
        )
    )
    await repos["meetings"].create(
        Meeting(title="MemoCore V4 planning", project_id=project.id, person_id=person.id, source_note_id=note.id)
    )
    await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROJECT,
            kind=MemoryKind.PROJECT_STATE,
            content="V4 should keep personal context import review-gated.",
            source_note_id=note.id,
            project_id=project.id,
            status=MemoryStatus.ACTIVE,
        )
    )

    context = await _secretary(repos).project_context("STE")
    prep = await _secretary(repos).meeting_prep("MemoCore STE")

    assert "Project MemoCore STE" in context
    assert "Finalize V4 schema" in context
    assert "Send V4 import plan to Lan" in context
    assert "V4 should keep personal context import review-gated." in context
    assert "Meeting prep cho project MemoCore STE" in prep
    assert "Commitments còn mở" in prep
    assert "MemoCore V4 planning" in prep


async def test_v43_people_and_commitments_views_are_empty_safe_and_list_real_items(repos):
    empty = await _secretary(repos).people()
    assert "Chưa có person" in empty

    note = await repos["notes"].create(
        Note(raw_text="commitment context", source_chat_id="9001", source_message_id="v43")
    )
    person = await repos["people"].create(Person(display_name="Sơn", aliases=["Son"]))
    await repos["commitments"].create(
        Commitment(
            title="Sơn owes PC quote",
            direction=CommitmentDirection.OWED_TO_USER,
            person_id=person.id,
            source_note_id=note.id,
        )
    )

    people = await _secretary(repos).people()
    commitments = await _secretary(repos).commitments()

    assert "Sơn" in people
    assert "aliases: Son" in people
    assert "Sơn owes PC quote" in commitments
    assert "người khác nợ mình" in commitments


async def test_v44_conversation_routes_meeting_prep_without_capture(capture_service, fake_provider, repos):
    note = await repos["notes"].create(
        Note(raw_text="Alex prep", source_chat_id="9001", source_message_id="v44-source")
    )
    person = await repos["people"].create(Person(display_name="Alex", aliases=["A"]))
    await repos["commitments"].create(
        Commitment(
            title="Alex owes review notes",
            direction=CommitmentDirection.OWED_TO_USER,
            person_id=person.id,
            source_note_id=note.id,
        )
    )
    secretary = _secretary(repos)
    conversation = ConversationService(
        capture_service,
        secretary,
        repos["notes"],
        repos["tasks"],
        capture_service.memory_service,
        capture_service.event_service,
    )

    assert classify_intent("chuẩn bị họp với Alex") == "query_meeting_prep"

    result = await conversation.handle_text(
        CaptureRequest(raw_text="chuẩn bị họp với Alex", source_chat_id="9001", source_message_id="v44")
    )

    assert result.intent == "query_meeting_prep"
    assert "Meeting prep với Alex" in result.reply
    assert "Alex owes review notes" in result.reply
    assert fake_provider.calls == []
