from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock

from telegram import Update
from telegram.error import NetworkError
from telegram.ext import ApplicationBuilder, CallbackContext

from memocore.adapters.telegram.handlers import (
    format_capture_response,
    message_handler,
    secretary_handler,
    start_handler,
)
from memocore.domain.schemas import CaptureResponse
from tests.fixtures.telegram_updates import (
    COMMAND_UPDATE,
    DOCUMENT_UPDATE,
    EDITED_MESSAGE_UPDATE,
    MESSAGE_UPDATE,
    START_UPDATE,
    VOICE_UPDATE,
)


def build_context(bot_data: dict[str, Any] | None = None) -> CallbackContext:
    app = ApplicationBuilder().token("test").build()
    if bot_data:
        app.bot_data.update(bot_data)
    return CallbackContext(application=app)


def build_real_update(update_dict: dict[str, Any]) -> Update:
    app = ApplicationBuilder().token("test").build()
    return Update.de_json(deepcopy(update_dict), app.bot)


def patch_send_message(monkeypatch, update: Update) -> AsyncMock:
    send_message = AsyncMock(return_value=None)
    monkeypatch.setattr(type(update.message.get_bot()), "send_message", send_message)
    return send_message


def patch_send_message_failure(monkeypatch, update: Update) -> AsyncMock:
    send_message = AsyncMock(side_effect=NetworkError("telegram network down"))
    monkeypatch.setattr(type(update.message.get_bot()), "send_message", send_message)
    return send_message


def test_format_capture_response_success():
    response = CaptureResponse(
        note_id="note-1",
        summary="Saved",
        tasks_created=1,
        reminders_created=1,
        memories_created=0,
    )

    assert "Saved" in format_capture_response(response)
    assert "1 task(s)" in format_capture_response(response)


def test_format_capture_response_error():
    response = CaptureResponse(note_id="note-1", summary="Failed", errors=["bad json"])

    assert "raw note saved" in format_capture_response(response)


def test_format_capture_response_includes_clarification_question():
    response = CaptureResponse(
        note_id="note-1",
        summary="Call John.",
        reminders_created=1,
        clarification_question='Khi nào bạn muốn được nhắc về "Call John"?',
    )

    assert 'Khi nào bạn muốn được nhắc về "Call John"?' in format_capture_response(response)


async def test_secretary_handler_ignores_empty_command_text(monkeypatch):
    update_dict = deepcopy(COMMAND_UPDATE)
    update_dict["message"]["text"] = None
    update = build_real_update(update_dict)
    context = build_context({"secretary_service": object()})
    send_message = patch_send_message(monkeypatch, update)

    await secretary_handler(update, context)

    send_message.assert_not_awaited()


async def test_secretary_handler_accepts_todays_alias(monkeypatch):
    class FakeSecretary:
        async def today(self) -> str:
            return "today view"

        async def tomorrow(self) -> str:
            return "tomorrow view"

        async def tasks(self) -> str:
            return "tasks view"

        async def reminders(self) -> str:
            return "reminders view"

        async def waiting(self) -> str:
            return "waiting view"

        async def projects(self) -> str:
            return "projects view"

        async def memories(self) -> str:
            return "memory view"

        async def daily_briefing(self) -> str:
            return "briefing view"

        async def weekly_review(self) -> str:
            return "weekly view"

    update_dict = deepcopy(COMMAND_UPDATE)
    update_dict["message"]["text"] = "/todays"
    update_dict["message"]["entities"] = [{"offset": 0, "length": 7, "type": "bot_command"}]
    update = build_real_update(update_dict)
    context = build_context({"secretary_service": FakeSecretary()})
    send_message = patch_send_message(monkeypatch, update)

    await secretary_handler(update, context)

    send_message.assert_awaited_once()
    assert send_message.await_args.kwargs["chat_id"] == 9001
    assert send_message.await_args.kwargs["text"] == "today view"


async def test_secretary_handler_accepts_briefing_command(monkeypatch):
    class FakeSecretary:
        async def today(self) -> str:
            return "today view"

        async def tomorrow(self) -> str:
            return "tomorrow view"

        async def tasks(self) -> str:
            return "tasks view"

        async def reminders(self) -> str:
            return "reminders view"

        async def waiting(self) -> str:
            return "waiting view"

        async def projects(self) -> str:
            return "projects view"

        async def memories(self) -> str:
            return "memory view"

        async def daily_briefing(self) -> str:
            return "briefing view"

        async def weekly_review(self) -> str:
            return "weekly view"

    update_dict = deepcopy(COMMAND_UPDATE)
    update_dict["message"]["text"] = "/briefing"
    update_dict["message"]["entities"] = [{"offset": 0, "length": 9, "type": "bot_command"}]
    update = build_real_update(update_dict)
    context = build_context({"secretary_service": FakeSecretary()})
    send_message = patch_send_message(monkeypatch, update)

    await secretary_handler(update, context)

    send_message.assert_awaited_once()
    assert send_message.await_args.kwargs["text"] == "briefing view"


async def test_start_handler_reply(monkeypatch):
    update = build_real_update(START_UPDATE)
    context = build_context()
    send_message = patch_send_message(monkeypatch, update)

    await start_handler(update, context)

    send_message.assert_awaited_once()
    assert send_message.await_args.kwargs["chat_id"] == 9001
    assert "MemoCore is ready" in send_message.await_args.kwargs["text"]


async def test_message_handler_calls_capture(monkeypatch, capture_service, fake_provider):
    update = build_real_update(MESSAGE_UPDATE)
    context = build_context({"capture_service": capture_service})
    send_message = patch_send_message(monkeypatch, update)

    await message_handler(update, context)

    assert len(fake_provider.calls) == 1
    assert "Nhắc tôi 7h sáng mai họp với Alex" in fake_provider.calls[0].messages[-1].content
    send_message.assert_awaited_once()
    assert "Call Alex about the budget" in send_message.await_args.kwargs["text"]


async def test_message_handler_ignores_empty_text(monkeypatch, capture_service, fake_provider):
    update_dict = deepcopy(MESSAGE_UPDATE)
    update_dict["message"]["text"] = None
    update = build_real_update(update_dict)
    context = build_context({"capture_service": capture_service})
    send_message = patch_send_message(monkeypatch, update)

    await message_handler(update, context)

    assert fake_provider.calls == []
    send_message.assert_not_awaited()


async def test_message_handler_ignores_edited_message(capture_service, fake_provider):
    update = build_real_update(EDITED_MESSAGE_UPDATE)
    context = build_context({"capture_service": capture_service})

    await message_handler(update, context)

    assert fake_provider.calls == []


async def test_message_handler_ignores_document_without_text(
    monkeypatch, capture_service, fake_provider
):
    update = build_real_update(DOCUMENT_UPDATE)
    context = build_context({"capture_service": capture_service})
    send_message = patch_send_message(monkeypatch, update)

    await message_handler(update, context)

    assert fake_provider.calls == []
    send_message.assert_not_awaited()


async def test_message_handler_ignores_voice_without_text(
    monkeypatch, capture_service, fake_provider
):
    update = build_real_update(VOICE_UPDATE)
    context = build_context({"capture_service": capture_service})
    send_message = patch_send_message(monkeypatch, update)

    await message_handler(update, context)

    assert fake_provider.calls == []
    send_message.assert_not_awaited()


async def test_message_handler_keeps_capture_when_telegram_reply_fails(
    monkeypatch, capture_service, fake_provider, repos
):
    update = build_real_update(MESSAGE_UPDATE)
    context = build_context({"capture_service": capture_service})
    send_message = patch_send_message_failure(monkeypatch, update)

    await message_handler(update, context)

    note = await repos["notes"].find_by_source_message("telegram", "9001", "501")
    assert note is not None
    assert note.raw_text == "Nhắc tôi 7h sáng mai họp với Alex"
    assert len(fake_provider.calls) == 1
    send_message.assert_awaited_once()


async def test_same_source_message_id_not_processed_twice(
    monkeypatch, capture_service, fake_provider
):
    update = build_real_update(MESSAGE_UPDATE)
    context = build_context({"capture_service": capture_service})
    send_message = patch_send_message(monkeypatch, update)

    await message_handler(update, context)
    await message_handler(update, context)

    assert len(fake_provider.calls) == 1
    assert send_message.await_count == 2
    assert "Call Alex about the budget" in send_message.await_args_list[1].kwargs["text"]
