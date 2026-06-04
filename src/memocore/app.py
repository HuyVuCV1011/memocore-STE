from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from telegram import Bot
from telegram.ext import Application

from memocore.adapters.llm.provider_factory import create_provider_with_fallback
from memocore.adapters.storage.repositories import (
    ClarificationRequestRepository,
    EventLogRepository,
    FollowUpRepository,
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
    PersonRepository(database)
    followup_repo = FollowUpRepository(database)
    memory_repo = MemoryItemRepository(database)
    event_repo = EventLogRepository(database)

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


async def shutdown_app(app: Application) -> None:
    reminder_task = app.bot_data.get("reminder_task")
    if reminder_task:
        reminder_task.cancel()
        try:
            await reminder_task
        except asyncio.CancelledError:
            pass
    database = app.bot_data.get("database")
    if database:
        await database.close()
