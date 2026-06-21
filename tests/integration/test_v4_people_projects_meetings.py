from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

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
    NoteStatus,
    Person,
    Task,
)
from memocore.domain.schemas import CaptureRequest
from memocore.services.conversation_service import ConversationService, classify_intent
from memocore.services.secretary_service import SecretaryService
from tests.fixtures.extraction_responses import V4_NATURAL_CAPTURE


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
    assert "người khác nợ anh" in commitments


async def test_person_context_asks_for_full_name_when_query_is_ambiguous(repos):
    await repos["people"].create(Person(display_name="Nguyễn Cảnh An"))
    await repos["people"].create(Person(display_name="Duyên Nguyễn An Huỳnh"))

    response = await _secretary(repos).person_context("an")

    assert "nhiều person cùng khớp" in response
    assert "Nguyễn Cảnh An" in response
    assert "Duyên Nguyễn An Huỳnh" in response


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


async def test_v45_natural_capture_persists_v4_entities_and_retrieves_context(
    capture_service, fake_provider, repos
):
    fake_provider.response = V4_NATURAL_CAPTURE

    response = await capture_service.capture(
        CaptureRequest(
            raw_text="Alex Nguyen is the MindX reviewer. Meet Alex Nguyen for MindX review on 2099-06-02. Alex Nguyen owes MindX feedback.",
            source_chat_id="9001",
            source_message_id="v45",
        )
    )

    people = await repos["people"].list_all()
    tasks = await repos["tasks"].list_by_note(response.note_id)
    meetings = await repos["meetings"].list_by_note(response.note_id)
    followups = await repos["followups"].list_by_note(response.note_id)
    commitments = await repos["commitments"].list_by_note(response.note_id)
    memories = await repos["memory"].list_by_note(response.note_id)
    context = await _secretary(repos).person_context("Alex")

    assert len(people) == 1
    assert response.people_created == 1
    assert response.meetings_created == 1
    assert response.followups_created == 1
    assert response.commitments_created == 1
    assert tasks[0].person_id == people[0].id
    assert meetings[0].person_id == people[0].id
    assert followups[0].person_id == people[0].id
    assert commitments[0].person_id == people[0].id
    assert memories[0].person_id == people[0].id
    assert "Alex Nguyen owes MindX feedback" in context
    assert "MindX review with Alex Nguyen" in context

    duplicate = await capture_service.capture(
        CaptureRequest(
            raw_text="duplicate",
            source_chat_id="9001",
            source_message_id="v45",
        )
    )
    assert duplicate.duplicate is True
    assert len(await repos["people"].list_all()) == 1
    assert len(await repos["meetings"].list_by_note(response.note_id)) == 1


async def test_v46_ambiguous_person_candidate_is_not_persisted_or_linked(
    capture_service, fake_provider, repos
):
    extraction = V4_NATURAL_CAPTURE.model_copy(deep=True)
    extraction.people[0].display_name = "client"
    extraction.people[0].aliases = []
    extraction.tasks[0].person_name = "client"
    extraction.meetings[0].person_names = ["client"]
    extraction.followups[0].person_name = "client"
    extraction.commitments[0].person_name = "client"
    extraction.memories[0].person_name = "client"
    fake_provider.response = extraction

    response = await capture_service.capture(
        CaptureRequest(raw_text="The client mentioned MindX follow-up and feedback.")
    )

    assert await repos["people"].list_all() == []
    assert (await repos["tasks"].list_by_note(response.note_id))[0].person_id is None
    assert await repos["meetings"].list_by_note(response.note_id) == []
    assert await repos["followups"].list_by_note(response.note_id) == []
    assert await repos["commitments"].list_by_note(response.note_id) == []
    assert (await repos["memory"].list_by_note(response.note_id))[0].person_id is None
    assert response.meetings_created == 0
    assert response.followups_created == 0
    assert response.commitments_created == 0
    events = await repos["events"].list_by_entity("note", response.note_id)
    warning_events = [
        event for event in events if event.event_type.value == "extraction_likely_incomplete"
    ]
    assert warning_events


async def test_v47_explicit_person_candidate_uses_exact_match_not_substring(
    capture_service, fake_provider, repos
):
    existing = await repos["people"].create(Person(display_name="Alex"))
    extraction = V4_NATURAL_CAPTURE.model_copy(deep=True)
    extraction.people[0].display_name = "Alexandra"
    extraction.people[0].aliases = []
    extraction.tasks[0].person_name = "Alexandra"
    extraction.meetings[0].person_names = ["Alexandra"]
    extraction.followups[0].person_name = "Alexandra"
    extraction.commitments[0].person_name = "Alexandra"
    extraction.memories[0].person_name = "Alexandra"
    fake_provider.response = extraction

    response = await capture_service.capture(
        CaptureRequest(
            raw_text="Alexandra is the MindX reviewer and owes feedback.",
            source_chat_id="9001",
            source_message_id="v47",
        )
    )

    people = await repos["people"].list_all()
    alexandra = next(person for person in people if person.display_name == "Alexandra")
    assert {person.display_name for person in people} == {"Alex", "Alexandra"}
    assert alexandra.id != existing.id
    assert (await repos["tasks"].list_by_note(response.note_id))[0].person_id == alexandra.id
    assert (await repos["meetings"].list_by_note(response.note_id))[0].person_id == alexandra.id
    assert (await repos["followups"].list_by_note(response.note_id))[0].person_id == alexandra.id
    assert (await repos["commitments"].list_by_note(response.note_id))[0].person_id == alexandra.id
    assert (await repos["memory"].list_by_note(response.note_id))[0].person_id == alexandra.id


async def test_v48_v4_entities_without_project_name_do_not_inherit_explicit_project(
    capture_service, fake_provider, repos
):
    extraction = V4_NATURAL_CAPTURE.model_copy(deep=True)
    extraction.meetings[0].project_name = None
    extraction.followups[0].project_name = None
    extraction.commitments[0].project_name = None
    fake_provider.response = extraction

    response = await capture_service.capture(
        CaptureRequest(
            raw_text="Alex Nguyen is the reviewer for MindX. Meet Alex Nguyen and ask for feedback.",
            source_chat_id="9001",
            source_message_id="v48",
        )
    )

    assert (await repos["tasks"].list_by_note(response.note_id))[0].project_id is not None
    assert (await repos["memory"].list_by_note(response.note_id))[0].project_id is not None
    assert (await repos["meetings"].list_by_note(response.note_id))[0].project_id is None
    assert (await repos["followups"].list_by_note(response.note_id))[0].project_id is None
    assert (await repos["commitments"].list_by_note(response.note_id))[0].project_id is None


async def test_v49_commitment_without_direction_is_not_persisted(
    capture_service, fake_provider, repos
):
    extraction = V4_NATURAL_CAPTURE.model_copy(deep=True)
    extraction.commitments[0].direction = None
    fake_provider.response = extraction

    response = await capture_service.capture(
        CaptureRequest(
            raw_text="Alex Nguyen mentioned an unclear MindX obligation.",
            source_chat_id="9001",
            source_message_id="v49",
        )
    )

    assert await repos["commitments"].list_by_note(response.note_id) == []
    assert response.commitments_created == 0


async def test_v410_vague_alias_does_not_link_entities(
    capture_service, fake_provider, repos
):
    extraction = V4_NATURAL_CAPTURE.model_copy(deep=True)
    extraction.people[0].aliases = ["client"]
    extraction.meetings[0].person_names = ["client"]
    extraction.followups[0].person_name = "client"
    extraction.commitments[0].person_name = "client"
    fake_provider.response = extraction

    response = await capture_service.capture(
        CaptureRequest(
            raw_text="Alex Nguyen is the client for MindX. The client owes feedback.",
            source_chat_id="9001",
            source_message_id="v410",
        )
    )

    person = (await repos["people"].list_all())[0]
    assert person.aliases == []
    assert await repos["meetings"].list_by_note(response.note_id) == []
    assert await repos["followups"].list_by_note(response.note_id) == []
    assert await repos["commitments"].list_by_note(response.note_id) == []


async def test_v411_failed_v4_persistence_rolls_back_and_same_message_can_retry(
    capture_service, fake_provider, repos, monkeypatch
):
    fake_provider.response = V4_NATURAL_CAPTURE
    original_create = repos["followups"].create
    attempts = 0

    async def fail_once(followup):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected follow-up failure")
        return await original_create(followup)

    monkeypatch.setattr(repos["followups"], "create", fail_once)
    request = CaptureRequest(
        raw_text="Alex Nguyen is the MindX reviewer and owes feedback.",
        source_chat_id="9001",
        source_message_id="v411",
    )

    with pytest.raises(RuntimeError, match="injected follow-up failure"):
        await capture_service.capture(request)

    failed_note = await repos["notes"].find_by_source_message("telegram", "9001", "v411")
    assert failed_note is not None
    assert failed_note.status == NoteStatus.FAILED
    assert await repos["people"].list_all() == []
    assert await repos["tasks"].list_by_note(failed_note.id) == []
    assert await repos["meetings"].list_by_note(failed_note.id) == []

    retry = await capture_service.capture(request)

    assert retry.duplicate is False
    assert retry.people_created == 1
    assert retry.meetings_created == 1
    assert retry.followups_created == 1
    assert retry.commitments_created == 1
