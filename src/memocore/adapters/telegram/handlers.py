from __future__ import annotations

import logging
from datetime import UTC, datetime

from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from memocore.adapters.telegram.presenter import present_response
from memocore.domain.schemas import AssistantAction, AssistantResponse, AssistantSection, CaptureRequest
from memocore.domain.models import NoteStatus
from memocore.services.capture_service import CaptureService
from memocore.services.clarification_service import ClarificationService
from memocore.services.conversation_service import (
    ConversationService,
    format_capture_response,
)
from memocore.services.memory_view_service import MemoryViewService
from memocore.services.secretary_service import SecretaryService
from memocore.services.work_action_service import WorkActionService
from memocore.services.entity_confirmation_service import EntityConfirmationService

logger = logging.getLogger(__name__)


async def owner_only_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    owner_id = context.application.bot_data["telegram_owner_id"]
    user = update.effective_user
    chat = update.effective_chat
    if (
        user is None
        or chat is None
        or user.id != owner_id
        or chat.type != ChatType.PRIVATE
        or chat.id != owner_id
    ):
        logger.warning(
            "Rejected unauthorized Telegram update user_id=%s chat_id=%s chat_type=%s",
            user.id if user else None,
            chat.id if chat else None,
            chat.type if chat else None,
        )
        raise ApplicationHandlerStop


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        keyboard = ReplyKeyboardMarkup(
            [
                [KeyboardButton("#linkedin"), KeyboardButton("#task"), KeyboardButton("#remind")],
                [KeyboardButton("#mem"), KeyboardButton("#ste"), KeyboardButton("#mindx")],
            ],
            resize_keyboard=True,
            is_persistent=True,
        )
        await _safe_reply_text(
            update,
            "MemoCore đã sẵn sàng.\n\n"
            "Gõ / để mở 6 cửa chính: /today, /work, /memory, /context, /briefing, /capture.\n"
            "Anh vẫn có thể nhắn tự nhiên hoặc dùng shortcut ẩn như /task, /prep <tên>, /person <tên>.",
            reply_markup=keyboard,
        )


async def secretary_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    command = update.message.text.split()[0].removeprefix("/").split("@")[0] if update.message.text else ""
    if not command:
        return
    service: SecretaryService = context.application.bot_data["secretary_service"]
    conversation_service: ConversationService | None = context.application.bot_data.get(
        "conversation_service"
    )
    source_chat_id = str(update.effective_chat.id) if update.effective_chat else None
    clarification_service: ClarificationService | None = context.application.bot_data.get(
        "clarification_service"
    )
    if source_chat_id and clarification_service:
        await clarification_service.cancel_pending_for_chat(
            source_chat_id, update.message.text or command
        )
    work_action_service: WorkActionService | None = context.application.bot_data.get(
        "work_action_service"
    )
    if command == "tasks" and work_action_service is not None:
        text, keyboard = present_response(await work_action_service.tasks_view())
        if conversation_service is not None:
            await conversation_service.remember_task_list(source_chat_id, text, command)
        await _safe_reply_text(update, text, reply_markup=keyboard)
        return
    if command == "reminders" and work_action_service is not None:
        text, keyboard = present_response(await work_action_service.reminders_view())
        await _safe_reply_text(update, text, reply_markup=keyboard)
        return
    if command == "work":
        text, keyboard = present_response(_work_hub_response(await service.work_dashboard()))
        await _safe_reply_text(update, text, reply_markup=keyboard)
        return
    if command == "capture":
        text, keyboard = present_response(_capture_hub_response())
        await _safe_reply_text(update, text, reply_markup=keyboard)
        return
    if command == "help":
        text, keyboard = present_response(_help_response())
        await _safe_reply_text(update, text, reply_markup=keyboard)
        return
    if command == "memory":
        arg = update.message.text.partition(" ")[2].strip() if update.message.text else ""
        memory_view_service: MemoryViewService | None = context.application.bot_data.get(
            "memory_view_service"
        )
        if memory_view_service is not None:
            if arg == "stale":
                response = await memory_view_service.stale()
            elif arg == "review":
                response = await memory_view_service.topic("review", 0)
            elif arg.startswith("by "):
                response = await memory_view_service.topic(arg.removeprefix("by ").strip(), 0)
            elif arg:
                response = await memory_view_service.topic(arg, 0)
            else:
                response = await memory_view_service.overview()
            if response is None:
                response = await memory_view_service.overview()
            text, keyboard = present_response(response)
            await _safe_reply_text(update, text, reply_markup=keyboard)
            return
    if command in {"people", "projects"}:
        arg = update.message.text.partition(" ")[2].strip() if update.message.text else ""
        entity_confirmation_service: EntityConfirmationService | None = context.application.bot_data.get(
            "entity_confirmation_service"
        )
        if arg == "review" and entity_confirmation_service is not None:
            entity_type = "person" if command == "people" else "project"
            text, keyboard = present_response(await entity_confirmation_service.review(entity_type))
            await _safe_reply_text(update, text, reply_markup=keyboard)
            return
    if command == "context" and not update.message.text.partition(" ")[2].strip():
        text, keyboard = present_response(_context_hub_response())
        await _safe_reply_text(update, text, reply_markup=keyboard)
        return
    if command in {"person", "context", "prep", "project"}:
        query = update.message.text.partition(" ")[2].strip() if update.message.text else ""
        if not query:
            prompts = {
                "person": "Anh muốn xem person nào? Dùng /person <tên>.",
                "project": "Anh muốn xem project nào? Dùng /project <tên>, hoặc /projects để xem danh sách.",
                "context": "Anh muốn xem context nào? Dùng /context <person hoặc project>.",
                "prep": "Anh muốn chuẩn bị meeting nào? Dùng /prep <person hoặc project>.",
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
    if hasattr(service, "end_of_day_review"):
        actions["endday"] = service.end_of_day_review
    if hasattr(service, "goals"):
        actions["goals"] = service.goals
    action = actions.get(command)
    if action is None:
        return
    text = await action()
    if conversation_service is not None and command in {
        "today",
        "todays",
        "tasks",
        "briefing",
    }:
        await conversation_service.remember_task_list(source_chat_id, text, command)
    await _safe_reply_text(update, text)


async def navigation_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    response = await _navigation_response(query.data, context)
    if response is None:
        await query.answer("Nút này đã hết hiệu lực. Hãy mở lại menu.", show_alert=False)
        return
    await query.answer()
    text, keyboard = present_response(response)
    try:
        await query.edit_message_text(text=text, reply_markup=keyboard)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def memory_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return
    service: MemoryViewService | None = context.application.bot_data.get("memory_view_service")
    if service is None or not query.data:
        await query.answer("Tính năng này chưa sẵn sàng.", show_alert=False)
        return

    response = None
    if query.data == "mem:o":
        response = await service.overview()
    elif query.data == "mem:t:stale:0":
        response = await service.stale()
    elif query.data.startswith(("mem:c:", "mem:k:")):
        parts = query.data.split(":")
        if len(parts) in {3, 5}:
            result = await service.confirm(parts[2])
            if result is not None and len(parts) == 5:
                try:
                    response = await service.topic(parts[3], int(parts[4]))
                except ValueError:
                    response = None
            else:
                response = result
    elif query.data.startswith("mem:r:"):
        parts = query.data.split(":")
        if len(parts) in {3, 5}:
            result = await service.reject(parts[2])
            if result is not None and len(parts) == 5:
                try:
                    response = await service.topic(parts[3], int(parts[4]))
                except ValueError:
                    response = None
            else:
                response = result
    elif query.data.startswith("mem:s:"):
        parts = query.data.split(":")
        if len(parts) in {3, 5}:
            result = await service.mark_stale(parts[2])
            if result is not None and len(parts) == 5:
                try:
                    response = await service.topic(parts[3], int(parts[4]))
                except ValueError:
                    response = None
            else:
                response = result
    elif query.data.startswith("mem:g:"):
        parts = query.data.split(":")
        if len(parts) == 3:
            response = await service.merge_prompt(parts[2])
    elif query.data.startswith("mem:t:"):
        parts = query.data.split(":")
        if len(parts) == 4:
            try:
                response = await service.topic(parts[2], int(parts[3]))
            except ValueError:
                response = None
    if response is None:
        await query.answer("Nút này đã hết hiệu lực. Hãy mở lại /memory.", show_alert=False)
        return

    await query.answer()
    text, keyboard = present_response(response)
    try:
        await query.edit_message_text(text=text, reply_markup=keyboard)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def work_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    service: WorkActionService | None = context.application.bot_data.get("work_action_service")
    if query is None or not query.data or service is None:
        return
    response = await service.handle(query.data)
    if response is None:
        await query.answer("Nút này đã hết hiệu lực. Hãy mở lại danh sách.", show_alert=False)
        return
    await query.answer()
    text, keyboard = present_response(response)
    try:
        await query.edit_message_text(text=text, reply_markup=keyboard)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def clarification_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    service: ClarificationService | None = context.application.bot_data.get(
        "clarification_service"
    )
    if query is None or not query.data or service is None or update.effective_chat is None:
        return
    parts = query.data.split(":")
    if len(parts) != 3 or parts[:2] != ["clar", "scope"] or not parts[2].isdigit():
        await query.answer("Lựa chọn không hợp lệ.", show_alert=False)
        return
    result = await service.answer_pending(str(update.effective_chat.id), parts[2])
    if not result.handled:
        await query.answer("Câu hỏi này đã hết hiệu lực.", show_alert=False)
        return
    await query.answer()
    await query.edit_message_text(result.message)


async def entity_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    service: EntityConfirmationService | None = context.application.bot_data.get(
        "entity_confirmation_service"
    )
    if query is None or not query.data or service is None:
        return
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Yêu cầu không hợp lệ.", show_alert=False)
        return
    action, event_id = parts[1], parts[2]
    if action == "p":
        response = await service.prompt(event_id)
    elif action == "x":
        response = await service.confirm(event_id)
    elif action == "n":
        response = AssistantResponse(title="Đã bỏ qua", summary="Em không lưu biệt danh này.")
    else:
        response = None
    if response is None:
        await query.answer("Gợi ý này đã hết hiệu lực.", show_alert=False)
        return
    await query.answer()
    text, keyboard = present_response(response)
    await query.edit_message_text(text=text, reply_markup=keyboard)


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    source_chat_id = str(update.effective_chat.id) if update.effective_chat else None
    conversation_service: ConversationService | None = context.application.bot_data.get(
        "conversation_service"
    )
    clarification_service: ClarificationService | None = context.application.bot_data.get(
        "clarification_service"
    )
    if source_chat_id and clarification_service:
        pending = await clarification_service.find_pending_for_chat(source_chat_id)
        pending_answer_check = getattr(
            clarification_service, "is_answer_for_pending", None
        )
        is_pending_answer = (
            pending is not None
            and pending_answer_check is not None
            and pending_answer_check(pending, update.message.text)
        )
        if (
            pending is not None
            and conversation_service is not None
            and not is_pending_answer
            and conversation_service.is_explicit_new_action(update.message.text)
        ):
            await clarification_service.cancel_pending_for_chat(
                source_chat_id, update.message.text
            )
        else:
            result = await clarification_service.answer_pending(
                source_chat_id, update.message.text
            )
            if result.handled:
                await _safe_reply_text(update, result.message)
                return
    capture_service: CaptureService = context.application.bot_data["capture_service"]
    request = CaptureRequest(
        source="telegram",
        source_message_id=str(update.message.message_id),
        source_chat_id=source_chat_id,
        raw_text=update.message.text,
    )
    if conversation_service is not None:
        response = await conversation_service.handle_text(request)
        if response.intent in {"query_today", "query_tasks"}:
            await conversation_service.remember_task_list(
                source_chat_id, response.reply, response.intent
            )
        timezone = conversation_service.secretary_service.display_timezone
        reply = _apply_light_tone(response.reply, update.message.text, timezone)
        await _safe_reply_text(update, reply, reply_markup=response.reply_markup)
        return
    response = await capture_service.capture(request)
    await _safe_reply_text(update, format_capture_response(response))


async def _safe_reply_text(update: Update, text: str, **kwargs) -> None:
    if not update.message:
        return
    try:
        chunks = _split_telegram_text(text)
        for index, chunk in enumerate(chunks):
            chunk_kwargs = kwargs if index == len(chunks) - 1 else {}
            await update.message.reply_text(chunk, **chunk_kwargs)
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


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled Telegram update error: %s", context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "MemoCore vừa gặp lỗi khi xử lý yêu cầu này. Lỗi đã được ghi log để kiểm tra."
            )
        except TelegramError:
            logger.warning("Failed to send Telegram error reply", exc_info=True)


def _split_telegram_text(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in text.splitlines():
        addition = len(line) + (1 if current else 0)
        if current and current_length + addition > limit:
            chunks.append("\n".join(current))
            current = []
            current_length = 0
        if len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_length = 0
            chunks.extend(line[index : index + limit] for index in range(0, len(line), limit))
            continue
        current.append(line)
        current_length += len(line) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append("\n".join(current))
    return chunks


async def tag_prompt_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return

    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "tag_prompt":
        await query.answer("Yêu cầu không hợp lệ.", show_alert=True)
        return

    action, note_id = parts[1], parts[2]

    if action not in {"li", "task", "mem", "ignore"}:
        await query.answer("Hành động không hợp lệ.", show_alert=True)
        return

    conversation_service: ConversationService = context.application.bot_data["conversation_service"]
    capture_service: CaptureService = context.application.bot_data["capture_service"]
    note_repo = conversation_service.note_repo

    note = await note_repo.get_by_id(note_id)
    if not note:
        await query.answer("Không tìm thấy ghi chú.", show_alert=True)
        return

    await query.answer()

    if action == "ignore":
        await note_repo.update_processed(
            note.id,
            "Người dùng đã chọn không lưu nội dung này.",
            ["ignored"],
        )
        await query.edit_message_text("Đã bỏ qua ghi chú này.")
        return

    # Failed notes can be reprocessed while preserving the immutable raw input.
    await note_repo.update_status(note.id, NoteStatus.FAILED)

    hashtag = ""
    if action == "li":
        hashtag = " #li"
    elif action == "task":
        hashtag = " #task"
    elif action == "mem":
        hashtag = " #mem"

    request = CaptureRequest(
        source=note.source,
        source_message_id=note.source_message_id,
        source_chat_id=note.source_chat_id,
        raw_text=note.raw_text + hashtag,
    )

    response = await capture_service.capture(request)
    reply = format_capture_response(response)
    await query.edit_message_text(reply)


def register_handlers(
    app: Application,
    owner_id: int,
    capture_service: CaptureService,
    secretary_service: SecretaryService,
    clarification_service: ClarificationService | None = None,
    conversation_service: ConversationService | None = None,
    memory_view_service: MemoryViewService | None = None,
    work_action_service: WorkActionService | None = None,
    entity_confirmation_service: EntityConfirmationService | None = None,
) -> None:
    app.bot_data["telegram_owner_id"] = owner_id
    app.bot_data["capture_service"] = capture_service
    app.bot_data["secretary_service"] = secretary_service
    app.bot_data["clarification_service"] = clarification_service
    app.bot_data["conversation_service"] = conversation_service
    app.bot_data["memory_view_service"] = memory_view_service
    app.bot_data["work_action_service"] = work_action_service
    app.bot_data["entity_confirmation_service"] = entity_confirmation_service
    app.add_error_handler(error_handler)
    app.add_handler(TypeHandler(Update, owner_only_handler), group=-1)
    app.add_handler(CallbackQueryHandler(memory_callback_handler, pattern=r"^mem:"))
    app.add_handler(CallbackQueryHandler(work_callback_handler, pattern=r"^work:"))
    app.add_handler(
        CallbackQueryHandler(clarification_callback_handler, pattern=r"^clar:scope:")
    )
    app.add_handler(CallbackQueryHandler(entity_callback_handler, pattern=r"^entity:"))
    app.add_handler(CallbackQueryHandler(tag_prompt_callback_handler, pattern=r"^tag_prompt:"))
    app.add_handler(CallbackQueryHandler(navigation_callback_handler, pattern=r"^nav:"))
    app.add_handler(CommandHandler("start", start_handler))
    for command in ("linkedin", "li", "task", "t", "mem", "m"):
        app.add_handler(CommandHandler(command, message_handler))
    for command in (
        "today",
        "todays",
        "tomorrow",
        "work",
        "tasks",
        "reminders",
        "waiting",
        "projects",
        "memory",
        "briefing",
        "weekly",
        "endday",
        "goals",
        "help",
        "people",
        "person",
        "project",
        "commitments",
        "context",
        "prep",
        "capture",
    ):
        app.add_handler(CommandHandler(command, secretary_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))


async def _navigation_response(
    callback_data: str, context: ContextTypes.DEFAULT_TYPE
) -> AssistantResponse | None:
    service: SecretaryService = context.application.bot_data["secretary_service"]
    work_action_service: WorkActionService | None = context.application.bot_data.get(
        "work_action_service"
    )
    memory_view_service: MemoryViewService | None = context.application.bot_data.get(
        "memory_view_service"
    )
    actions = {
        "nav:work": lambda: _work_hub_response(),
        "nav:context": lambda: _context_hub_response(),
        "nav:capture": lambda: _capture_hub_response(),
        "nav:work:waiting": lambda: AssistantResponse(title="Đang chờ", summary=None),
        "nav:work:commitments": lambda: AssistantResponse(title="Cam kết", summary=None),
        "nav:context:people": lambda: AssistantResponse(title="Nhân sự", summary=None),
        "nav:context:projects": lambda: AssistantResponse(title="Dự án", summary=None),
        "nav:context:prep": lambda: _prep_help_response(),
        "nav:capture:task": lambda: _capture_detail_response("task"),
        "nav:capture:memory": lambda: _capture_detail_response("memory"),
        "nav:capture:linkedin": lambda: _capture_detail_response("linkedin"),
    }
    if callback_data == "nav:work:tasks" and work_action_service is not None:
        return await work_action_service.tasks_view()
    if callback_data == "nav:work:reminders" and work_action_service is not None:
        return await work_action_service.reminders_view()
    if callback_data == "nav:work:today":
        return AssistantResponse(title="Hôm nay", summary=await service.today())
    if callback_data == "nav:work":
        return _work_hub_response(await service.work_dashboard())
    if callback_data == "nav:work:waiting":
        return AssistantResponse(title="Đang chờ", summary=await service.waiting())
    if callback_data == "nav:work:commitments":
        return AssistantResponse(title="Cam kết", summary=await service.commitments())
    if callback_data == "nav:memory" and memory_view_service is not None:
        return await memory_view_service.overview()
    if callback_data == "nav:context:people":
        return AssistantResponse(title="Nhân sự", summary=await service.people())
    if callback_data == "nav:context:projects":
        return AssistantResponse(title="Dự án", summary=await service.projects())
    action = actions.get(callback_data)
    return action() if action else None


def _work_hub_response(summary: str | None = None) -> AssistantResponse:
    return AssistantResponse(
        title="Work",
        summary=summary
        or "Chọn phần anh muốn xem. Các shortcut cũ như /tasks, /reminders, /waiting vẫn dùng được.",
        actions=[
            AssistantAction(label="Hôm nay", action_id="nav:work:today", row=0),
            AssistantAction(label="Tasks", action_id="nav:work:tasks", row=1),
            AssistantAction(label="Reminders", action_id="nav:work:reminders", row=1),
            AssistantAction(label="Waiting", action_id="nav:work:waiting", row=2),
            AssistantAction(label="Commitments", action_id="nav:work:commitments", row=2),
        ],
    )


def _context_hub_response() -> AssistantResponse:
    return AssistantResponse(
        title="Context",
        summary="People, projects và meeting prep. Dùng /person <tên>, /project <tên>, hoặc /prep <tên> khi anh đã biết cần xem ai.",
        actions=[
            AssistantAction(label="People", action_id="nav:context:people", row=0),
            AssistantAction(label="Projects", action_id="nav:context:projects", row=0),
            AssistantAction(label="Meeting prep", action_id="nav:context:prep", row=1),
            AssistantAction(label="Memory", action_id="nav:memory", row=1),
        ],
    )


def _capture_hub_response() -> AssistantResponse:
    return AssistantResponse(
        title="Capture",
        summary="Gửi nội dung tự nhiên, hoặc ép loại bằng command/hashtag ở cuối tin nhắn.",
        actions=[
            AssistantAction(label="Task", action_id="nav:capture:task", row=0),
            AssistantAction(label="Memory", action_id="nav:capture:memory", row=0),
            AssistantAction(label="LinkedIn", action_id="nav:capture:linkedin", row=1),
        ],
    )


def _prep_help_response() -> AssistantResponse:
    return AssistantResponse(
        title="Meeting prep",
        summary="Dùng /prep <person hoặc project>. Ví dụ: /prep Alex hoặc /prep MemoCore.",
        actions=[AssistantAction(label="Quay lại", action_id="nav:context", row=0)],
    )


def _capture_detail_response(kind: str) -> AssistantResponse:
    examples = {
        "task": "Dùng /task <nội dung> hoặc thêm #task ở cuối. Ví dụ: /task gọi Alex lúc 15h.",
        "memory": "Dùng /mem <nội dung> hoặc thêm #mem ở cuối. Ví dụ: /mem Alex thích nhận brief dạng bullet.",
        "linkedin": "Dùng /li <nội dung> hoặc thêm #li ở cuối để lưu ý tưởng/content note.",
    }
    titles = {"task": "Capture task", "memory": "Capture memory", "linkedin": "Capture LinkedIn"}
    return AssistantResponse(
        title=titles[kind],
        summary=examples[kind],
        actions=[AssistantAction(label="Quay lại", action_id="nav:capture", row=0)],
    )


def _help_response() -> AssistantResponse:
    return AssistantResponse(
        title="MemoCore help",
        summary="Menu / chỉ hiện các cửa chính. Các shortcut vẫn dùng được khi anh nhớ chính xác.",
        sections=[
            AssistantSection(
                heading="Cửa chính",
                lines=[
                    "/today - top việc hôm nay",
                    "/work - dashboard work/open loops",
                    "/memory - review bộ nhớ",
                    "/context - people/projects/prep",
                    "/briefing - briefing trong ngày",
                    "/capture - cách lưu nhanh",
                ],
            ),
            AssistantSection(
                heading="Shortcut ẩn",
                lines=[
                    "/task <nội dung>, /mem <nội dung>, /li <nội dung>",
                    "/prep <person/project>, /person <tên>, /project <tên>",
                    "/people review, /projects review, /memory stale, /goals, /endday",
                ],
            ),
        ],
        actions=[
            AssistantAction(label="Work", action_id="nav:work", row=0),
            AssistantAction(label="Context", action_id="nav:context", row=0),
            AssistantAction(label="Capture", action_id="nav:capture", row=1),
        ],
    )


def _apply_light_tone(reply: str, user_text: str, display_timezone) -> str:
    normalized = user_text.casefold()
    if any(signal in normalized for signal in ("mệt", "met", "đuối", "duoi", "bận", "ban qua")):
        return f"{reply}\n\nEm giữ phần này thật ngắn để anh đỡ phải xử lý thêm."
    if not _is_capture_confirmation(reply):
        return reply
    local_hour = datetime.now(UTC).astimezone(display_timezone).hour
    if local_hour >= 23 or local_hour < 5:
        return f"{reply}\n\nĐã khá khuya, em ghi nhận gọn để anh có thể nghỉ sớm."
    return reply


def _is_capture_confirmation(reply: str) -> bool:
    normalized = reply.casefold()
    return any(
        signal in normalized
        for signal in (
            "em đã ghi nhận",
            "đã tạo/cập nhật",
            "đã lưu",
            "got it. updated",
        )
    )
