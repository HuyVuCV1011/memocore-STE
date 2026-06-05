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
        clarification_question='Khi nào bạn muốn được nhắc về "Call John"?',
    )

    assert 'Khi nào bạn muốn được nhắc về "Call John"?' in format_capture_response(response)


async def test_secretary_handler_ignores_empty_command_text():
    replies: list[str] = []

    async def reply_text(text: str) -> None:
        replies.append(text)

    update = SimpleNamespace(message=SimpleNamespace(text=None, reply_text=reply_text))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"secretary_service": object()}))

    await secretary_handler(update, context)

    assert replies == []


async def test_secretary_handler_accepts_todays_alias():
    replies: list[str] = []

    async def reply_text(text: str) -> None:
        replies.append(text)

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

    update = SimpleNamespace(message=SimpleNamespace(text="/todays", reply_text=reply_text))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"secretary_service": FakeSecretary()}))

    await secretary_handler(update, context)

    assert replies == ["today view"]
