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

logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    commands = [
        BotCommand("today", "Việc hôm nay"),
        BotCommand("work", "Tasks, reminders, waiting"),
        BotCommand("memory", "Bộ nhớ cá nhân"),
        BotCommand("context", "People, projects, meeting prep"),
        BotCommand("briefing", "Briefing trong ngày"),
        BotCommand("capture", "Cách lưu task/memory/note"),
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
    )
    return app
