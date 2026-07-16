import logging

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder

from memocore.adapters.telegram.handlers import register_handlers
from memocore.services.capture_service import CaptureService
from memocore.services.clarification_service import ClarificationService
from memocore.services.conversation_service import ConversationService
from memocore.services.memory_view_service import MemoryViewService
from memocore.services.secretary_service import SecretaryService
from memocore.services.work_action_service import WorkActionService
from memocore.services.entity_confirmation_service import EntityConfirmationService
from memocore.services.review_service import ReviewService
from memocore.services.daily_closeout_service import DailyCloseoutService
from memocore.services.timeline_query_service import TimelineQueryService
from memocore.services.event_service import EventService

logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    commands = [
        BotCommand("today", "Việc hôm nay"),
        BotCommand("work", "Công việc và open loops"),
        BotCommand("context", "Người, dự án, meeting prep"),
        BotCommand("search", "Tìm timeline/source"),
        BotCommand("review", "Các mục cần anh xem lại"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Telegram slash command menu synced with %s commands", len(commands))


def create_bot(
    token: str,
    owner_id: int,
    capture_service: CaptureService,
    secretary_service: SecretaryService,
    clarification_service: ClarificationService | None = None,
    conversation_service: ConversationService | None = None,
    memory_view_service: MemoryViewService | None = None,
    work_action_service: WorkActionService | None = None,
    entity_confirmation_service: EntityConfirmationService | None = None,
    review_service: ReviewService | None = None,
    daily_closeout_service: DailyCloseoutService | None = None,
    timeline_query_service: TimelineQueryService | None = None,
    event_service: EventService | None = None,
) -> Application:
    app = ApplicationBuilder().token(token).post_init(post_init).build()
    register_handlers(
        app,
        owner_id,
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
        event_service,
    )
    return app
