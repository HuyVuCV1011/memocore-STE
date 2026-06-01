from memocore.adapters.telegram.handlers import format_capture_response
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
