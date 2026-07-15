from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import Bot
from telegram.ext import Application

from memocore.adapters.llm.provider_factory import create_provider_with_fallback
from memocore.adapters.storage.repositories import (
    ActivityLinkRepository,
    ClarificationRequestRepository,
    ChatContextRepository,
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
    TaskListContextRepository,
)
from memocore.adapters.storage.knowledge_repositories import (
    DecisionRepository,
    KnowledgeRelationRepository,
    OrganizationRepository,
)
from memocore.adapters.storage.sqlite import Database
from memocore.adapters.telegram.bot import create_bot
from memocore.config import Settings, get_settings
from memocore.domain.models import EventType
from memocore.services.capture_service import CaptureService
from memocore.services.clarification_service import ClarificationService
from memocore.services.conversation_service import ConversationService
from memocore.services.daily_closeout_service import DailyCloseoutService
from memocore.services.event_service import EventService
from memocore.services.memory_service import MemoryService
from memocore.services.memory_view_service import MemoryViewService
from memocore.services.reminder_service import ReminderService
from memocore.services.secretary_service import SecretaryService
from memocore.services.task_extraction_service import ExtractionService
from memocore.services.intent_classifier_service import IntentClassifierService
from memocore.services.knowledge_query_service import KnowledgeQueryService
from memocore.services.work_action_service import WorkActionService
from memocore.services.entity_confirmation_service import EntityConfirmationService
from memocore.services.reference_resolver import ReferenceResolver
from memocore.services.task_operation_service import TaskOperationService
from memocore.services.timeline_query_service import TimelineQueryService
from memocore.services.activity_reconciliation_service import (
    ActivityReconciliationService,
)
from memocore.services.backup_service import BackupService
from memocore.services.review_service import ReviewService

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
    task_list_context_repo = TaskListContextRepository(database)
    chat_context_repo = ChatContextRepository(database)
    reminder_repo = ReminderRepository(database)
    clarification_repo = ClarificationRequestRepository(database)
    project_repo = ProjectRepository(database)
    person_repo = PersonRepository(database)
    meeting_repo = MeetingRepository(database)
    activity_link_repo = ActivityLinkRepository(database)
    followup_repo = FollowUpRepository(database)
    memory_repo = MemoryItemRepository(database)
    event_repo = EventLogRepository(database)
    commitment_repo = CommitmentRepository(database)
    organization_repo = OrganizationRepository(database)
    decision_repo = DecisionRepository(database)
    knowledge_relation_repo = KnowledgeRelationRepository(database)

    event_service = EventService(event_repo)
    activity_reconciliation_service = ActivityReconciliationService(
        task_repo,
        meeting_repo,
        person_repo,
        project_repo,
        activity_link_repo,
        event_service,
    )
    repaired_activities = await activity_reconciliation_service.repair_legacy_renames()
    if repaired_activities:
        logger.info("Reconciled %s legacy renamed activities", repaired_activities)
    task_operation_service = TaskOperationService(
        task_repo,
        event_service,
        activity_reconciliation_service,
    )
    provider = create_provider_with_fallback(settings.model, settings.fallback)
    extraction_service = ExtractionService(provider, temperature=settings.model.temperature)
    intent_classifier_service = IntentClassifierService(provider, temperature=settings.model.temperature)
    knowledge_query_service = KnowledgeQueryService(
        provider,
        memory_repo,
        project_repo,
        person_repo,
        task_repo,
        followup_repo,
        commitment_repo,
        meeting_repo,
        reminder_repo,
        organization_repo,
        decision_repo,
        knowledge_relation_repo,
    )
    memory_service = MemoryService(memory_repo, event_service)
    memory_view_service = MemoryViewService(
        memory_repo, project_repo, person_repo, event_service, note_repo
    )
    reminder_service = ReminderService(reminder_repo, event_service)
    clarification_service = ClarificationService(
        clarification_repo,
        reminder_repo,
        reminder_service,
        event_service,
        default_timezone=ZoneInfo(settings.user_timezone),
        task_repo=task_repo,
        followup_repo=followup_repo,
        commitment_repo=commitment_repo,
        task_operation_service=task_operation_service,
        default_reminder_time=_parse_clock(settings.reminder_default_time),
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
        person_repo,
        meeting_repo,
        followup_repo,
        commitment_repo,
        organization_repo,
        decision_repo,
        knowledge_relation_repo,
        activity_reconciliation_service,
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
        note_repo=note_repo,
        event_service=event_service,
        activity_link_repo=activity_link_repo,
    )
    work_action_service = WorkActionService(
        task_repo,
        reminder_repo,
        event_service,
        display_timezone=ZoneInfo(settings.user_timezone),
        task_operation_service=task_operation_service,
    )
    entity_confirmation_service = EntityConfirmationService(
        person_repo,
        project_repo,
        event_service,
        note_repo,
    )
    review_service = ReviewService(
        memory_repo,
        task_repo,
        clarification_repo,
        event_service,
        project_repo,
    )
    daily_closeout_service = DailyCloseoutService(
        task_repo,
        clarification_repo,
        event_service,
        followup_repo=followup_repo,
        commitment_repo=commitment_repo,
        display_timezone=ZoneInfo(settings.user_timezone),
    )
    timeline_query_service = TimelineQueryService(
        note_repo,
        task_repo,
        reminder_repo,
        project_repo,
        person_repo,
        meeting_repo,
        followup_repo,
        commitment_repo,
        memory_repo,
        event_repo,
        decision_repo,
        display_timezone=ZoneInfo(settings.user_timezone),
    )
    reference_resolver = ReferenceResolver(
        chat_context_repo,
        project_repo,
        person_repo,
        task_repo,
        organization_repo,
    )
    conversation_service = ConversationService(
        capture_service,
        secretary_service,
        note_repo,
        task_repo,
        memory_service,
        event_service,
        intent_classifier_service=intent_classifier_service,
        knowledge_query_service=knowledge_query_service,
        task_list_context_repo=task_list_context_repo,
        reference_resolver=reference_resolver,
        task_operation_service=task_operation_service,
        timeline_query_service=timeline_query_service,
    )

    app = create_bot(
        settings.telegram_bot_token,
        settings.telegram_owner_id,
        capture_service,
        secretary_service,
        clarification_service,
        conversation_service,
        memory_view_service,
        work_action_service,
        entity_confirmation_service,
        review_service,
        daily_closeout_service,
        timeline_query_service,
    )
    app.bot_data["database"] = database
    app.bot_data["reminder_task"] = asyncio.create_task(
        reminder_dispatch_loop(reminder_service, app.bot, settings.telegram_owner_id)
    )
    app.bot_data["morning_briefing_task"] = asyncio.create_task(
        scheduled_morning_briefing_loop(secretary_service, event_service, app.bot, settings)
    )
    app.bot_data["nudge_task"] = asyncio.create_task(
        proactive_nudge_loop(secretary_service, event_service, app.bot, settings)
    )
    app.bot_data["weekly_review_task"] = asyncio.create_task(
        scheduled_weekly_review_loop(secretary_service, event_service, app.bot, settings)
    )
    app.bot_data["backup_task"] = asyncio.create_task(
        scheduled_backup_loop(event_service, settings)
    )
    return app


async def reminder_dispatch_loop(
    reminder_service: ReminderService,
    bot: Bot,
    owner_id: int,
    interval: int = 30,
) -> None:
    while True:
        await send_due_reminders(reminder_service, bot, owner_id)
        await asyncio.sleep(interval)


async def send_due_reminders(
    reminder_service: ReminderService,
    bot: Bot,
    owner_id: int,
    now: datetime | None = None,
) -> int:
    sent = 0
    for reminder in await reminder_service.claim_due_reminders(now or datetime.now(UTC)):
        try:
            await bot.send_message(
                chat_id=owner_id,
                text=f"Reminder: {reminder.title}",
            )
            await reminder_service.mark_sent(reminder.id)
            sent += 1
        except Exception:
            logger.exception("Failed to send reminder %s", reminder.id)
            await reminder_service.mark_failed(reminder.id, "telegram_send_error")
    return sent


async def scheduled_morning_briefing_loop(
    secretary_service: SecretaryService,
    event_service: EventService,
    bot: Bot,
    settings: Settings,
    interval: int = 60,
) -> None:
    while True:
        await send_due_morning_briefings(secretary_service, event_service, bot, settings)
        await asyncio.sleep(interval)


async def proactive_nudge_loop(
    secretary_service: SecretaryService,
    event_service: EventService,
    bot: Bot,
    settings: Settings,
) -> None:
    while True:
        await send_due_nudges(secretary_service, event_service, bot, settings)
        await asyncio.sleep(max(60, settings.proactive_nudge_interval_minutes * 60))


async def scheduled_weekly_review_loop(
    secretary_service: SecretaryService,
    event_service: EventService,
    bot: Bot,
    settings: Settings,
    interval: int = 60,
) -> None:
    while True:
        await send_due_weekly_reviews(secretary_service, event_service, bot, settings)
        await asyncio.sleep(interval)


async def scheduled_backup_loop(
    event_service: EventService,
    settings: Settings,
    interval: int = 60,
) -> None:
    while True:
        await send_due_backups(event_service, settings)
        await asyncio.sleep(interval)


async def send_due_morning_briefings(
    secretary_service: SecretaryService,
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
    chat_id = str(settings.telegram_owner_id)
    if await event_service.exists_recent(EventType.BRIEFING_SENT, "telegram_chat", chat_id, day_start):
        return 0
    try:
        await bot.send_message(
            chat_id=settings.telegram_owner_id,
            text=await secretary_service.daily_briefing(now),
        )
    except Exception:
        logger.exception("Failed to send morning briefing to owner chat %s", chat_id)
        return 0
    await event_service.append_event(
        EventType.BRIEFING_SENT,
        "telegram_chat",
        chat_id,
        {"date": local_now.date().isoformat()},
        created_at=now,
    )
    return 1


async def send_due_weekly_reviews(
    secretary_service: SecretaryService,
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
    chat_id = str(settings.telegram_owner_id)
    if await event_service.exists_recent(
        EventType.WEEKLY_REVIEW_SENT, "telegram_chat", chat_id, day_start
    ):
        return 0
    try:
        await bot.send_message(
            chat_id=settings.telegram_owner_id,
            text=await secretary_service.weekly_review(now),
        )
    except Exception:
        logger.exception("Failed to send weekly review to owner chat %s", chat_id)
        return 0
    await event_service.append_event(
        EventType.WEEKLY_REVIEW_SENT,
        "telegram_chat",
        chat_id,
        {"date": local_now.date().isoformat()},
        created_at=now,
    )
    return 1


async def send_due_nudges(
    secretary_service: SecretaryService,
    event_service: EventService,
    bot: Bot,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    if not settings.proactive_nudges_enabled:
        return 0
    now = now or datetime.now(UTC)
    timezone = ZoneInfo(settings.user_timezone)
    local_time = now.astimezone(timezone).time()
    if _is_in_quiet_hours(local_time, settings.quiet_hours_start, settings.quiet_hours_end):
        return 0
    cooldown_since = now - timedelta(hours=settings.proactive_nudge_cooldown_hours)
    candidates = []
    followup_window_open = _is_in_optional_window(
        local_time,
        settings.followup_nudge_window_start,
        settings.followup_nudge_window_end,
    )
    for entity_type, entity_id, text_body in await secretary_service.deadline_nudges(
        now,
        stale_followup_days=settings.stale_followup_days,
        predeadline_warning_hours=settings.proactive_deadline_warning_hours,
    ):
        if entity_type == "followup" and not followup_window_open:
            continue
        if await event_service.exists_recent(EventType.NUDGE_SENT, entity_type, entity_id, cooldown_since):
            continue
        candidates.append((entity_type, entity_id, text_body))
    if settings.proactive_nudge_max_per_run > 0:
        candidates = candidates[: settings.proactive_nudge_max_per_run]
    if not candidates:
        return 0

    sent = 0
    if len(candidates) >= settings.proactive_nudge_bundle_threshold > 1:
        text_body = "Nudge digest\n" + "\n\n".join(
            f"{index}. {body}" for index, (_, _, body) in enumerate(candidates, 1)
        )
        try:
            await bot.send_message(chat_id=settings.telegram_owner_id, text=text_body)
        except Exception:
            logger.exception(
                "Failed to send nudge digest with %s item(s) to owner chat %s",
                len(candidates),
                settings.telegram_owner_id,
            )
            return 0
        for entity_type, entity_id, _ in candidates:
            await event_service.append_event(EventType.NUDGE_SENT, entity_type, entity_id, created_at=now)
        return 1

    for entity_type, entity_id, text_body in candidates:
        try:
            await bot.send_message(chat_id=settings.telegram_owner_id, text=text_body)
        except Exception:
            logger.exception(
                "Failed to send nudge %s:%s to owner chat %s",
                entity_type,
                entity_id,
                settings.telegram_owner_id,
            )
            continue
        sent += 1
        await event_service.append_event(EventType.NUDGE_SENT, entity_type, entity_id, created_at=now)
    return sent


async def send_due_backups(
    event_service: EventService,
    settings: Settings,
    now: datetime | None = None,
) -> int:
    if not settings.backup_enabled:
        return 0
    now = now or datetime.now(UTC)
    timezone = ZoneInfo(settings.user_timezone)
    local_now = now.astimezone(timezone)
    if local_now.time() < _parse_clock(settings.backup_time):
        return 0
    day_start = datetime.combine(local_now.date(), time.min, tzinfo=timezone).astimezone(UTC)
    entity_id = settings.database_path.name
    if await event_service.exists_recent(
        EventType.BACKUP_CREATED,
        "database",
        entity_id,
        day_start,
    ):
        return 0
    try:
        backup_service = BackupService(settings.database_path, settings.backup_dir)
        result = backup_service.create_backup(verify=True)
        removed_backups = backup_service.prune_backups(
            keep_count=settings.backup_retention_count,
            max_age_days=settings.backup_retention_days,
            now=now,
        )
    except Exception as exc:
        logger.exception("Failed to create scheduled backup")
        await event_service.append_event(
            EventType.BACKUP_FAILED,
            "database",
            entity_id,
            {"error": str(exc)[:500]},
            created_at=now,
        )
        return 0
    await event_service.append_event(
        EventType.BACKUP_CREATED,
        "database",
        entity_id,
        {
            "backup_id": result.backup_id,
            "size_bytes": result.database_path.stat().st_size,
            "verified": result.verified,
            "pruned": removed_backups,
        },
        created_at=now,
    )
    return 1


def _parse_clock(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _is_in_optional_window(current: time, start: str | None, end: str | None) -> bool:
    if not start and not end:
        return True
    if not start or not end:
        return False
    start_time = _parse_clock(start)
    end_time = _parse_clock(end)
    if start_time <= end_time:
        return start_time <= current <= end_time
    return current >= start_time or current <= end_time


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
        "backup_task",
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
