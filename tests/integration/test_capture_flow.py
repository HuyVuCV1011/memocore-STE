from memocore.domain.models import NoteStatus
from memocore.domain.schemas import CaptureRequest


async def test_capture_flow_persists_extracted_objects(capture_service, repos):
    response = await capture_service.capture(
        CaptureRequest(raw_text="Remind me tomorrow to call Alex", source_chat_id="123")
    )

    note = await repos["notes"].get_by_id(response.note_id)
    tasks = await repos["tasks"].list_by_note(response.note_id)
    reminders = await repos["reminders"].list_by_note(response.note_id)
    events = await repos["events"].list_by_entity("note", response.note_id)

    assert note.status == NoteStatus.PROCESSED
    assert tasks[0].title == "Call Alex about the budget"
    assert reminders[0].status == "scheduled"
    assert response.tasks_created == 1
    assert [event.event_type for event in events]
