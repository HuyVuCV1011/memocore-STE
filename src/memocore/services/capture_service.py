from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
import re
import unicodedata
from typing import TYPE_CHECKING

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
from memocore.adapters.storage.knowledge_repositories import (
    DecisionRepository,
    KnowledgeRelationRepository,
    OrganizationRepository,
)
from memocore.domain.knowledge import (
    Decision,
    DecisionStatus,
    KnowledgeEntityType,
    KnowledgeRelation,
)
from memocore.domain.models import (
    Commitment,
    CommitmentDirection,
    EventType,
    FollowUp,
    Meeting,
    MemoryBucket,
    MemoryKind,
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
from memocore.services.schedule_semantics import (
    is_future_schedule_request as _is_future_schedule_request,
    normalize_scheduled_work as _normalize_scheduled_work,
)

if TYPE_CHECKING:
    from memocore.services.activity_reconciliation_service import (
        ActivityReconciliationService,
    )


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
        organization_repo: OrganizationRepository | None = None,
        decision_repo: DecisionRepository | None = None,
        knowledge_relation_repo: KnowledgeRelationRepository | None = None,
        activity_reconciliation_service: "ActivityReconciliationService | None" = None,
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
        self.organization_repo = organization_repo
        self.decision_repo = decision_repo
        self.knowledge_relation_repo = knowledge_relation_repo
        self.activity_reconciliation_service = activity_reconciliation_service

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

        extraction = _normalize_scheduled_work(
            extraction,
            request.raw_text,
            datetime.now().astimezone(),
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
                        "Em chưa tự gộp."
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
                organizations_by_name, organizations_created = await self._resolve_organizations(
                    extraction, request.raw_text
                )

                tasks_created = 0
                is_memory_correction = _is_memory_correction(request.raw_text)
                if not is_memory_correction:
                    known_tasks = await self.task_repo.list_active()
                    for candidate in extraction.tasks:
                        duplicate = _find_duplicate_task(candidate.title, known_tasks)
                        if duplicate is not None:
                            duplicate_suggestions.append(
                                f"Task “{candidate.title}” gần trùng với task đang mở "
                                f"“{duplicate.title}”, nên em không tạo thêm."
                            )
                            continue
                        task = Task(
                            title=candidate.title,
                            description=candidate.description,
                            priority=candidate.priority,
                            due_at=parse_model_datetime(candidate.due_at),
                            project_id=self._resolve_candidate_project_id(
                                candidate.project_name,
                                candidate.title,
                                projects_by_name,
                                project_id,
                            ),
                            person_id=self._resolve_person_id(
                                candidate.person_name, people_by_name
                            ),
                            source_note_id=note.id,
                            confidence=candidate.confidence,
                            recurrence_rule=candidate.recurrence_rule,
                            duration_minutes=candidate.duration_minutes,
                        )
                        if task.recurrence_rule:
                            task.recurrence_series_id = task.id
                            task.recurrence_occurrence_at = task.due_at
                        created_task = await self.task_repo.create(task)
                        known_tasks.append(created_task)
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
                        if not _should_persist_memory(
                            candidate,
                            extraction.tasks,
                            extraction.meetings,
                            explicit_memory_intent=is_memory_intent,
                        ):
                            continue
                        similar = await self.memory_service.find_similar(candidate)
                        for item in similar:
                            suggestion = (
                                f"“{candidate.content}” gần với memory đã có: “{item.content}”. "
                                "Em chưa tự gộp."
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
                                project_id=self._resolve_candidate_project_id(
                                    candidate.project_name,
                                    candidate.content,
                                    projects_by_name,
                                    project_id,
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
                if self.activity_reconciliation_service is not None:
                    await self.activity_reconciliation_service.link_note_artifacts(note.id)
                decisions_created = await self._persist_decisions(
                    extraction,
                    note.id,
                    projects_by_name,
                    people_by_name,
                    organizations_by_name,
                )
                relationships_created = await self._persist_relationships(
                    extraction,
                    note.id,
                    projects_by_name,
                    people_by_name,
                    organizations_by_name,
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
                        "organizations_created": organizations_created,
                        "decisions_created": decisions_created,
                        "relationships_created": relationships_created,
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
            organizations_created=organizations_created,
            decisions_created=decisions_created,
            relationships_created=relationships_created,
            meetings_created=v4_counts["meetings_created"],
            followups_created=v4_counts["followups_created"],
            commitments_created=v4_counts["commitments_created"],
            clarification_question=clarification_question,
            duplicate_suggestions=duplicate_suggestions,
            entity_suggestion_ids=entity_suggestion_ids,
        )

    async def _resolve_organizations(
        self, extraction, raw_text: str
    ) -> tuple[dict[str, str], int]:
        if self.organization_repo is None:
            return {}, 0
        resolved: dict[str, str] = {}
        created = 0
        existing = await self.organization_repo.list_all()
        existing_names = {
            normalize_lookup(name): item
            for item in existing
            for name in (item.name, *item.aliases)
        }
        for candidate in extraction.organizations:
            if candidate.confidence < _MIN_V4_CONFIDENCE or not _explicit_mention(
                candidate.name, raw_text
            ):
                continue
            organization = existing_names.get(normalize_lookup(candidate.name))
            if organization is None:
                organization = await self.organization_repo.find_or_create(candidate.name)
                created += 1
            resolved[normalize_lookup(candidate.name)] = organization.id
        return resolved, created

    async def _persist_decisions(
        self,
        extraction,
        note_id: str,
        projects_by_name: dict[str, str],
        people_by_name: dict[str, str],
        organizations_by_name: dict[str, str],
    ) -> int:
        if self.decision_repo is None:
            return 0
        created = 0
        for candidate in extraction.decisions:
            if candidate.confidence < _MIN_V4_CONFIDENCE:
                continue
            project_id = (
                projects_by_name.get(normalize_lookup(candidate.project_name))
                if candidate.project_name
                else None
            )
            person_id = (
                people_by_name.get(normalize_lookup(candidate.person_name))
                if candidate.person_name
                else None
            )
            organization_id = (
                organizations_by_name.get(normalize_lookup(candidate.organization_name))
                if candidate.organization_name
                else None
            )
            superseded = (
                await self.decision_repo.find_current_by_title(candidate.supersedes_title)
                if candidate.supersedes_title
                else None
            )
            decision = await self.decision_repo.create(
                Decision(
                    title=candidate.title,
                    summary=candidate.summary,
                    status=DecisionStatus(candidate.status),
                    project_id=project_id,
                    person_id=person_id,
                    organization_id=organization_id,
                    source_note_id=note_id,
                    confidence=candidate.confidence,
                    supersedes_decision_id=superseded.id if superseded else None,
                )
            )
            if superseded is not None:
                await self.decision_repo.supersede(superseded.id, decision.id)
                await self.event_service.append_event(
                    EventType.DECISION_SUPERSEDED,
                    "decision",
                    superseded.id,
                    {"replacement_decision_id": decision.id, "source_note_id": note_id},
                )
            created += 1
        return created

    async def _persist_relationships(
        self,
        extraction,
        note_id: str,
        projects_by_name: dict[str, str],
        people_by_name: dict[str, str],
        organizations_by_name: dict[str, str],
    ) -> int:
        if self.knowledge_relation_repo is None:
            return 0
        entity_maps = {
            "project": projects_by_name,
            "person": people_by_name,
            "organization": organizations_by_name,
        }
        created = 0
        for candidate in extraction.relationships:
            if candidate.confidence < _MIN_V4_CONFIDENCE:
                continue
            source_id = entity_maps[candidate.source_type].get(
                normalize_lookup(candidate.source_name)
            )
            target_id = entity_maps[candidate.target_type].get(
                normalize_lookup(candidate.target_name)
            )
            if not source_id or not target_id or source_id == target_id:
                continue
            relation = await self.knowledge_relation_repo.create(
                KnowledgeRelation(
                    source_type=KnowledgeEntityType(candidate.source_type),
                    source_id=source_id,
                    target_type=KnowledgeEntityType(candidate.target_type),
                    target_id=target_id,
                    relation_type=candidate.relation_type.strip().lower(),
                    source_note_id=note_id,
                    confidence=candidate.confidence,
                )
            )
            await self.event_service.append_event(
                EventType.KNOWLEDGE_RELATION_CREATED,
                "knowledge_relation",
                relation.id,
                {"source_note_id": note_id},
            )
            created += 1
        return created

    async def capture_scoped_knowledge(
        self,
        request: CaptureRequest,
        *,
        entity_type: str,
        entity_id: str,
        entity_name: str,
        statements: list[str],
    ) -> CaptureResponse:
        existing = await self.note_repo.find_by_source_message(
            request.source, request.source_chat_id, request.source_message_id
        )
        if existing and existing.status != NoteStatus.FAILED:
            memories = await self.memory_service.memory_repo.list_by_note(existing.id)
            return CaptureResponse(
                note_id=existing.id,
                summary=existing.summary or f"Đã cập nhật knowledge cho {entity_name}.",
                memories_created=len(memories),
                duplicate=True,
            )

        note = existing or Note(
            source=request.source,
            source_message_id=request.source_message_id,
            source_chat_id=request.source_chat_id,
            raw_text=request.raw_text,
            metadata={
                "target_entity_type": entity_type,
                "target_entity_id": entity_id,
                "target_entity_name": entity_name,
            },
        )
        if existing is None:
            await self.note_repo.create(note)
            await self.event_service.append_event(
                EventType.NOTE_CAPTURED, "note", note.id
            )

        candidates = [
            MemoryCandidate(
                bucket=(
                    MemoryBucket.PROJECT
                    if entity_type == "project"
                    else MemoryBucket.PROFILE
                ),
                kind=_scoped_memory_kind(statement),
                content=statement,
                project_name=entity_name if entity_type == "project" else None,
                person_name=entity_name if entity_type == "person" else None,
                confidence=1.0,
            )
            for statement in statements
            if statement.strip()
        ]
        if not candidates:
            summary = f"Chưa có nội dung để cập nhật cho {entity_name}."
            await self.note_repo.update_processed(
                note.id, summary, ["knowledge_update", entity_type, "empty"]
            )
            return CaptureResponse(note_id=note.id, summary=summary)

        created = []
        async with self.note_repo.database.transaction():
            for candidate in candidates:
                created.extend(
                    await self.memory_service.persist_candidates(
                        [candidate],
                        note.id,
                        project_id=entity_id if entity_type == "project" else None,
                        person_id=entity_id if entity_type == "person" else None,
                        organization_id=(
                            entity_id if entity_type == "organization" else None
                        ),
                    )
                )
            summary = f"Đã cập nhật {len(created)} thông tin cho {entity_name}."
            await self.note_repo.update_processed(
                note.id, summary, ["knowledge_update", entity_type]
            )
            await self.event_service.append_event(
                EventType.NOTE_PROCESSED,
                "note",
                note.id,
                {
                    "intent": "update_knowledge",
                    "target_entity_type": entity_type,
                    "target_entity_id": entity_id,
                    "memories_created": len(created),
                },
            )
        return CaptureResponse(
            note_id=note.id,
            summary=summary,
            memories_created=len(created),
        )

    async def rollback_recent_knowledge_update(
        self,
        request: CaptureRequest,
        *,
        requested_count: int | None = None,
    ) -> CaptureResponse:
        if not request.source_chat_id:
            return CaptureResponse(
                note_id="",
                summary="Em chưa xác định được cuộc hội thoại chứa cập nhật cần hoàn tác.",
            )
        recent_notes = await self.note_repo.list_recent_by_chat(
            request.source, request.source_chat_id, limit=20
        )
        source_note = next(
            (
                note
                for note in recent_notes
                if "knowledge_update" in note.tags and "rolled_back" not in note.tags
            ),
            None,
        )
        if source_note is None:
            return CaptureResponse(
                note_id="",
                summary="Em chưa tìm thấy batch knowledge nào gần đây để hoàn tác.",
            )
        memories = await self.memory_service.memory_repo.list_by_note(source_note.id)
        if not memories:
            return CaptureResponse(
                note_id=source_note.id,
                summary="Batch cập nhật gần nhất không còn memory nào để xóa.",
            )
        if requested_count and requested_count != len(memories):
            return CaptureResponse(
                note_id=source_note.id,
                summary=(
                    f"Batch gần nhất có {len(memories)} thông tin, không phải "
                    f"{requested_count}. Em chưa xóa để tránh nhầm dữ liệu."
                ),
            )

        async with self.note_repo.database.transaction():
            for item in memories:
                await self.memory_service.delete(item.id)
            await self.note_repo.update_processed(
                source_note.id,
                f"Đã hoàn tác {len(memories)} thông tin từ knowledge update.",
                [*source_note.tags, "rolled_back"],
            )
        return CaptureResponse(
            note_id=source_note.id,
            summary=f"Đã xóa {len(memories)} thông tin từ lần cập nhật gần nhất.",
            memories_deleted=len(memories),
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

    def _resolve_candidate_project_id(
        self,
        name: str | None,
        candidate_text: str,
        projects_by_name: dict[str, str],
        default_project_id: str | None,
    ) -> str | None:
        if name:
            return self._resolve_project_id(name, projects_by_name, None)
        if default_project_id is None:
            return None
        for project_name, project_id in projects_by_name.items():
            if project_id == default_project_id and _explicit_mention(project_name, candidate_text):
                return project_id
        return None

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
    if _is_future_schedule_request(normalized):
        return False
    return bool(
        re.search(r"\bda\s+.+\s+xong\b", normalized)
        or re.search(r"\b(?:toi\s+)?(?:da|vua)\s+hoan\s+thanh\b", normalized)
        or "da xong" in normalized
        or re.search(r"^hoan thanh\s+(?:cai|task|viec|so)\b", normalized)
        or re.search(r"\b(?:i\s+)?(?:have\s+)?(?:finished|completed)\b", normalized)
        or normalized in {"xong", "xong roi", "done"}
    )


def _scoped_memory_kind(statement: str) -> MemoryKind:
    normalized = _normalize_text(statement)
    if any(
        signal in normalized
        for signal in ("muc tieu", "huong toi", "trong tuong lai", "can tro thanh")
    ):
        return MemoryKind.GOAL
    if any(
        signal in normalized
        for signal in ("khong duoc", "khong can", "bat buoc", "uu tien", "ranh gioi")
    ):
        return MemoryKind.BOUNDARY
    return MemoryKind.PROJECT_STATE


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


def _find_duplicate_task(title: str, tasks: list[Task]) -> Task | None:
    candidate_tokens = _task_identity_tokens(title)
    if not candidate_tokens:
        return None
    for task in tasks:
        existing_tokens = _task_identity_tokens(task.title)
        if not existing_tokens:
            continue
        score = len(candidate_tokens & existing_tokens) / len(
            candidate_tokens | existing_tokens
        )
        if score >= 0.75:
            return task
    return None


def _task_identity_tokens(value: str) -> set[str]:
    ignored = {
        "tao",
        "thuc",
        "hien",
        "lam",
        "hoan",
        "thanh",
        "moi",
        "task",
        "viec",
        "can",
        "toi",
    }
    return {
        token
        for token in normalize_lookup(value).split()
        if token not in ignored and len(token) > 1
    }


def _should_persist_memory(
    candidate: MemoryCandidate,
    tasks: list[TaskCandidate],
    meetings: list,
    *,
    explicit_memory_intent: bool,
) -> bool:
    if explicit_memory_intent:
        return True
    # Ordinary durable facts and preferences may mention the same person/project as
    # operational records. Only suppress a goal that merely restates a task; dated
    # appointment restatements are removed earlier by _normalize_scheduled_work.
    if str(candidate.kind) != MemoryKind.GOAL.value:
        return True
    return not any(_matches_task(candidate.content, task.title) for task in tasks)
