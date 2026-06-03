from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo

from memocore.adapters.storage.repositories import (
    ClarificationRequestRepository,
    ReminderRepository,
    parse_model_datetime,
)
from memocore.domain.models import ClarificationRequest, EventType
from memocore.services.event_service import EventService
from memocore.services.reminder_service import ReminderService


@dataclass(frozen=True)
class ClarificationResult:
    handled: bool
    message: str


class ClarificationService:
    def __init__(
        self,
        clarification_repo: ClarificationRequestRepository,
        reminder_repo: ReminderRepository,
        reminder_service: ReminderService,
        event_service: EventService,
        default_timezone: tzinfo = UTC,
    ):
        self.clarification_repo = clarification_repo
        self.reminder_repo = reminder_repo
        self.reminder_service = reminder_service
        self.event_service = event_service
        self.default_timezone = default_timezone

    async def request_reminder_time(
        self,
        *,
        source_chat_id: str,
        reminder_id: str,
        reminder_title: str,
        source_message_id: str | None = None,
    ) -> ClarificationRequest:
        question = f"When should I remind you about \"{reminder_title}\"?"
        request = ClarificationRequest(
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            entity_type="reminder",
            entity_id=reminder_id,
            field_name="remind_at",
            question=question,
        )
        created = await self.clarification_repo.create(request)
        await self.event_service.append_event(
            EventType.CLARIFICATION_REQUESTED,
            "clarification_request",
            created.id,
            {"entity_type": "reminder", "entity_id": reminder_id, "field_name": "remind_at"},
        )
        return created

    async def find_pending_for_chat(self, source_chat_id: str) -> ClarificationRequest | None:
        return await self.clarification_repo.find_pending_for_chat(source_chat_id)

    async def answer_pending(self, source_chat_id: str, answer_text: str) -> ClarificationResult:
        pending = await self.find_pending_for_chat(source_chat_id)
        if pending is None:
            return ClarificationResult(handled=False, message="")

        if answer_text.strip().lower() in {"cancel", "skip", "never mind", "nevermind"}:
            await self.clarification_repo.cancel(pending.id, answer_text)
            await self.event_service.append_event(
                EventType.CLARIFICATION_FAILED,
                "clarification_request",
                pending.id,
                {"reason": "user_cancelled"},
            )
            return ClarificationResult(handled=True, message="Okay, I left it unscheduled.")

        if pending.entity_type != "reminder" or pending.field_name != "remind_at":
            await self.clarification_repo.cancel(pending.id, answer_text)
            return ClarificationResult(
                handled=True,
                message="I could not apply that clarification yet, so I left the original item unchanged.",
            )

        remind_at = parse_clarification_datetime(answer_text, default_timezone=self.default_timezone)
        if remind_at is None:
            await self.event_service.append_event(
                EventType.CLARIFICATION_FAILED,
                "clarification_request",
                pending.id,
                {"reason": "unparseable_answer", "answer_text": answer_text},
            )
            return ClarificationResult(
                handled=True,
                message="I could not understand the time. Try something like 'tomorrow 9am'.",
            )

        await self.reminder_repo.update_remind_at(pending.entity_id, remind_at)
        await self.reminder_service.schedule_reminder(pending.entity_id)
        await self.clarification_repo.resolve(pending.id, answer_text)
        await self.event_service.append_event(
            EventType.CLARIFICATION_RESOLVED,
            "clarification_request",
            pending.id,
            {"entity_type": "reminder", "entity_id": pending.entity_id},
        )
        return ClarificationResult(
            handled=True,
            message=(
                "Got it. Reminder set for "
                f"{remind_at.astimezone(self.default_timezone).strftime('%Y-%m-%d %H:%M')}."
            ),
        )


def parse_clarification_datetime(
    value: str,
    now: datetime | None = None,
    default_timezone: tzinfo = UTC,
) -> datetime | None:
    parsed = parse_model_datetime(value)
    if parsed is not None:
        return parsed.astimezone(UTC)

    now = now or datetime.now(default_timezone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=default_timezone)
    lowered = value.strip().lower()
    relative = _parse_relative_duration(lowered, now)
    if relative is not None:
        return relative.astimezone(UTC)

    target_date = None
    if "tomorrow" in lowered:
        target_date = now.date() + timedelta(days=1)
    elif "today" in lowered:
        target_date = now.date()
    else:
        target_date = _next_named_weekday(lowered, now)

    if target_date is None:
        return None

    parsed_time = _parse_time(lowered)
    if parsed_time is None:
        parsed_time = time(hour=9)

    return datetime.combine(target_date, parsed_time, tzinfo=now.tzinfo).astimezone(UTC)


def _parse_relative_duration(value: str, now: datetime) -> datetime | None:
    match = re.search(
        r"\bin\s+(\d{1,3})\s*(minute|minutes|min|mins|hour|hours|hr|hrs|day|days)\b",
        value,
    )
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit in {"minute", "minutes", "min", "mins"}:
        return now + timedelta(minutes=amount)
    if unit in {"hour", "hours", "hr", "hrs"}:
        return now + timedelta(hours=amount)
    return now + timedelta(days=amount)


def _parse_time(value: str) -> time | None:
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", value)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = match.group(3)
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour=hour, minute=minute)


def _next_named_weekday(value: str, now: datetime):
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for name, weekday in weekdays.items():
        if name in value:
            days_ahead = (weekday - now.weekday()) % 7
            return now.date() + timedelta(days=days_ahead or 7)
    return None
