from datetime import datetime, timedelta

from memocore.adapters.storage.repositories import ReminderRepository, parse_model_datetime
from memocore.domain.models import EventType, Reminder, ReminderStatus
from memocore.domain.schemas import ReminderCandidate
from memocore.services.event_service import EventService


class ReminderService:
    def __init__(self, reminder_repo: ReminderRepository, event_service: EventService):
        self.reminder_repo = reminder_repo
        self.event_service = event_service

    async def persist_candidates(
        self, candidates: list[ReminderCandidate], source_note_id: str
    ) -> list[Reminder]:
        created: list[Reminder] = []
        for candidate in candidates:
            reminder = Reminder(
                title=candidate.title,
                remind_at=parse_model_datetime(candidate.remind_at),
                source_note_id=source_note_id,
                confidence=candidate.confidence,
            )
            created_reminder = await self.reminder_repo.create(reminder)
            await self.event_service.append_event(
                EventType.REMINDER_CANDIDATE_CREATED,
                "reminder",
                created_reminder.id,
                {"source_note_id": source_note_id},
            )
            created.append(created_reminder)
        return created

    async def schedule_reminder(self, reminder_id: str) -> None:
        await self.reminder_repo.update_status(reminder_id, ReminderStatus.SCHEDULED)
        await self.event_service.append_event(
            EventType.REMINDER_SCHEDULED, "reminder", reminder_id
        )

    async def find_due_reminders(self, now: datetime) -> list[Reminder]:
        return await self.reminder_repo.find_due(now)

    async def claim_due_reminders(self, now: datetime, lease_seconds: int = 120) -> list[Reminder]:
        return await self.reminder_repo.claim_due(now, now - timedelta(seconds=lease_seconds))

    async def mark_sent(self, reminder_id: str) -> None:
        reminder = await self.reminder_repo.get_by_id(reminder_id)
        await self.event_service.append_event(EventType.REMINDER_SENT, "reminder", reminder_id)
        if reminder and reminder.recurrence_rule and reminder.remind_at:
            next_remind_at = next_recurrence(reminder.remind_at, reminder.recurrence_rule)
            if next_remind_at is not None:
                await self.reminder_repo.update_schedule(reminder_id, next_remind_at)
                await self.event_service.append_event(
                    EventType.REMINDER_SCHEDULED,
                    "reminder",
                    reminder_id,
                    {"recurrence_rule": reminder.recurrence_rule},
                )
                return
        await self.reminder_repo.update_status(reminder_id, ReminderStatus.SENT)

    async def mark_failed(self, reminder_id: str, reason: str) -> None:
        await self.reminder_repo.update_status(reminder_id, ReminderStatus.FAILED)
        await self.event_service.append_event(
            EventType.REMINDER_FAILED, "reminder", reminder_id, {"reason": reason}
        )


def next_recurrence(current: datetime, recurrence_rule: str) -> datetime | None:
    if recurrence_rule == "daily":
        return current + timedelta(days=1)
    if recurrence_rule.startswith("weekly"):
        return current + timedelta(days=7)
    return None
