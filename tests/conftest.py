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
    ActivityLinkRepository,
    ClarificationRequestRepository,
    ChatContextRepository,
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
from memocore.adapters.storage.knowledge_repositories import (
    DecisionRepository,
    KnowledgeRelationRepository,
    OrganizationRepository,
)
from memocore.domain.schemas import NoteExtraction
from memocore.services.capture_service import CaptureService
from memocore.services.clarification_service import ClarificationService
from memocore.services.event_service import EventService
from memocore.services.memory_service import MemoryService
from memocore.services.reminder_service import ReminderService
from memocore.services.task_extraction_service import ExtractionService
from memocore.services.activity_reconciliation_service import (
    ActivityReconciliationService,
)
from memocore.services.task_operation_service import TaskOperationService
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
        "chat_contexts": ChatContextRepository(tmp_database),
        "organizations": OrganizationRepository(tmp_database),
        "decisions": DecisionRepository(tmp_database),
        "knowledge_relations": KnowledgeRelationRepository(tmp_database),
        "activity_links": ActivityLinkRepository(tmp_database),
    }


@pytest.fixture
def capture_service(repos, fake_provider):
    event_service = EventService(repos["events"])
    activity_reconciliation_service = ActivityReconciliationService(
        repos["tasks"],
        repos["meetings"],
        repos["people"],
        repos["projects"],
        repos["activity_links"],
        event_service,
    )
    task_operation_service = TaskOperationService(
        repos["tasks"], event_service, activity_reconciliation_service
    )
    reminder_service = ReminderService(repos["reminders"], event_service)
    clarification_service = ClarificationService(
        repos["clarifications"],
        repos["reminders"],
        reminder_service,
        event_service,
        task_repo=repos["tasks"],
        followup_repo=repos["followups"],
        commitment_repo=repos["commitments"],
        task_operation_service=task_operation_service,
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
        repos["organizations"],
        repos["decisions"],
        repos["knowledge_relations"],
        activity_reconciliation_service,
    )
