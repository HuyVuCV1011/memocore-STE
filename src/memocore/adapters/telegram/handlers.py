from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from memocore.domain.schemas import CaptureRequest, CaptureResponse
from memocore.services.capture_service import CaptureService
from memocore.services.secretary_service import SecretaryService


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "Memocore is ready. Send a note or use /today, /waiting, /projects, or /memory."
        )


async def secretary_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    service: SecretaryService = context.application.bot_data["secretary_service"]
    command = update.message.text.split()[0].removeprefix("/").split("@")[0] if update.message.text else ""
    actions = {
        "today": service.today,
        "waiting": service.waiting,
        "projects": service.projects,
        "memory": service.memories,
    }
    await update.message.reply_text(await actions[command]())


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    capture_service: CaptureService = context.application.bot_data["capture_service"]
    request = CaptureRequest(
        source="telegram",
        source_message_id=str(update.message.message_id),
        source_chat_id=str(update.effective_chat.id) if update.effective_chat else None,
        raw_text=update.message.text,
    )
    response = await capture_service.capture(request)
    await update.message.reply_text(format_capture_response(response))


def format_capture_response(response: CaptureResponse) -> str:
    text = (
        f"Captured: {response.summary}\n"
        f"{response.tasks_created} task(s) | "
        f"{response.reminders_created} reminder(s) | "
        f"{response.memories_created} memory item(s)"
    )
    if response.errors:
        text += "\nExtraction had issues, raw note saved."
    return text


def register_handlers(
    app: Application, capture_service: CaptureService, secretary_service: SecretaryService
) -> None:
    app.bot_data["capture_service"] = capture_service
    app.bot_data["secretary_service"] = secretary_service
    app.add_handler(CommandHandler("start", start_handler))
    for command in ("today", "waiting", "projects", "memory"):
        app.add_handler(CommandHandler(command, secretary_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
