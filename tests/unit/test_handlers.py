from types import SimpleNamespace

from memocore.adapters.telegram.handlers import format_capture_response, secretary_handler
from memocore.domain.schemas import CaptureResponse


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
        clarification_question='When should I remind you about "Call John"?',
    )

    assert 'When should I remind you about "Call John"?' in format_capture_response(response)


async def test_secretary_handler_ignores_empty_command_text():
    replies: list[str] = []

    async def reply_text(text: str) -> None:
        replies.append(text)

    update = SimpleNamespace(message=SimpleNamespace(text=None, reply_text=reply_text))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"secretary_service": object()}))

    await secretary_handler(update, context)

    assert replies == []
