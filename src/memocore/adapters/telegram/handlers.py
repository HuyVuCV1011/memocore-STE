from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from memocore.domain.schemas import CaptureRequest
from memocore.services.capture_service import CaptureService
from memocore.services.clarification_service import ClarificationService
from memocore.services.conversation_service import (
    ConversationService,
    format_capture_response,
)
from memocore.services.secretary_service import SecretaryService


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "MemoCore is ready. Send a note or use /today, /tomorrow, /tasks, /reminders, /waiting, /projects, or /memory."
        )


async def secretary_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    command = update.message.text.split()[0].removeprefix("/").split("@")[0] if update.message.text else ""
    if not command:
        return
    service: SecretaryService = context.application.bot_data["secretary_service"]
    actions = {
        "today": service.today,
        "todays": service.today,
        "tomorrow": service.tomorrow,
        "tasks": service.tasks,
        "reminders": service.reminders,
        "waiting": service.waiting,
        "projects": service.projects,
        "memory": service.memories,
    }
    action = actions.get(command)
    if action is None:
        return
    await update.message.reply_text(await action())


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
            await update.message.reply_text(result.message)
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
        await update.message.reply_text(response.reply)
        return
    response = await capture_service.capture(request)
    await update.message.reply_text(format_capture_response(response))


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
    for command in ("today", "todays", "tomorrow", "tasks", "reminders", "waiting", "projects", "memory"):
        app.add_handler(CommandHandler(command, secretary_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
