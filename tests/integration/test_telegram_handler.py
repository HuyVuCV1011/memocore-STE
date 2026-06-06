from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock

from telegram import Update
from telegram.ext import ApplicationBuilder, CallbackContext

from memocore.adapters.telegram.handlers import message_handler
from tests.fixtures.extraction_responses import MISSING_REMINDER_TIME
from tests.fixtures.telegram_updates import MESSAGE_UPDATE


def build_context(bot_data: dict[str, Any]) -> CallbackContext:
    app = ApplicationBuilder().token("test").build()
    app.bot_data.update(bot_data)
    return CallbackContext(application=app)


def build_real_update(update_dict: dict[str, Any]) -> Update:
    app = ApplicationBuilder().token("test").build()
    return Update.de_json(deepcopy(update_dict), app.bot)


def patch_send_message(monkeypatch, update: Update) -> AsyncMock:
    send_message = AsyncMock(return_value=None)
    monkeypatch.setattr(type(update.message.get_bot()), "send_message", send_message)
    return send_message


async def test_message_handler_with_real_services(monkeypatch, capture_service, fake_provider, repos):
    update = build_real_update(MESSAGE_UPDATE)
    context = build_context({"capture_service": capture_service})
    send_message = patch_send_message(monkeypatch, update)

    await message_handler(update, context)

    note = await repos["notes"].find_by_source_message("telegram", "9001", "501")
    tasks = await repos["tasks"].list_by_note(note.id)
    reminders = await repos["reminders"].list_by_note(note.id)

    assert note.raw_text == "Nhắc tôi 7h sáng mai họp với Alex"
    assert tasks[0].title == "Call Alex about the budget"
    assert reminders[0].title == "Call Alex about the budget"
    assert len(fake_provider.calls) == 1
    assert "Call Alex about the budget" in send_message.await_args.kwargs["text"]


async def test_message_handler_with_clarification(monkeypatch, capture_service, fake_provider, repos):
    fake_provider.response = MISSING_REMINDER_TIME
    initial_update = build_real_update(MESSAGE_UPDATE)
    answer_dict = deepcopy(MESSAGE_UPDATE)
    answer_dict["update_id"] = 100006
    answer_dict["message"]["message_id"] = 506
    answer_dict["message"]["text"] = "tomorrow 9am"
    answer_update = build_real_update(answer_dict)
    context = build_context(
        {
            "capture_service": capture_service,
            "clarification_service": capture_service.clarification_service,
        }
    )
    send_message = patch_send_message(monkeypatch, initial_update)

    await message_handler(initial_update, context)
    await message_handler(answer_update, context)

    pending = await repos["clarifications"].find_pending_for_chat("9001")
    reminders = await repos["reminders"].list_by_note(
        (await repos["notes"].find_by_source_message("telegram", "9001", "501")).id
    )

    assert pending is None
    assert reminders[0].remind_at is not None
    assert len(fake_provider.calls) == 1
    assert 'Khi nào bạn muốn được nhắc về "Call John"?' in send_message.await_args_list[0].kwargs["text"]
    assert "Reminder được đặt" in send_message.await_args_list[1].kwargs["text"]
