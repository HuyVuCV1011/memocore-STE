from __future__ import annotations

from collections.abc import AsyncIterator
import json

import pytest

from memocore.adapters.llm.base import (
    ChatRequest,
    ChatResponse,
    ProviderInfo,
    StructuredOutputMode,
)
from memocore.adapters.storage.repositories import (
    ClarificationRequestRepository,
    CommitmentRepository,
    EventLogRepository,
    FollowUpRepository,
    MeetingRepository,
    MemoryItemRepository,
    NoteRepository,
    PersonRepository,
    ProjectRepository,
    ReminderRepository,
    TaskRepository,
)
from memocore.adapters.storage.sqlite import Database
from memocore.domain.schemas import NoteExtraction
from memocore.services.capture_service import CaptureService
from memocore.services.clarification_service import ClarificationService
from memocore.services.event_service import EventService
from memocore.services.memory_service import MemoryService
from memocore.services.reminder_service import ReminderService
from memocore.services.task_extraction_service import ExtractionService
from tests.fixtures.extraction_responses import TASK_AND_REMINDER


class FakeProvider:
    def __init__(self, response: NoteExtraction = TASK_AND_REMINDER):
        self.response = response
        self.calls: list[ChatRequest] = []

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo("fake", "fake-model", StructuredOutputMode.JSON_MODE)

    async def health_check(self) -> bool:
        return True

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        return ChatResponse(
            content=json.dumps(self.response.model_dump(mode="json")),
            model="fake-model",
        )


@pytest.fixture
async def tmp_database(tmp_path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "memocore.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def repos(tmp_database):
    return {
        "notes": NoteRepository(tmp_database),
        "tasks": TaskRepository(tmp_database),
        "reminders": ReminderRepository(tmp_database),
        "projects": ProjectRepository(tmp_database),
        "people": PersonRepository(tmp_database),
        "meetings": MeetingRepository(tmp_database),
        "memory": MemoryItemRepository(tmp_database),
        "events": EventLogRepository(tmp_database),
        "followups": FollowUpRepository(tmp_database),
        "commitments": CommitmentRepository(tmp_database),
        "clarifications": ClarificationRequestRepository(tmp_database),
    }


@pytest.fixture
def capture_service(repos, fake_provider):
    event_service = EventService(repos["events"])
    reminder_service = ReminderService(repos["reminders"], event_service)
    clarification_service = ClarificationService(
        repos["clarifications"],
        repos["reminders"],
        reminder_service,
        event_service,
        task_repo=repos["tasks"],
    )
    return CaptureService(
        repos["notes"],
        repos["tasks"],
        repos["projects"],
        ExtractionService(fake_provider),
        MemoryService(repos["memory"], event_service),
        reminder_service,
        event_service,
        clarification_service,
        repos["people"],
        repos["meetings"],
        repos["followups"],
        repos["commitments"],
    )
