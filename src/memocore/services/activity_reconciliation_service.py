from __future__ import annotations

from dataclasses import dataclass

from memocore.adapters.storage.repositories import (
    ActivityLinkRepository,
    MeetingRepository,
    PersonRepository,
    ProjectRepository,
    TaskRepository,
    normalize_lookup,
)
from memocore.domain.models import EventLog, EventType, Meeting, Task
from memocore.services.event_service import EventService


@dataclass(frozen=True)
class ActivityMutationResult:
    task: Task
    event_id: str
    linked_meetings_updated: int = 0


class ActivityReconciliationService:
    """Keeps projections of one real-world activity semantically aligned.

    A scheduled activity may be represented by both a task and a meeting.  The
    link is explicit so later mutations can update identity, entity links and
    undo snapshots atomically instead of relying on presentation-time guesses.
    """

    def __init__(
        self,
        task_repo: TaskRepository,
        meeting_repo: MeetingRepository,
        person_repo: PersonRepository,
        project_repo: ProjectRepository,
        activity_link_repo: ActivityLinkRepository,
        event_service: EventService,
    ):
        self.task_repo = task_repo
        self.meeting_repo = meeting_repo
        self.person_repo = person_repo
        self.project_repo = project_repo
        self.activity_link_repo = activity_link_repo
        self.event_service = event_service

    async def link_note_artifacts(self, note_id: str) -> int:
        tasks = await self.task_repo.list_by_note(note_id)
        meetings = await self.meeting_repo.list_by_note(note_id)
        if not tasks or not meetings:
            return 0

        linked = 0
        used_meeting_ids: set[str] = set()
        for task in tasks:
            candidates = [
                meeting
                for meeting in meetings
                if meeting.id not in used_meeting_ids
                and _same_activity_candidate(task, meeting, len(tasks), len(meetings))
            ]
            if not candidates:
                continue
            candidates.sort(key=lambda item: _activity_distance(task, item))
            meeting = candidates[0]
            await self.activity_link_repo.link(task.id, meeting.id)
            used_meeting_ids.add(meeting.id)
            linked += 1
        return linked

    async def rename_task(
        self,
        task_id: str,
        new_title: str,
        *,
        source_note_id: str | None = None,
        transition: str = "renamed_from_conversation",
    ) -> ActivityMutationResult | None:
        task = await self.task_repo.get_by_id(task_id)
        if task is None:
            return None

        await self.link_note_artifacts(task.source_note_id)
        meetings = await self._linked_meetings(task.id)
        person_id = await self._mentioned_person_id(new_title)
        project_id = await self._mentioned_project_id(new_title)

        # Preserve manually assigned links for standalone tasks.  When task and
        # meeting are two projections of one captured activity, a full title
        # replacement also replaces the old activity identity and stale links.
        next_person_id = person_id if meetings else (person_id or task.person_id)
        next_project_id = project_id if meetings else (project_id or task.project_id)
        before = _task_snapshot(task)
        meeting_changes = []

        async with self.task_repo.database.transaction():
            await self.task_repo.update_title(task.id, new_title)
            await self.task_repo.update_links(
                task.id,
                person_id=next_person_id,
                project_id=next_project_id,
            )
            for meeting in meetings:
                meeting_changes.append({"before": _meeting_snapshot(meeting)})
                await self.meeting_repo.update_activity(
                    meeting.id,
                    title=new_title,
                    person_id=next_person_id,
                    project_id=next_project_id,
                )
                updated_meeting = await self.meeting_repo.get_by_id(meeting.id)
                meeting_changes[-1]["after"] = _meeting_snapshot(updated_meeting)

            updated = await self.task_repo.get_by_id(task.id)
            if updated is None:
                raise RuntimeError("Task disappeared during rename reconciliation")
            event = await self.event_service.append_event(
                EventType.WORK_ITEM_CHANGED,
                "task",
                task.id,
                {
                    "action": "rename_task",
                    "transition": transition,
                    "source_note_id": source_note_id,
                    "before": before,
                    "after": _task_snapshot(updated),
                    "linked_meetings": meeting_changes,
                },
            )
        return ActivityMutationResult(updated, event.id, len(meetings))

    async def undo_event(self, event: EventLog) -> Task | None:
        if (
            event.event_type != EventType.WORK_ITEM_CHANGED
            or event.payload.get("action") != "rename_task"
        ):
            return None
        before = event.payload.get("before") or {}
        if not before.get("title"):
            return None
        async with self.task_repo.database.transaction():
            await self.task_repo.update_title(event.entity_id, before["title"])
            await self.task_repo.update_links(
                event.entity_id,
                person_id=before.get("person_id"),
                project_id=before.get("project_id"),
            )
            for change in event.payload.get("linked_meetings", []):
                snapshot = change.get("before") or {}
                if not snapshot.get("id") or not snapshot.get("title"):
                    continue
                await self.meeting_repo.update_activity(
                    snapshot["id"],
                    title=snapshot["title"],
                    person_id=snapshot.get("person_id"),
                    project_id=snapshot.get("project_id"),
                )
            await self.event_service.append_event(
                EventType.WORK_ITEM_UNDONE,
                "work_event",
                event.id,
                {"restored": before, "action": "rename_task"},
            )
        return await self.task_repo.get_by_id(event.entity_id)

    async def transfer_task_links(
        self, source_task_ids: list[str], target_task_id: str
    ) -> int:
        """Carry activity identity forward when task projections are merged."""
        meeting_ids: set[str] = set()
        for task_id in source_task_ids:
            meeting_ids.update(
                await self.activity_link_repo.meeting_ids_for_task(task_id)
            )
        for meeting_id in meeting_ids:
            await self.activity_link_repo.link(target_task_id, meeting_id)
        return len(meeting_ids)

    async def repair_legacy_renames(self) -> int:
        """Backfill identity links and replay old rename semantics once.

        Older versions changed only tasks.title.  A recorded rename event gives
        enough evidence to safely reconcile the linked meeting and entity links.
        """
        tasks = await self.task_repo.list_active()
        all_meetings = await self.meeting_repo.list_all()
        for note_id in {task.source_note_id for task in tasks}:
            await self.link_note_artifacts(note_id)

        repaired = 0
        for task in tasks:
            events = await self.event_service.list_events_for_entity("task", task.id)
            rename_event = next(
                (
                    event
                    for event in reversed(events)
                    if (
                        event.payload.get("conversation_intent") == "rename_task"
                        or event.payload.get("transition")
                        == "renamed_from_selection_confirmation"
                    )
                ),
                None,
            )
            if rename_event is None:
                continue
            meetings = await self._linked_meetings(task.id)
            if not meetings:
                old_title = str(
                    rename_event.payload.get("old_title") or task.title
                )
                legacy_candidates = [
                    meeting
                    for meeting in all_meetings
                    if _legacy_activity_score(task, meeting, old_title) >= 2.0
                ]
                legacy_candidates.sort(
                    key=lambda meeting: _legacy_activity_score(
                        task, meeting, old_title
                    ),
                    reverse=True,
                )
                if legacy_candidates and (
                    len(legacy_candidates) == 1
                    or _legacy_activity_score(task, legacy_candidates[0], old_title)
                    > _legacy_activity_score(task, legacy_candidates[1], old_title)
                ):
                    await self.activity_link_repo.link(
                        task.id, legacy_candidates[0].id
                    )
                    meetings = [legacy_candidates[0]]
            if not meetings:
                continue
            person_id = await self._mentioned_person_id(task.title)
            project_id = await self._mentioned_project_id(task.title)
            needs_repair = (
                task.person_id != person_id
                or task.project_id != project_id
                or any(
                    meeting.title != task.title
                    or meeting.person_id != person_id
                    or meeting.project_id != project_id
                    for meeting in meetings
                )
            )
            if not needs_repair:
                continue
            async with self.task_repo.database.transaction():
                await self.task_repo.update_links(
                    task.id, person_id=person_id, project_id=project_id
                )
                for meeting in meetings:
                    await self.meeting_repo.update_activity(
                        meeting.id,
                        title=task.title,
                        person_id=person_id,
                        project_id=project_id,
                    )
                await self.event_service.append_event(
                    EventType.WORK_ITEM_CHANGED,
                    "task",
                    task.id,
                    {
                        "action": "activity_reconciled",
                        "source_event_id": rename_event.id,
                        "linked_meeting_ids": [item.id for item in meetings],
                    },
                )
            repaired += 1
        return repaired

    async def _linked_meetings(self, task_id: str) -> list[Meeting]:
        result = []
        for meeting_id in await self.activity_link_repo.meeting_ids_for_task(task_id):
            meeting = await self.meeting_repo.get_by_id(meeting_id)
            if meeting is not None:
                result.append(meeting)
        return result

    async def _mentioned_person_id(self, text: str) -> str | None:
        return _single_mentioned_entity_id(text, await self.person_repo.list_all(), "display_name")

    async def _mentioned_project_id(self, text: str) -> str | None:
        return _single_mentioned_entity_id(text, await self.project_repo.list_all(), "name")


def _same_activity_candidate(
    task: Task, meeting: Meeting, task_count: int, meeting_count: int
) -> bool:
    if task.source_note_id != meeting.source_note_id:
        return False
    if task.due_at is not None and meeting.starts_at is not None:
        if abs((task.due_at - meeting.starts_at).total_seconds()) > 60:
            return False
        return not (
            task.person_id
            and meeting.person_id
            and task.person_id != meeting.person_id
        )
    if task_count == meeting_count == 1:
        task_tokens = set(normalize_lookup(task.title).split())
        meeting_tokens = set(normalize_lookup(meeting.title).split())
        overlap = task_tokens & meeting_tokens
        return bool(overlap) and len(overlap) / max(
            1, min(len(task_tokens), len(meeting_tokens))
        ) >= 0.6
    return False


def _activity_distance(task: Task, meeting: Meeting) -> float:
    if task.due_at is None or meeting.starts_at is None:
        return float("inf")
    return abs((task.due_at - meeting.starts_at).total_seconds())


def _legacy_activity_score(task: Task, meeting: Meeting, old_title: str) -> float:
    if task.due_at is None or meeting.starts_at is None:
        return 0.0
    if abs((task.due_at - meeting.starts_at).total_seconds()) > 60:
        return 0.0
    score = 1.0
    if task.person_id and meeting.person_id and task.person_id == meeting.person_id:
        score += 1.0
    if task.project_id and meeting.project_id and task.project_id == meeting.project_id:
        score += 0.5
    old_tokens = set(normalize_lookup(old_title).split())
    meeting_tokens = set(normalize_lookup(meeting.title).split())
    if old_tokens and meeting_tokens:
        score += len(old_tokens & meeting_tokens) / min(
            len(old_tokens), len(meeting_tokens)
        )
    return score


def _single_mentioned_entity_id(text: str, entities, name_field: str) -> str | None:
    normalized = normalize_lookup(text)
    tokens = normalized.split()
    matches: list[tuple[int, str]] = []
    for entity in entities:
        values = [getattr(entity, name_field), *getattr(entity, "aliases", [])]
        for value in values:
            needle = normalize_lookup(value)
            needle_tokens = needle.split()
            if needle_tokens and any(
                tokens[index : index + len(needle_tokens)] == needle_tokens
                for index in range(len(tokens) - len(needle_tokens) + 1)
            ):
                matches.append((len(needle_tokens), entity.id))
                break
    if not matches:
        return None
    longest = max(length for length, _ in matches)
    entity_ids = {entity_id for length, entity_id in matches if length == longest}
    return next(iter(entity_ids)) if len(entity_ids) == 1 else None


def _task_snapshot(task: Task | None) -> dict:
    if task is None:
        return {}
    return {
        "id": task.id,
        "title": task.title,
        "person_id": task.person_id,
        "project_id": task.project_id,
        "due_at": task.due_at.isoformat() if task.due_at else None,
    }


def _meeting_snapshot(meeting: Meeting | None) -> dict:
    if meeting is None:
        return {}
    return {
        "id": meeting.id,
        "title": meeting.title,
        "person_id": meeting.person_id,
        "project_id": meeting.project_id,
        "starts_at": meeting.starts_at.isoformat() if meeting.starts_at else None,
    }
