from __future__ import annotations

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from memocore.domain.schemas import CaptureRequest
from memocore.services.capture_service import CaptureService
from memocore.services.clarification_service import ClarificationService
from memocore.services.conversation_service import (
    ConversationService,
    format_capture_response,
)
from memocore.services.secretary_service import SecretaryService

logger = logging.getLogger(__name__)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await _safe_reply_text(
            update,
            "MemoCore is ready. Send a note or use /today, /tomorrow, /tasks, /reminders, /waiting, /projects, /memory, /briefing, /weekly, /people, /commitments, /person, /project, /context, or /prep."
        )


async def secretary_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    command = update.message.text.split()[0].removeprefix("/").split("@")[0] if update.message.text else ""
    if not command:
        return
    service: SecretaryService = context.application.bot_data["secretary_service"]
    if command in {"person", "context", "prep", "project"}:
        query = update.message.text.partition(" ")[2].strip() if update.message.text else ""
        if not query:
            prompts = {
                "person": "Bạn muốn xem person nào? Dùng /person <tên>.",
                "project": "Bạn muốn xem project nào? Dùng /project <tên>.",
                "context": "Bạn muốn xem context nào? Dùng /context <person hoặc project>.",
                "prep": "Bạn muốn chuẩn bị meeting nào? Dùng /prep <person hoặc project>.",
            }
            await _safe_reply_text(update, prompts[command])
            return
        if command == "person":
            await _safe_reply_text(update, await service.person_context(query))
            return
        if command == "project":
            await _safe_reply_text(update, await service.project_context(query))
            return
        if command == "context":
            await _safe_reply_text(update, await service.context(query))
            return
        await _safe_reply_text(update, await service.meeting_prep(query))
        return
    actions = {
        "today": service.today,
        "todays": service.today,
        "tomorrow": service.tomorrow,
        "tasks": service.tasks,
        "reminders": service.reminders,
        "waiting": service.waiting,
        "projects": service.projects,
        "memory": service.memories,
        "briefing": service.daily_briefing,
        "weekly": service.weekly_review,
        "people": service.people,
        "commitments": service.commitments,
    }
    action = actions.get(command)
    if action is None:
        return
    await _safe_reply_text(update, await action())


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    source_chat_id = str(update.effective_chat.id) if update.effective_chat else None
    clarification_service: ClarificationService | None = context.application.bot_data.get(
        "clarification_service"
    )
    if source_chat_id and clarification_service:
        result = await clarification_service.answer_pending(source_chat_id, update.message.text)
        if result.handled:
            await _safe_reply_text(update, result.message)
            return

    conversation_service: ConversationService | None = context.application.bot_data.get(
        "conversation_service"
    )
    capture_service: CaptureService = context.application.bot_data["capture_service"]
    request = CaptureRequest(
        source="telegram",
        source_message_id=str(update.message.message_id),
        source_chat_id=source_chat_id,
        raw_text=update.message.text,
    )
    if conversation_service is not None:
        response = await conversation_service.handle_text(request)
        await _safe_reply_text(update, response.reply)
        return
    response = await capture_service.capture(request)
    await _safe_reply_text(update, format_capture_response(response))


async def _safe_reply_text(update: Update, text: str) -> None:
    if not update.message:
        return
    try:
        await update.message.reply_text(text)
    except TelegramError:
        logger.warning(
            "Failed to send Telegram reply",
            exc_info=True,
            extra={
                "update_id": update.update_id,
                "chat_id": update.effective_chat.id if update.effective_chat else None,
                "message_id": update.message.message_id,
            },
        )


def register_handlers(
    app: Application,
    capture_service: CaptureService,
    secretary_service: SecretaryService,
    clarification_service: ClarificationService | None = None,
    conversation_service: ConversationService | None = None,
) -> None:
    app.bot_data["capture_service"] = capture_service
    app.bot_data["secretary_service"] = secretary_service
    app.bot_data["clarification_service"] = clarification_service
    app.bot_data["conversation_service"] = conversation_service
    app.add_handler(CommandHandler("start", start_handler))
    for command in (
        "today",
        "todays",
        "tomorrow",
        "tasks",
        "reminders",
        "waiting",
        "projects",
        "memory",
        "briefing",
        "weekly",
        "people",
        "person",
        "project",
        "commitments",
        "context",
        "prep",
    ):
        app.add_handler(CommandHandler(command, secretary_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
