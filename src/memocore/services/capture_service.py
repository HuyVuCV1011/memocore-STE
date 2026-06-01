from __future__ import annotations

from datetime import UTC, datetime

from memocore.adapters.llm.base import ExtractionError
from memocore.adapters.storage.repositories import (
    NoteRepository,
    ProjectRepository,
    TaskRepository,
    parse_model_datetime,
)
from memocore.domain.models import EventType, Note, NoteStatus, Task
from memocore.domain.schemas import CaptureRequest, CaptureResponse
from memocore.services.event_service import EventService
from memocore.services.memory_service import MemoryService
from memocore.services.reminder_service import ReminderService
from memocore.services.task_extraction_service import ExtractionService


class CaptureService:
    def __init__(
        self,
        note_repo: NoteRepository,
        task_repo: TaskRepository,
        project_repo: ProjectRepository,
        extraction_service: ExtractionService,
        memory_service: MemoryService,
        reminder_service: ReminderService,
        event_service: EventService,
    ):
        self.note_repo = note_repo
        self.task_repo = task_repo
        self.project_repo = project_repo
        self.extraction_service = extraction_service
        self.memory_service = memory_service
        self.reminder_service = reminder_service
        self.event_service = event_service

    async def capture(self, request: CaptureRequest) -> CaptureResponse:
        existing = await self.note_repo.find_by_source_message(
            request.source, request.source_chat_id, request.source_message_id
        )
        if existing:
            tasks = await self.task_repo.list_by_note(existing.id)
            reminders = await self.reminder_service.reminder_repo.list_by_note(existing.id)
            memories = await self.memory_service.memory_repo.list_by_note(existing.id)
            return CaptureResponse(
                note_id=existing.id,
                summary=existing.summary or "Already captured.",
                tasks_created=len(tasks),
                reminders_created=len(reminders),
                memories_created=len(memories),
                duplicate=True,
            )

        note = Note(
            source=request.source,
            source_message_id=request.source_message_id,
            source_chat_id=request.source_chat_id,
            raw_text=request.raw_text,
        )
        await self.note_repo.create(note)
        await self.event_service.append_event(EventType.NOTE_CAPTURED, "note", note.id)
        try:
            extraction = await self.extraction_service.extract(request.raw_text)
        except ExtractionError as exc:
            await self.note_repo.update_status(note.id, NoteStatus.FAILED)
            await self.event_service.append_event(
                EventType.MODEL_OUTPUT_INVALID,
                "note",
                note.id,
                {"error": str(exc)},
            )
            return CaptureResponse(
                note_id=note.id,
                summary="Raw note saved, but extraction failed.",
                errors=[str(exc)],
            )

        async with self.note_repo.database.transaction():
            explicit_projects = [
                hint for hint in extraction.projects if hint.name.lower() in request.raw_text.lower()
            ]
            project_id: str | None = None
            for hint in explicit_projects:
                project = await self.project_repo.find_or_create(hint.name)
                await self.event_service.append_event(
                    EventType.PROJECT_SEEN, "project", project.id,
                    {"source_note_id": note.id, "confidence": hint.confidence},
                )
                if len(explicit_projects) == 1:
                    project_id = project.id

            tasks_created = 0
            for candidate in extraction.tasks:
                task = Task(
                    title=candidate.title, description=candidate.description,
                    priority=candidate.priority, due_at=parse_model_datetime(candidate.due_at),
                    project_id=project_id, source_note_id=note.id, confidence=candidate.confidence,
                )
                created_task = await self.task_repo.create(task)
                await self.event_service.append_event(
                    EventType.TASK_CANDIDATE_CREATED, "task", created_task.id,
                    {"source_note_id": note.id},
                )
                tasks_created += 1

            reminders = await self.reminder_service.persist_candidates(extraction.reminders, note.id)
            for reminder in reminders:
                if reminder.remind_at is not None and reminder.remind_at > datetime.now(UTC):
                    await self.reminder_service.schedule_reminder(reminder.id)

            memories = await self.memory_service.persist_candidates(
                extraction.memories, note.id, project_id=project_id
            )
            await self.note_repo.update_processed(note.id, extraction.summary, extraction.tags)
            await self._record_quality_warnings(note.id, request.raw_text, extraction)
            await self.event_service.append_event(
                EventType.NOTE_PROCESSED, "note", note.id,
                {"tasks_created": tasks_created, "reminders_created": len(reminders),
                 "memories_created": len(memories)},
            )

        return CaptureResponse(
            note_id=note.id,
            summary=extraction.summary,
            tasks_created=tasks_created,
            reminders_created=len(reminders),
            memories_created=len(memories),
        )

    async def _record_quality_warnings(self, note_id: str, raw_text: str, extraction) -> None:
        lowered = raw_text.lower()
        warnings: list[str] = []
        if any(signal in lowered for signal in ("remind me", "nhắc tôi", "nhắc")) and not extraction.reminders:
            warnings.append("reminder_language_without_reminder")
        if any(signal in lowered for signal in ("remember that", "nhớ rằng")) and not extraction.memories:
            warnings.append("memory_language_without_memory")
        if warnings:
            await self.event_service.append_event(
                EventType.EXTRACTION_LIKELY_INCOMPLETE, "note", note_id, {"warnings": warnings}
            )
