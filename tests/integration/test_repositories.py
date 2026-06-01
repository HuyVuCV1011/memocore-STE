from datetime import UTC, datetime, timedelta

from memocore.domain.models import MemoryBucket, MemoryItem, Note, Reminder, ReminderStatus, Task


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
