from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import Bot
from telegram.ext import Application

from memocore.adapters.llm.provider_factory import create_provider_with_fallback
from memocore.adapters.storage.repositories import (
    ClarificationRequestRepository,
    CommitmentRepository,
    EventLogRepository,
    FollowUpRepository,
    MeetingRepository,
    MemoryItemRepository,
    NoteRepository,
    PersonRepository,
    ProjectRepository,
    ReminderRepository,
    TaskRepository,
)
from memocore.adapters.storage.sqlite import Database
from memocore.adapters.telegram.bot import create_bot
from memocore.config import Settings, get_settings
from memocore.domain.models import EventType
from memocore.services.capture_service import CaptureService
from memocore.services.clarification_service import ClarificationService
from memocore.services.conversation_service import ConversationService
from memocore.services.event_service import EventService
from memocore.services.memory_service import MemoryService
from memocore.services.reminder_service import ReminderService
from memocore.services.secretary_service import SecretaryService
from memocore.services.task_extraction_service import ExtractionService
from memocore.services.intent_classifier_service import IntentClassifierService

logger = logging.getLogger(__name__)


async def create_app(settings: Settings | None = None) -> Application:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    database = Database(settings.database_path)
    await database.initialize()

    note_repo = NoteRepository(database)
    task_repo = TaskRepository(database)
    reminder_repo = ReminderRepository(database)
    clarification_repo = ClarificationRequestRepository(database)
    project_repo = ProjectRepository(database)
    person_repo = PersonRepository(database)
    meeting_repo = MeetingRepository(database)
    followup_repo = FollowUpRepository(database)
    memory_repo = MemoryItemRepository(database)
    event_repo = EventLogRepository(database)
    commitment_repo = CommitmentRepository(database)

    event_service = EventService(event_repo)
    provider = create_provider_with_fallback(settings.model, settings.fallback)
    extraction_service = ExtractionService(provider, temperature=settings.model.temperature)
    intent_classifier_service = IntentClassifierService(provider, temperature=settings.model.temperature)
    memory_service = MemoryService(memory_repo, event_service)
    reminder_service = ReminderService(reminder_repo, event_service)
    clarification_service = ClarificationService(
        clarification_repo,
        reminder_repo,
        reminder_service,
        event_service,
        default_timezone=ZoneInfo(settings.user_timezone),
        task_repo=task_repo,
    )
    capture_service = CaptureService(
        note_repo,
        task_repo,
        project_repo,
        extraction_service,
        memory_service,
        reminder_service,
        event_service,
        clarification_service,
    )
    secretary_service = SecretaryService(
        task_repo,
        reminder_repo,
        followup_repo,
        project_repo,
        memory_repo,
        display_timezone=ZoneInfo(settings.user_timezone),
        meeting_repo=meeting_repo,
        person_repo=person_repo,
        commitment_repo=commitment_repo,
    )
    conversation_service = ConversationService(
        capture_service,
        secretary_service,
        note_repo,
        task_repo,
        memory_service,
        event_service,
        intent_classifier_service=intent_classifier_service,
    )

    app = create_bot(
        settings.telegram_bot_token,
        capture_service,
        secretary_service,
        clarification_service,
        conversation_service,
    )
    app.bot_data["database"] = database
    app.bot_data["reminder_task"] = asyncio.create_task(
        reminder_dispatch_loop(reminder_service, note_repo, app.bot)
    )
    app.bot_data["morning_briefing_task"] = asyncio.create_task(
        scheduled_morning_briefing_loop(secretary_service, note_repo, event_service, app.bot, settings)
    )
    app.bot_data["nudge_task"] = asyncio.create_task(
        proactive_nudge_loop(secretary_service, note_repo, event_service, app.bot, settings)
    )
    app.bot_data["weekly_review_task"] = asyncio.create_task(
        scheduled_weekly_review_loop(secretary_service, note_repo, event_service, app.bot, settings)
    )
    return app


async def reminder_dispatch_loop(
    reminder_service: ReminderService,
    note_repo: NoteRepository,
    bot: Bot,
    interval: int = 30,
) -> None:
    while True:
        due = await reminder_service.claim_due_reminders(datetime.now(UTC))
        for reminder in due:
            note = await note_repo.get_by_id(reminder.source_note_id)
            if not note or not note.source_chat_id:
                await reminder_service.mark_failed(reminder.id, "missing_chat_id")
                continue
            try:
                await bot.send_message(
                    chat_id=int(note.source_chat_id),
                    text=f"Reminder: {reminder.title}",
                )
                await reminder_service.mark_sent(reminder.id)
            except Exception:
                logger.exception("Failed to send reminder %s", reminder.id)
                await reminder_service.mark_failed(reminder.id, "telegram_send_error")
        await asyncio.sleep(interval)


async def scheduled_morning_briefing_loop(
    secretary_service: SecretaryService,
    note_repo: NoteRepository,
    event_service: EventService,
    bot: Bot,
    settings: Settings,
    interval: int = 60,
) -> None:
    while True:
        await send_due_morning_briefings(secretary_service, note_repo, event_service, bot, settings)
        await asyncio.sleep(interval)


async def proactive_nudge_loop(
    secretary_service: SecretaryService,
    note_repo: NoteRepository,
    event_service: EventService,
    bot: Bot,
    settings: Settings,
) -> None:
    while True:
        await send_due_nudges(secretary_service, note_repo, event_service, bot, settings)
        await asyncio.sleep(max(60, settings.proactive_nudge_interval_minutes * 60))


async def scheduled_weekly_review_loop(
    secretary_service: SecretaryService,
    note_repo: NoteRepository,
    event_service: EventService,
    bot: Bot,
    settings: Settings,
    interval: int = 60,
) -> None:
    while True:
        await send_due_weekly_reviews(secretary_service, note_repo, event_service, bot, settings)
        await asyncio.sleep(interval)


async def send_due_morning_briefings(
    secretary_service: SecretaryService,
    note_repo: NoteRepository,
    event_service: EventService,
    bot: Bot,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    if not settings.morning_briefing_enabled:
        return 0
    now = now or datetime.now(UTC)
    timezone = ZoneInfo(settings.user_timezone)
    local_now = now.astimezone(timezone)
    if local_now.time() < _parse_clock(settings.morning_briefing_time):
        return 0
    day_start = datetime.combine(local_now.date(), time.min, tzinfo=timezone).astimezone(UTC)
    sent = 0
    for chat_id in await note_repo.list_source_chat_ids():
        if await event_service.exists_recent(EventType.BRIEFING_SENT, "telegram_chat", chat_id, day_start):
            continue
        try:
            await bot.send_message(chat_id=int(chat_id), text=await secretary_service.daily_briefing(now))
        except Exception:
            logger.exception("Failed to send morning briefing to chat %s", chat_id)
            continue
        await event_service.append_event(
            EventType.BRIEFING_SENT,
            "telegram_chat",
            chat_id,
            {"date": local_now.date().isoformat()},
            created_at=now,
        )
        sent += 1
    return sent


async def send_due_weekly_reviews(
    secretary_service: SecretaryService,
    note_repo: NoteRepository,
    event_service: EventService,
    bot: Bot,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    if not settings.weekly_review_enabled:
        return 0
    now = now or datetime.now(UTC)
    timezone = ZoneInfo(settings.user_timezone)
    local_now = now.astimezone(timezone)
    if local_now.weekday() != settings.weekly_review_weekday:
        return 0
    if local_now.time() < _parse_clock(settings.weekly_review_time):
        return 0
    day_start = datetime.combine(local_now.date(), time.min, tzinfo=timezone).astimezone(UTC)
    sent = 0
    for chat_id in await note_repo.list_source_chat_ids():
        if await event_service.exists_recent(EventType.WEEKLY_REVIEW_SENT, "telegram_chat", chat_id, day_start):
            continue
        try:
            await bot.send_message(chat_id=int(chat_id), text=await secretary_service.weekly_review(now))
        except Exception:
            logger.exception("Failed to send weekly review to chat %s", chat_id)
            continue
        await event_service.append_event(
            EventType.WEEKLY_REVIEW_SENT,
            "telegram_chat",
            chat_id,
            {"date": local_now.date().isoformat()},
            created_at=now,
        )
        sent += 1
    return sent


async def send_due_nudges(
    secretary_service: SecretaryService,
    note_repo: NoteRepository,
    event_service: EventService,
    bot: Bot,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    if not settings.proactive_nudges_enabled:
        return 0
    now = now or datetime.now(UTC)
    timezone = ZoneInfo(settings.user_timezone)
    if _is_in_quiet_hours(now.astimezone(timezone).time(), settings.quiet_hours_start, settings.quiet_hours_end):
        return 0
    chat_ids = await note_repo.list_source_chat_ids()
    if not chat_ids:
        return 0
    cooldown_since = now - timedelta(hours=settings.proactive_nudge_cooldown_hours)
    sent = 0
    for entity_type, entity_id, text_body in await secretary_service.deadline_nudges(
        now, stale_followup_days=settings.stale_followup_days
    ):
        if await event_service.exists_recent(EventType.NUDGE_SENT, entity_type, entity_id, cooldown_since):
            continue
        delivered = False
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id=int(chat_id), text=text_body)
                delivered = True
                sent += 1
            except Exception:
                logger.exception("Failed to send nudge %s:%s to chat %s", entity_type, entity_id, chat_id)
        if delivered:
            await event_service.append_event(EventType.NUDGE_SENT, entity_type, entity_id, created_at=now)
    return sent


def _parse_clock(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _is_in_quiet_hours(current: time, start: str | None, end: str | None) -> bool:
    if not start or not end:
        return False
    start_time = _parse_clock(start)
    end_time = _parse_clock(end)
    if start_time <= end_time:
        return start_time <= current < end_time
    return current >= start_time or current < end_time


async def shutdown_app(app: Application) -> None:
    for task_name in (
        "reminder_task",
        "morning_briefing_task",
        "nudge_task",
        "weekly_review_task",
    ):
        task = app.bot_data.get(task_name)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    database = app.bot_data.get("database")
    if database:
        await database.close()
