from memocore.domain.models import EventType
from memocore.domain.models import Note
from memocore.services.event_service import EventService
from memocore.services.memory_service import MemoryService
from tests.fixtures.extraction_responses import PROFILE_MEMORY


async def test_memory_candidates_are_persisted(repos):
    note = await repos["notes"].create(Note(raw_text="remember this"))
    event_service = EventService(repos["events"])
    service = MemoryService(repos["memory"], event_service)

    created = await service.persist_candidates(PROFILE_MEMORY.memories, note.id)
    events = await event_service.list_events_for_entity("memory_item", created[0].id)

    assert created[0].source_note_id == note.id
    assert created[0].bucket == "profile"
    assert created[0].last_confirmed_at is None
    assert events[0].event_type == EventType.MEMORY_CANDIDATE_CREATED
