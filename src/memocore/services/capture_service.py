from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
import re
import unicodedata

from memocore.adapters.llm.base import ExtractionError
from memocore.adapters.storage.repositories import (
    NoteRepository,
    ProjectRepository,
    TaskRepository,
    parse_model_datetime,
)
from memocore.domain.models import EventType, Note, NoteStatus, Reminder, Task, TaskStatus
from memocore.domain.schemas import CaptureRequest, CaptureResponse
from memocore.services.event_service import EventService
from memocore.services.clarification_service import ClarificationService
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
        clarification_service: ClarificationService | None = None,
    ):
        self.note_repo = note_repo
        self.task_repo = task_repo
        self.project_repo = project_repo
        self.extraction_service = extraction_service
        self.memory_service = memory_service
        self.reminder_service = reminder_service
        self.event_service = event_service
        self.clarification_service = clarification_service

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

        if _is_state_query(request.raw_text):
            await self.note_repo.update_processed(
                note.id,
                _state_query_summary(request.raw_text),
                ["query"],
            )
            return CaptureResponse(
                note_id=note.id,
                summary=_state_query_summary(request.raw_text),
            )

        delete_query = _memory_delete_query(request.raw_text)
        if delete_query is not None:
            deleted = await self.memory_service.delete_matching(delete_query)
            await self.note_repo.update_processed(
                note.id,
                f"Memory delete request handled: {deleted} item(s) removed.",
                ["memory", "delete"],
            )
            return CaptureResponse(
                note_id=note.id,
                summary=f"Memory delete request handled: {deleted} item(s) removed.",
                memories_deleted=deleted,
            )

        recurring = _parse_recurring_reminder(request.raw_text)
        if recurring is not None:
            title, remind_at, recurrence_rule = recurring
            reminder = await self.reminder_service.reminder_repo.create(
                Reminder(
                    title=title,
                    remind_at=remind_at,
                    source_note_id=note.id,
                    recurrence_rule=recurrence_rule,
                    confidence=1.0,
                )
            )
            await self.event_service.append_event(
                EventType.REMINDER_CANDIDATE_CREATED,
                "reminder",
                reminder.id,
                {"source_note_id": note.id, "recurrence_rule": recurrence_rule},
            )
            await self.reminder_service.schedule_reminder(reminder.id)
            summary = f"Recurring reminder scheduled: {title}"
            await self.note_repo.update_processed(note.id, summary, ["reminder", "recurring"])
            return CaptureResponse(
                note_id=note.id,
                summary=summary,
                reminders_created=1,
            )

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
            is_memory_correction = _is_memory_correction(request.raw_text)
            if not is_memory_correction:
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

            tasks_completed = await self._complete_matching_tasks(note.id, request.raw_text)

            reminders = await self.reminder_service.persist_candidates(extraction.reminders, note.id)
            clarification_question: str | None = None
            for reminder in reminders:
                if reminder.remind_at is not None and reminder.remind_at > datetime.now(UTC):
                    await self.reminder_service.schedule_reminder(reminder.id)
                elif (
                    reminder.remind_at is None
                    and self.clarification_service is not None
                    and request.source_chat_id
                    and clarification_question is None
                ):
                    clarification = await self.clarification_service.request_reminder_time(
                        source_chat_id=request.source_chat_id,
                        source_message_id=request.source_message_id,
                        reminder_id=reminder.id,
                        reminder_title=reminder.title,
                    )
                    clarification_question = clarification.question

            memories = []
            if not tasks_completed:
                memories = await self.memory_service.persist_candidates(
                    extraction.memories,
                    note.id,
                    project_id=project_id,
                    supersede_related=is_memory_correction,
                )
            await self.note_repo.update_processed(note.id, extraction.summary, extraction.tags)
            await self._record_quality_warnings(note.id, request.raw_text, extraction)
            await self.event_service.append_event(
                EventType.NOTE_PROCESSED, "note", note.id,
                {
                    "tasks_created": tasks_created,
                    "tasks_completed": tasks_completed,
                    "reminders_created": len(reminders),
                    "memories_created": len(memories),
                },
            )

        return CaptureResponse(
            note_id=note.id,
            summary=extraction.summary,
            tasks_created=tasks_created,
            tasks_completed=tasks_completed,
            reminders_created=len(reminders),
            memories_created=len(memories),
            clarification_question=clarification_question,
        )

    async def _complete_matching_tasks(self, note_id: str, raw_text: str) -> int:
        if not _is_completion_note(raw_text):
            return 0
        active_tasks = await self.task_repo.list_active()
        matched = [task for task in active_tasks if _matches_task(raw_text, task.title)]
        for task in matched:
            await self.task_repo.update_status(task.id, TaskStatus.DONE.value)
            await self.event_service.append_event(
                EventType.TASK_DONE,
                "task",
                task.id,
                {"source_note_id": note_id, "transition": "completed_from_note"},
            )
        return len(matched)

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


def _is_completion_note(raw_text: str) -> bool:
    normalized = _normalize_text(raw_text)
    signals = (
        "da lam xong",
        "lam xong",
        "da xong",
        "hoan thanh",
        "da hoan thanh",
        "done",
        "finished",
        "completed",
        "complete",
    )
    return bool(re.search(r"\bda\s+.+\s+xong\b", normalized)) or any(
        signal in normalized for signal in signals
    )


def _is_memory_correction(raw_text: str) -> bool:
    normalized = _normalize_text(raw_text)
    signals = (
        "sua lai",
        "sua la",
        "doi ten",
        "doi thanh",
        "cap nhat lai",
        "cap nhat thong tin",
        "dinh chinh",
        "khong phai",
        "that ra",
        "correction",
        "correct that",
        "actually",
    )
    return any(signal in normalized for signal in signals)


def _memory_delete_query(raw_text: str) -> str | None:
    normalized = _normalize_text(raw_text)
    signals = (
        "xoa memory",
        "xoa thong tin memory",
        "xoa thong tin",
        "xoa memory lien quan",
        "xoa thong tin lien quan",
        "quen thong tin",
        "dung nho",
        "forget memory",
        "forget that",
        "delete memory",
    )
    if not any(signal in normalized for signal in signals):
        return None
    cleanup_words = (
        "xoa",
        "thong tin",
        "lien quan den",
        "lien quan",
        "memory",
        "quen",
        "dung nho",
        "forget",
        "that",
        "delete",
    )
    query = normalized
    for word in cleanup_words:
        query = query.replace(word, " ")
    return " ".join(query.split()) or raw_text


def _is_state_query(raw_text: str) -> bool:
    normalized = _normalize_text(raw_text)
    query_signals = (
        "toi da luu gi",
        "da luu gi",
        "luu gi ve ban than",
        "memory cua toi",
        "co memory gi",
        "hom nay toi can lam gi",
        "hom nay con can lam gi",
        "toi can lam gi hom nay",
    )
    return any(signal in normalized for signal in query_signals)


def _state_query_summary(raw_text: str) -> str:
    normalized = _normalize_text(raw_text)
    if "luu gi" in normalized or "memory" in normalized:
        return "This looks like a memory query. Use /memory for the V1 memory view."
    if "hom nay" in normalized or "today" in normalized:
        return "This looks like a today query. Use /today for the V1 agenda view."
    return "This looks like a question, so I saved the raw note without extracting durable objects."


def _matches_task(raw_text: str, title: str) -> bool:
    raw_tokens = _meaningful_tokens(raw_text)
    title_tokens = _meaningful_tokens(title)
    if not raw_tokens or not title_tokens:
        return False
    overlap = raw_tokens & title_tokens
    return len(overlap) >= 3 and len(overlap) / len(title_tokens) >= 0.55


def _meaningful_tokens(value: str) -> set[str]:
    stopwords = {
        "toi",
        "da",
        "dang",
        "can",
        "lam",
        "xong",
        "hoan",
        "thanh",
        "project",
        "du",
        "an",
        "the",
        "a",
        "an",
        "and",
        "done",
        "finished",
        "completed",
        "complete",
    }
    tokens = set(_normalize_text(value).split())
    meaningful = {token for token in tokens if token not in stopwords and len(token) > 1}
    return meaningful or tokens


def _normalize_text(value: str) -> str:
    lowered = value.lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    ascii_text = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return "".join(char if char.isalnum() else " " for char in ascii_text)


def _parse_recurring_reminder(raw_text: str) -> tuple[str, datetime, str] | None:
    normalized = _normalize_text(raw_text)
    if not any(signal in normalized for signal in ("nhac toi", "remind me")):
        return None
    recurrence_rule: str | None = None
    weekday: int | None = None
    if any(signal in normalized for signal in ("moi ngay", "hang ngay", "every day", "daily")):
        recurrence_rule = "daily"
    else:
        weekdays = {
            "thu 2": 0,
            "thu hai": 0,
            "monday": 0,
            "thu 3": 1,
            "thu ba": 1,
            "tuesday": 1,
            "thu 4": 2,
            "thu tu": 2,
            "wednesday": 2,
            "thu 5": 3,
            "thu nam": 3,
            "thursday": 3,
            "thu 6": 4,
            "thu sau": 4,
            "friday": 4,
            "thu 7": 5,
            "thu bay": 5,
            "saturday": 5,
            "chu nhat": 6,
            "sunday": 6,
        }
        if any(signal in normalized for signal in ("moi tuan", "hang tuan", "weekly", "every week")):
            weekday = next((day for label, day in weekdays.items() if label in normalized), None)
            recurrence_rule = f"weekly:{weekday if weekday is not None else 0}"
        else:
            for label, day in weekdays.items():
                if f"moi {label}" in normalized or f"hang {label}" in normalized or f"every {label}" in normalized:
                    weekday = day
                    recurrence_rule = f"weekly:{day}"
                    break
    if recurrence_rule is None:
        return None
    clock = _parse_recurring_clock(normalized)
    if clock is None:
        return None
    now = datetime.now().astimezone()
    target_date = now.date()
    if recurrence_rule.startswith("weekly"):
        target_weekday = weekday if weekday is not None else int(recurrence_rule.split(":", 1)[1])
        days_ahead = (target_weekday - now.weekday()) % 7
        target_date = now.date() + timedelta(days=days_ahead)
    remind_at = datetime.combine(target_date, clock, tzinfo=now.tzinfo).astimezone(UTC)
    if remind_at <= datetime.now(UTC):
        remind_at += timedelta(days=7 if recurrence_rule.startswith("weekly") else 1)
    return (_recurring_title(raw_text), remind_at, recurrence_rule)


def _parse_recurring_clock(normalized: str) -> time | None:
    match = re.search(r"\b(\d{1,2})(?:h|:)(\d{2})?\b", normalized)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return time(hour, minute)
    match = re.search(r"\b(\d{1,2})\s*(am|pm)\b", normalized)
    if match:
        hour = int(match.group(1))
        if match.group(2) == "pm" and hour < 12:
            hour += 12
        if match.group(2) == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23:
            return time(hour, 0)
    return None


def _recurring_title(raw_text: str) -> str:
    title = re.sub(r"(?i)\b(nhắc tôi|nhac toi|remind me)\b", " ", raw_text)
    title = re.sub(r"(?i)\b(mỗi ngày|moi ngay|hằng ngày|hang ngay|every day|daily)\b", " ", title)
    title = re.sub(r"(?i)\b(mỗi tuần|moi tuan|hằng tuần|hang tuan|weekly|every week)\b", " ", title)
    title = re.sub(r"(?i)\b(mỗi|moi|hằng|hang|every)\s+(thứ\s+\d|thu\s+\d|thứ hai|thu hai|thứ ba|thu ba|thứ tư|thu tu|thứ năm|thu nam|thứ sáu|thu sau|thứ bảy|thu bay|chủ nhật|chu nhat|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", " ", title)
    title = re.sub(r"\b\d{1,2}(?:h|:)\d{0,2}\b", " ", title)
    title = re.sub(r"\b\d{1,2}\s*(?:am|pm)\b", " ", title, flags=re.IGNORECASE)
    title = " ".join(title.split())
    return title or "Recurring reminder"
