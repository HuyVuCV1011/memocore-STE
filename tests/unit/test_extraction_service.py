from memocore.services.task_extraction_service import ExtractionService
from tests.conftest import FakeProvider
from tests.fixtures.extraction_responses import NO_ACTION, PROFILE_MEMORY, TASK_AND_REMINDER
from tests.fixtures.sample_notes import NO_ACTION_NOTE, PROFILE_NOTE, TASK_NOTE


async def test_task_and_reminder_fixture():
    service = ExtractionService(FakeProvider(TASK_AND_REMINDER))

    result = await service.extract(TASK_NOTE)

    assert result.tasks
    assert result.reminders[0].remind_at


async def test_profile_memory_fixture():
    service = ExtractionService(FakeProvider(PROFILE_MEMORY))

    result = await service.extract(PROFILE_NOTE)

    assert result.memories[0].bucket == "profile"


async def test_no_action_fixture():
    service = ExtractionService(FakeProvider(NO_ACTION))

    result = await service.extract(NO_ACTION_NOTE)

    assert result.summary
    assert result.tasks == []
