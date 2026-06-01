from memocore.domain.models import EventType
from memocore.services.event_service import EventService


async def test_event_creation_round_trip(repos):
    service = EventService(repos["events"])

    event = await service.append_event(
        EventType.NOTE_CAPTURED, "note", "note-1", {"raw": True}
    )
    events = await service.list_events_for_entity("note", "note-1")

    assert event.id
    assert events[0].payload == {"raw": True}
