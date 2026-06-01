from datetime import UTC, datetime, timedelta

from memocore.domain.models import Note
from memocore.domain.schemas import ReminderCandidate
from memocore.services.event_service import EventService
from memocore.services.reminder_service import ReminderService


async def test_reminder_due_query_and_transitions(repos):
    note = await repos["notes"].create(Note(raw_text="remind me"))
    event_service = EventService(repos["events"])
    service = ReminderService(repos["reminders"], event_service)
    now = datetime.now(UTC)

    created = await service.persist_candidates(
        [
            ReminderCandidate(
                title="Past reminder",
                remind_at=(now - timedelta(minutes=1)).isoformat(),
                confidence=0.9,
            ),
            ReminderCandidate(
                title="Future reminder",
                remind_at=(now + timedelta(hours=1)).isoformat(),
                confidence=0.9,
            ),
        ],
        note.id,
    )
    for reminder in created:
        await service.schedule_reminder(reminder.id)

    due = await service.find_due_reminders(now)
    await service.mark_sent(due[0].id)

    assert [reminder.title for reminder in due] == ["Past reminder"]
