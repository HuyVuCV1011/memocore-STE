from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
import re
import unicodedata

from memocore.adapters.llm.base import ExtractionError
from memocore.adapters.storage.repositories import (
    CommitmentRepository,
    FollowUpRepository,
    MeetingRepository,
    NoteRepository,
    PersonRepository,
    ProjectRepository,
    TaskRepository,
    normalize_lookup,
    parse_model_datetime,
)
from memocore.domain.models import (
    Commitment,
    CommitmentDirection,
    EventType,
    FollowUp,
    Meeting,
    Note,
    NoteStatus,
    Person,
    Reminder,
    Task,
    TaskStatus,
)
from memocore.domain.schemas import (
    CaptureRequest,
    CaptureResponse,
    MemoryCandidate,
    NoteExtraction,
    ReminderCandidate,
    TaskCandidate,
)
from memocore.services.event_service import EventService
from memocore.services.clarification_service import (
    ClarificationService,
    parse_clarification_datetime,
)
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
        person_repo: PersonRepository | None = None,
        meeting_repo: MeetingRepository | None = None,
        followup_repo: FollowUpRepository | None = None,
        commitment_repo: CommitmentRepository | None = None,
    ):
        self.note_repo = note_repo
        self.task_repo = task_repo
        self.project_repo = project_repo
        self.extraction_service = extraction_service
        self.memory_service = memory_service
        self.reminder_service = reminder_service
        self.event_service = event_service
        self.clarification_service = clarification_service
        self.person_repo = person_repo
        self.meeting_repo = meeting_repo
        self.followup_repo = followup_repo
        self.commitment_repo = commitment_repo

    async def capture(self, request: CaptureRequest) -> CaptureResponse:
        existing = await self.note_repo.find_by_source_message(
            request.source, request.source_chat_id, request.source_message_id
        )
        if existing and existing.status != NoteStatus.FAILED:
            tasks = await self.task_repo.list_by_note(existing.id)
            reminders = await self.reminder_service.reminder_repo.list_by_note(existing.id)
            memories = await self.memory_service.memory_repo.list_by_note(existing.id)
            meetings = await self.meeting_repo.list_by_note(existing.id) if self.meeting_repo else []
            followups = await self.followup_repo.list_by_note(existing.id) if self.followup_repo else []
            commitments = (
                await self.commitment_repo.list_by_note(existing.id) if self.commitment_repo else []
            )
            return CaptureResponse(
                note_id=existing.id,
                summary=existing.summary or "Already captured.",
                tasks_created=len(tasks),
                reminders_created=len(reminders),
                memories_created=len(memories),
                meetings_created=len(meetings),
                followups_created=len(followups),
                commitments_created=len(commitments),
                duplicate=True,
            )

        if existing is None:
            note = Note(
                source=request.source,
                source_message_id=request.source_message_id,
                source_chat_id=request.source_chat_id,
                raw_text=request.raw_text,
            )
            await self.note_repo.create(note)
            await self.event_service.append_event(EventType.NOTE_CAPTURED, "note", note.id)
        else:
            note = existing

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
            # Rescue deterministic/explicit routing notes
            action_tag = _trailing_action_tag(request.raw_text)
            command = _capture_command(request.raw_text)
            is_deterministic = action_tag is not None or command is not None
            if is_deterministic:
                fallback_tags = []
                if action_tag in {"li", "linkedin"} or command in {"li", "linkedin"}:
                    fallback_tags.extend(["li", "linkedin"])
                if action_tag in {"task", "t"} or command in {"task", "t"}:
                    fallback_tags.append("task")
                if action_tag in {"remind", "r"}:
                    fallback_tags.append("reminder")
                if action_tag in {"mem", "m"} or command in {"mem", "m"}:
                    fallback_tags.append("memory")

                clean_summary = request.raw_text
                for cmd in ("/li", "/linkedin", "/task", "/t", "/mem", "/m"):
                    if clean_summary.lower().startswith(cmd):
                        parts = clean_summary.split(maxsplit=1)
                        if len(parts) > 1:
                            clean_summary = parts[1]
                        break

                extraction = NoteExtraction(
                    summary=clean_summary,
                    tags=fallback_tags,
                )
            else:
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

        # Force tag presence if explicit tags are found in raw text or command
        action_tag = _trailing_action_tag(request.raw_text)
        command = _capture_command(request.raw_text)

        # Check LinkedIn tags
        if action_tag in {"li", "linkedin"} or command in {"li", "linkedin"}:
            for tag in ("li", "linkedin"):
                if tag not in extraction.tags:
                    extraction.tags.append(tag)

        # Check Task tags
        if action_tag in {"task", "t"} or command in {"task", "t"}:
            if "task" not in extraction.tags:
                extraction.tags.append("task")

        # Check Reminder tags
        if action_tag in {"remind", "r"} and "reminder" not in extraction.tags:
            extraction.tags.append("reminder")

        # Check Memory tags
        if action_tag in {"mem", "m"} or command in {"mem", "m"}:
            if "memory" not in extraction.tags:
                extraction.tags.append("memory")

        # Force candidate injection if explicitly routed as task/memory but none extracted
        is_task_intent = action_tag in {"task", "t"} or command in {"task", "t"}
        if is_task_intent and not extraction.tasks:
            clean_title = _clean_capture_text(request.raw_text)
            extraction.tasks.append(
                TaskCandidate(
                    title=clean_title,
                    priority="medium",
                    confidence=1.0
                )
            )

        is_reminder_intent = action_tag in {"remind", "r"}
        if is_reminder_intent and not extraction.reminders:
            clean_title = _clean_capture_text(request.raw_text)
            remind_at = parse_clarification_datetime(clean_title)
            extraction.reminders.append(
                ReminderCandidate(
                    title=clean_title,
                    remind_at=remind_at.isoformat() if remind_at else None,
                    confidence=1.0,
                )
            )

        is_memory_intent = action_tag in {"mem", "m"} or command in {"mem", "m"}
        if is_memory_intent and not extraction.memories:
            clean_content = _clean_capture_text(request.raw_text)
            from memocore.domain.models import MemoryBucket, MemoryKind
            extraction.memories.append(
                MemoryCandidate(
                    bucket=MemoryBucket.FACT,
                    kind=MemoryKind.FACT,
                    content=clean_content,
                    confidence=1.0
                )
            )

        duplicate_suggestions: list[str] = []
        if {"li", "linkedin"} & set(extraction.tags):
            for existing_note in await self.note_repo.list_recent():
                if existing_note.id == note.id or not {"li", "linkedin"} & set(existing_note.tags):
                    continue
                if _text_similarity(request.raw_text, existing_note.raw_text) >= 0.55:
                    duplicate_suggestions.append(
                        f"Ý tưởng này gần với note LinkedIn đã có: “{existing_note.summary or existing_note.raw_text}”. "
                        "Mình chưa tự gộp."
                    )
                    await self.event_service.append_event(
                        EventType.MEMORY_DUPLICATE_SUGGESTED,
                        "note",
                        existing_note.id,
                        {
                            "source_note_id": note.id,
                            "candidate_content": request.raw_text,
                        },
                    )
                    break

        try:
            async with self.note_repo.database.transaction():
                explicit_projects = [
                    hint
                    for hint in extraction.projects
                    if _explicit_mention(hint.name, request.raw_text)
                ]
                project_id: str | None = None
                projects_by_name: dict[str, str] = {}
                entity_suggestion_ids: list[str] = []
                known_projects = await self.project_repo.list_all()
                for hint in explicit_projects:
                    project = _similar_named_entity(hint.name, known_projects, "name", "aliases")
                    if project is not None and normalize_lookup(hint.name) not in {
                        normalize_lookup(project.name),
                        *(normalize_lookup(alias) for alias in project.aliases),
                    }:
                        suggestion = await self.event_service.append_event(
                            EventType.ENTITY_ALIAS_SUGGESTED,
                            "project",
                            project.id,
                            {
                                "alias": hint.name,
                                "canonical_name": project.name,
                                "source_note_id": note.id,
                            },
                        )
                        entity_suggestion_ids.append(suggestion.id)
                    else:
                        project = await self.project_repo.find_or_create(hint.name)
                    projects_by_name[normalize_lookup(hint.name)] = project.id
                    await self.event_service.append_event(
                        EventType.PROJECT_SEEN,
                        "project",
                        project.id,
                        {"source_note_id": note.id, "confidence": hint.confidence},
                    )
                    if len(explicit_projects) == 1:
                        project_id = project.id

                people_by_name, people_created, person_suggestions = await self._resolve_people(
                    extraction, request.raw_text, note.id
                )
                entity_suggestion_ids.extend(person_suggestions)

                tasks_created = 0
                is_memory_correction = _is_memory_correction(request.raw_text)
                if not is_memory_correction:
                    for candidate in extraction.tasks:
                        task = Task(
                            title=candidate.title,
                            description=candidate.description,
                            priority=candidate.priority,
                            due_at=parse_model_datetime(candidate.due_at),
                            project_id=self._resolve_project_id(
                                candidate.project_name, projects_by_name, project_id
                            ),
                            person_id=self._resolve_person_id(
                                candidate.person_name, people_by_name
                            ),
                            source_note_id=note.id,
                            confidence=candidate.confidence,
                        )
                        created_task = await self.task_repo.create(task)
                        await self.event_service.append_event(
                            EventType.TASK_CANDIDATE_CREATED,
                            "task",
                            created_task.id,
                            {"source_note_id": note.id},
                        )
                        tasks_created += 1

                tasks_completed = await self._complete_matching_tasks(note.id, request.raw_text)

                reminders = await self.reminder_service.persist_candidates(
                    extraction.reminders, note.id
                )
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
                    for candidate in extraction.memories:
                        similar = await self.memory_service.find_similar(candidate)
                        for item in similar:
                            suggestion = (
                                f"“{candidate.content}” gần với memory đã có: “{item.content}”. "
                                "Mình chưa tự gộp."
                            )
                            duplicate_suggestions.append(suggestion)
                            await self.event_service.append_event(
                                EventType.MEMORY_DUPLICATE_SUGGESTED,
                                "memory_item",
                                item.id,
                                {
                                    "source_note_id": note.id,
                                    "candidate_content": candidate.content,
                                },
                            )
                        memories.extend(
                            await self.memory_service.persist_candidates(
                                [candidate],
                                note.id,
                                project_id=self._resolve_project_id(
                                    candidate.project_name, projects_by_name, project_id
                                ),
                                person_id=self._resolve_person_id(
                                    candidate.person_name, people_by_name
                                ),
                                supersede_related=is_memory_correction,
                            )
                        )
                v4_counts, v4_warnings = await self._persist_v4_entities(
                    extraction, note.id, projects_by_name, people_by_name
                )
                await self.note_repo.update_processed(note.id, extraction.summary, extraction.tags)
                await self._record_quality_warnings(
                    note.id, request.raw_text, extraction, v4_warnings
                )
                await self.event_service.append_event(
                    EventType.NOTE_PROCESSED,
                    "note",
                    note.id,
                    {
                        "tasks_created": tasks_created,
                        "tasks_completed": tasks_completed,
                        "reminders_created": len(reminders),
                        "memories_created": len(memories),
                        **v4_counts,
                    },
                )
        except Exception as exc:
            await self.note_repo.update_status(note.id, NoteStatus.FAILED)
            await self.event_service.append_event(
                EventType.NOTE_FAILED,
                "note",
                note.id,
                {"error": type(exc).__name__},
            )
            raise

        return CaptureResponse(
            note_id=note.id,
            summary=extraction.summary,
            tasks_created=tasks_created,
            tasks_completed=tasks_completed,
            reminders_created=len(reminders),
            memories_created=len(memories),
            people_created=people_created,
            meetings_created=v4_counts["meetings_created"],
            followups_created=v4_counts["followups_created"],
            commitments_created=v4_counts["commitments_created"],
            clarification_question=clarification_question,
            duplicate_suggestions=duplicate_suggestions,
            entity_suggestion_ids=entity_suggestion_ids,
        )

    async def _resolve_people(
        self, extraction, raw_text: str, note_id: str
    ) -> tuple[dict[str, str], int, list[str]]:
        people_by_name: dict[str, str] = {}
        if self.person_repo is None:
            return people_by_name, 0, []
        created_count = 0
        suggestion_ids: list[str] = []
        existing_people_by_name: dict[str, Person] = {}
        for person in await self.person_repo.list_all():
            for name in [person.display_name, *person.aliases]:
                if name:
                    existing_people_by_name[normalize_lookup(name)] = person
                if _explicit_mention(name, raw_text):
                    people_by_name[normalize_lookup(name)] = person.id
        for candidate in extraction.people:
            if (
                candidate.confidence < _MIN_V4_CONFIDENCE
                or not _safe_person_candidate(candidate.display_name, raw_text)
            ):
                continue
            existing = existing_people_by_name.get(normalize_lookup(candidate.display_name))
            person = existing or await self.person_repo.create(
                Person(
                    display_name=candidate.display_name,
                    aliases=[alias for alias in candidate.aliases if _safe_alias(alias)],
                    relationship=candidate.relationship,
                    notes=candidate.notes,
                )
            )
            if existing is None:
                created_count += 1
                await self.event_service.append_event(
                    EventType.PERSON_CREATED,
                    "person",
                    person.id,
                    {"source_note_id": note_id, "confidence": candidate.confidence},
                )
            safe_candidate_aliases = [alias for alias in candidate.aliases if _safe_alias(alias)]
            if existing is not None:
                new_aliases = [
                    alias
                    for alias in safe_candidate_aliases
                    if normalize_lookup(alias)
                    not in {
                        normalize_lookup(person.display_name),
                        *(normalize_lookup(value) for value in person.aliases),
                    }
                ]
                for alias in new_aliases:
                    suggestion = await self.event_service.append_event(
                        EventType.ENTITY_ALIAS_SUGGESTED,
                        "person",
                        person.id,
                        {
                            "alias": alias,
                            "canonical_name": person.display_name,
                            "source_note_id": note_id,
                        },
                    )
                    suggestion_ids.append(suggestion.id)
            for name in [
                person.display_name,
                *person.aliases,
                candidate.display_name,
                *safe_candidate_aliases,
            ]:
                if name:
                    existing_people_by_name[normalize_lookup(name)] = person
                if name and _explicit_mention(name, raw_text):
                    people_by_name[normalize_lookup(name)] = person.id
        return people_by_name, created_count, suggestion_ids

    def _resolve_project_id(
        self, name: str | None, projects_by_name: dict[str, str], default_project_id: str | None
    ) -> str | None:
        if name:
            return projects_by_name.get(normalize_lookup(name))
        return default_project_id

    def _resolve_person_id(self, name: str | None, people_by_name: dict[str, str]) -> str | None:
        return people_by_name.get(normalize_lookup(name)) if name else None

    async def _persist_v4_entities(
        self,
        extraction,
        note_id: str,
        projects_by_name: dict[str, str],
        people_by_name: dict[str, str],
    ) -> tuple[dict[str, int], list[str]]:
        counts = {
            "meetings_created": 0,
            "followups_created": 0,
            "commitments_created": 0,
        }
        warnings: list[str] = []
        if self.meeting_repo is not None:
            for candidate in extraction.meetings:
                if candidate.confidence < _MIN_V4_CONFIDENCE:
                    warnings.append("low_confidence_meeting_skipped")
                    continue
                person_ids = [
                    person_id for name in candidate.person_names
                    if (person_id := self._resolve_person_id(name, people_by_name)) is not None
                ]
                if (
                    (candidate.person_names and len(person_ids) != len(candidate.person_names))
                    or (
                        candidate.project_name
                        and self._resolve_project_id(
                            candidate.project_name, projects_by_name, None
                        )
                        is None
                    )
                ):
                    warnings.append("unresolved_meeting_link_skipped")
                    continue
                meeting = await self.meeting_repo.create(Meeting(
                    title=candidate.title,
                    starts_at=parse_model_datetime(candidate.starts_at),
                    ends_at=parse_model_datetime(candidate.ends_at),
                    project_id=self._resolve_project_id(candidate.project_name, projects_by_name, None),
                    person_id=person_ids[0] if len(person_ids) == 1 else None,
                    source_note_id=note_id,
                    notes=candidate.notes,
                ))
                for person_id in person_ids:
                    await self.meeting_repo.add_person(meeting.id, person_id)
                await self.event_service.append_event(
                    EventType.MEETING_CREATED, "meeting", meeting.id,
                    {"source_note_id": note_id, "confidence": candidate.confidence},
                )
                counts["meetings_created"] += 1
        if self.followup_repo is not None:
            for candidate in extraction.followups:
                person_id = self._resolve_person_id(candidate.person_name, people_by_name)
                project_id = self._resolve_project_id(
                    candidate.project_name, projects_by_name, None
                )
                if (
                    candidate.confidence < _MIN_V4_CONFIDENCE
                    or person_id is None
                    or (candidate.project_name and project_id is None)
                ):
                    warnings.append("ambiguous_followup_skipped")
                    continue
                followup = await self.followup_repo.create(FollowUp(
                    title=candidate.title,
                    due_at=parse_model_datetime(candidate.due_at),
                    person_id=person_id,
                    project_id=project_id,
                    source_note_id=note_id,
                    notes=candidate.notes,
                ))
                await self.event_service.append_event(
                    EventType.FOLLOWUP_CREATED, "followup", followup.id,
                    {"source_note_id": note_id, "confidence": candidate.confidence},
                )
                counts["followups_created"] += 1
        if self.commitment_repo is not None:
            for candidate in extraction.commitments:
                person_id = self._resolve_person_id(candidate.person_name, people_by_name)
                project_id = self._resolve_project_id(
                    candidate.project_name, projects_by_name, None
                )
                if (
                    candidate.confidence < _MIN_V4_CONFIDENCE
                    or candidate.direction is None
                    or person_id is None
                    or (candidate.project_name and project_id is None)
                ):
                    warnings.append("ambiguous_commitment_skipped")
                    continue
                commitment = await self.commitment_repo.create(Commitment(
                    title=candidate.title,
                    direction=CommitmentDirection(candidate.direction),
                    due_at=parse_model_datetime(candidate.due_at),
                    person_id=person_id,
                    project_id=project_id,
                    source_note_id=note_id,
                    notes=candidate.notes,
                ))
                await self.event_service.append_event(
                    EventType.COMMITMENT_CREATED, "commitment", commitment.id,
                    {"source_note_id": note_id, "confidence": candidate.confidence},
                )
                counts["commitments_created"] += 1
        return counts, warnings

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

    async def _record_quality_warnings(
        self,
        note_id: str,
        raw_text: str,
        extraction,
        additional_warnings: list[str] | None = None,
    ) -> None:
        lowered = raw_text.lower()
        warnings = list(additional_warnings or [])
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


def _explicit_mention(name: str, raw_text: str) -> bool:
    normalized_name = normalize_lookup(name)
    normalized_text = normalize_lookup(raw_text)
    if not normalized_name or not normalized_text:
        return False
    return re.search(rf"(?<!\w){re.escape(normalized_name)}(?!\w)", normalized_text) is not None


def _safe_alias(alias: str) -> bool:
    normalized = normalize_lookup(alias)
    return bool(normalized) and normalized not in _VAGUE_PERSON_TERMS


def _safe_person_candidate(name: str, raw_text: str) -> bool:
    normalized = normalize_lookup(name)
    if normalized in _VAGUE_PERSON_TERMS:
        return False
    if len(normalized) < 2:
        return False
    return _explicit_mention(name, raw_text)


_VAGUE_PERSON_TERMS = {
    "ai do",
    "anyone",
    "ban",
    "boss",
    "client",
    "customer",
    "doi tac",
    "khach hang",
    "manager",
    "nguoi do",
    "someone",
    "team",
    "teammate",
}

_MIN_V4_CONFIDENCE = 0.7


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


def _trailing_action_tag(text: str) -> str | None:
    match = re.search(
        r"(?:^|\s)#(linkedin|li|task|t|remind|r|mem|m)\s*[.,!?;:]*\s*$",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1).lower() if match else None


def _capture_command(text: str) -> str | None:
    match = re.match(r"^/(linkedin|li|task|t|mem|m)(?:@\w+)?(?:\s|$)", text, flags=re.IGNORECASE)
    return match.group(1).lower() if match else None


def _clean_capture_text(text: str) -> str:
    cleaned = re.sub(
        r"^/(?:linkedin|li|task|t|mem|m)(?:@\w+)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s*#(?:linkedin|li|task|t|remind|r|mem|m)\s*[.,!?;:]*\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def _similar_named_entity(name: str, entities: list, name_field: str, aliases_field: str):
    normalized = normalize_lookup(name)
    tokens = set(normalized.split())
    if len(tokens) < 2:
        return None
    best = None
    best_score = 0.0
    for entity in entities:
        values = [getattr(entity, name_field), *getattr(entity, aliases_field)]
        for value in values:
            candidate_tokens = set(normalize_lookup(value).split())
            if not candidate_tokens:
                continue
            score = len(tokens & candidate_tokens) / len(tokens | candidate_tokens)
            if score > best_score:
                best = entity
                best_score = score
    return best if best_score >= 0.75 else None


def _text_similarity(left: str, right: str) -> float:
    left_tokens = set(normalize_lookup(_clean_capture_text(left)).split())
    right_tokens = set(normalize_lookup(_clean_capture_text(right)).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
