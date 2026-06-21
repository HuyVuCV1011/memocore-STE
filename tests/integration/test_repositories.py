from datetime import UTC, datetime, timedelta

from memocore.domain.models import (
    MemoryBucket,
    MemoryItem,
    Note,
    Person,
    Reminder,
    ReminderStatus,
    Task,
)


async def test_person_matching_does_not_silently_choose_substring_match(repos):
    an = await repos["people"].create(Person(display_name="Nguyễn Cảnh An"))
    another_an = await repos["people"].create(Person(display_name="Duyên Nguyễn An Huỳnh"))

    matches = await repos["people"].find_matches("an")
    resolved = await repos["people"].find_by_name_or_alias("an")

    assert {person.id for person in matches} == {an.id, another_an.id}
    assert resolved is None


async def test_person_matching_prefers_exact_alias_over_token_matches(repos):
    exact = await repos["people"].create(
        Person(display_name="Nguyễn Cảnh An", aliases=["An"])
    )
    await repos["people"].create(Person(display_name="Duyên Nguyễn An Huỳnh"))

    matches = await repos["people"].find_matches("an")

    assert [person.id for person in matches] == [exact.id]


async def test_project_matching_returns_ambiguity_instead_of_first_result(repos):
    first = await repos["projects"].find_or_create("STE Dashboard")
    second = await repos["projects"].find_or_create("MindX Dashboard")

    matches = await repos["projects"].find_matches("dashboard")
    resolved = await repos["projects"].find_by_name_or_alias("dashboard")

    assert {project.id for project in matches} == {first.id, second.id}
    assert resolved is None


async def test_repositories_insert_read_update(repos):
    note = await repos["notes"].create(Note(raw_text="hello", source_chat_id="123"))
    fetched_note = await repos["notes"].get_by_id(note.id)
    project = await repos["projects"].find_or_create("Project A")
    task = await repos["tasks"].create(
        Task(title="Do thing", source_note_id=note.id, project_id=project.id, confidence=0.7)
    )
    reminder = await repos["reminders"].create(
        Reminder(
            title="Remember thing",
            source_note_id=note.id,
            remind_at=datetime.now(UTC) - timedelta(seconds=1),
            confidence=0.8,
        )
    )
    await repos["reminders"].update_status(reminder.id, ReminderStatus.SCHEDULED)
    memory = await repos["memory"].create(
        MemoryItem(
            bucket="profile",
            kind="fact",
            content="A fact",
            source_note_id=note.id,
            confidence=0.8,
        )
    )

    due = await repos["reminders"].find_due(datetime.now(UTC))

    assert fetched_note.source_chat_id == "123"
    assert (await repos["tasks"].list_by_note(note.id))[0].id == task.id
    assert due[0].id == reminder.id
    assert (await repos["memory"].list_by_bucket(MemoryBucket.PROFILE))[0].id == memory.id
