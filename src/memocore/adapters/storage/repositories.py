from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel

from memocore.adapters.storage.sqlite import Database
from memocore.domain.models import (
    ClarificationRequest,
    ClarificationStatus,
    Commitment,
    CommitmentStatus,
    EventLog,
    EventType,
    FollowUp,
    FollowUpStatus,
    Meeting,
    MemoryBucket,
    MemoryItem,
    Note,
    NoteStatus,
    Person,
    Project,
    Reminder,
    ReminderStatus,
    Task,
    utc_now,
)


ModelT = TypeVar("ModelT", bound=BaseModel)


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def _loads(value: str) -> Any:
    return json.loads(value)


class BaseRepository:
    def __init__(self, database: Database):
        self.database = database

    async def _execute(self, query: str, params: tuple[Any, ...]) -> None:
        conn = await self.database.connection()
        await conn.execute(query, params)
        await self.database.commit_if_needed()


class NoteRepository(BaseRepository):
    async def create(self, note: Note) -> Note:
        await self._execute(
            """
            INSERT INTO notes (
                id, source, source_message_id, source_chat_id, raw_text, summary,
                tags, status, metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note.id,
                note.source,
                note.source_message_id,
                note.source_chat_id,
                note.raw_text,
                note.summary,
                _json(note.tags),
                note.status.value,
                _json(note.metadata),
                _dt(note.created_at),
                _dt(note.updated_at),
            ),
        )
        return note

    async def get_by_id(self, note_id: str) -> Note | None:
        conn = await self.database.connection()
        row = await (await conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,))).fetchone()
        return _note_from_row(row) if row else None

    async def find_by_source_message(
        self, source: str, source_chat_id: str | None, source_message_id: str | None
    ) -> Note | None:
        if source_message_id is None:
            return None
        conn = await self.database.connection()
        row = await (
            await conn.execute(
                """
                SELECT * FROM notes
                WHERE source = ? AND source_chat_id IS ? AND source_message_id = ?
                """,
                (source, source_chat_id, source_message_id),
            )
        ).fetchone()
        return _note_from_row(row) if row else None

    async def list_recent_by_chat(
        self, source: str, source_chat_id: str | None, limit: int = 5
    ) -> list[Note]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM notes
                WHERE source = ? AND source_chat_id IS ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (source, source_chat_id, limit),
            )
        ).fetchall()
        return [_note_from_row(row) for row in rows]

    async def list_source_chat_ids(self, source: str = "telegram") -> list[str]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT DISTINCT source_chat_id FROM notes
                WHERE source = ? AND source_chat_id IS NOT NULL
                ORDER BY updated_at DESC
                """,
                (source,),
            )
        ).fetchall()
        return [row["source_chat_id"] for row in rows]

    async def update_status(self, note_id: str, status: NoteStatus) -> None:
        await self._execute(
            "UPDATE notes SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, _dt(utc_now()), note_id),
        )

    async def update_processed(
        self, note_id: str, summary: str, tags: list[str], status: NoteStatus = NoteStatus.PROCESSED
    ) -> None:
        await self._execute(
            "UPDATE notes SET summary = ?, tags = ?, status = ?, updated_at = ? WHERE id = ?",
            (summary, _json(tags), status.value, _dt(utc_now()), note_id),
        )


class TaskRepository(BaseRepository):
    async def create(self, task: Task) -> Task:
        await self._execute(
            """
            INSERT INTO tasks (
                id, title, description, status, priority, due_at, project_id,
                person_id, source_note_id, confidence, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.title,
                task.description,
                task.status.value,
                task.priority,
                _dt(task.due_at),
                task.project_id,
                task.person_id,
                task.source_note_id,
                task.confidence,
                _dt(task.created_at),
                _dt(task.updated_at),
            ),
        )
        return task

    async def get_by_id(self, task_id: str) -> Task | None:
        conn = await self.database.connection()
        row = await (await conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))).fetchone()
        return _task_from_row(row) if row else None

    async def list_by_note(self, note_id: str) -> list[Task]:
        conn = await self.database.connection()
        rows = await (await conn.execute("SELECT * FROM tasks WHERE source_note_id = ?", (note_id,))).fetchall()
        return [_task_from_row(row) for row in rows]

    async def list_active(self) -> list[Task]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM tasks
                WHERE status IN (?, ?, ?, ?)
                ORDER BY due_at IS NULL, due_at, created_at
                """,
                ("candidate", "open", "waiting", "blocked"),
            )
        ).fetchall()
        return [_task_from_row(row) for row in rows]

    async def list_recent_active(self, limit: int = 5) -> list[Task]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM tasks
                WHERE status IN (?, ?, ?, ?)
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                ("candidate", "open", "waiting", "blocked", limit),
            )
        ).fetchall()
        return [_task_from_row(row) for row in rows]

    async def list_done_since(self, since: datetime) -> list[Task]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = ? AND updated_at >= ?
                ORDER BY updated_at DESC
                """,
                ("done", _dt(since)),
            )
        ).fetchall()
        return [_task_from_row(row) for row in rows]

    async def list_active_by_project(self, project_id: str) -> list[Task]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM tasks
                WHERE project_id = ? AND status IN (?, ?, ?, ?)
                ORDER BY due_at IS NULL, due_at, created_at
                """,
                (project_id, "candidate", "open", "waiting", "blocked"),
            )
        ).fetchall()
        return [_task_from_row(row) for row in rows]

    async def list_active_by_person(self, person_id: str) -> list[Task]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM tasks
                WHERE person_id = ? AND status IN (?, ?, ?, ?)
                ORDER BY due_at IS NULL, due_at, created_at
                """,
                (person_id, "candidate", "open", "waiting", "blocked"),
            )
        ).fetchall()
        return [_task_from_row(row) for row in rows]

    async def update_status(self, task_id: str, status: str) -> None:
        await self._execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, _dt(utc_now()), task_id),
        )

    async def update_due_at(self, task_id: str, due_at: datetime | None) -> None:
        await self._execute(
            "UPDATE tasks SET due_at = ?, updated_at = ? WHERE id = ?",
            (_dt(due_at), _dt(utc_now()), task_id),
        )


class ReminderRepository(BaseRepository):
    async def create(self, reminder: Reminder) -> Reminder:
        await self._execute(
            """
            INSERT INTO reminders (
                id, title, remind_at, status, task_id, source_note_id,
                delivery_channel, confidence, attempt_count, claimed_at,
                recurrence_rule, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reminder.id,
                reminder.title,
                _dt(reminder.remind_at),
                reminder.status.value,
                reminder.task_id,
                reminder.source_note_id,
                reminder.delivery_channel,
                reminder.confidence,
                reminder.attempt_count,
                _dt(reminder.claimed_at),
                reminder.recurrence_rule,
                _dt(reminder.created_at),
                _dt(reminder.updated_at),
            ),
        )
        return reminder

    async def list_by_note(self, note_id: str) -> list[Reminder]:
        conn = await self.database.connection()
        rows = await (await conn.execute("SELECT * FROM reminders WHERE source_note_id = ?", (note_id,))).fetchall()
        return [_reminder_from_row(row) for row in rows]

    async def list_recent(self, limit: int = 20) -> list[Reminder]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM reminders
                WHERE status != ?
                ORDER BY remind_at IS NULL, remind_at DESC, created_at DESC
                LIMIT ?
                """,
                (ReminderStatus.CANCELLED.value, limit),
            )
        ).fetchall()
        return [_reminder_from_row(row) for row in rows]

    async def find_due(self, now: datetime) -> list[Reminder]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM reminders
                WHERE status = ? AND remind_at IS NOT NULL
                ORDER BY remind_at ASC
                """,
                (ReminderStatus.SCHEDULED.value,),
            )
        ).fetchall()
        return [
            reminder
            for reminder in (_reminder_from_row(row) for row in rows)
            if reminder.remind_at and reminder.remind_at <= now
        ]

    async def claim_due(self, now: datetime, lease_before: datetime) -> list[Reminder]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT id FROM reminders
                WHERE status = ? AND remind_at IS NOT NULL
                  AND (claimed_at IS NULL OR claimed_at <= ?)
                ORDER BY remind_at ASC
                """,
                (ReminderStatus.SCHEDULED.value, _dt(lease_before)),
            )
        ).fetchall()
        claimed: list[Reminder] = []
        for row in rows:
            reminder_row = await (
                await conn.execute("SELECT * FROM reminders WHERE id = ?", (row["id"],))
            ).fetchone()
            reminder = _reminder_from_row(reminder_row)
            if not reminder.remind_at or reminder.remind_at > now:
                continue
            result = await conn.execute(
                """
                UPDATE reminders
                SET claimed_at = ?, attempt_count = attempt_count + 1, updated_at = ?
                WHERE id = ? AND status = ?
                  AND (claimed_at IS NULL OR claimed_at <= ?)
                """,
                (_dt(now), _dt(now), row["id"], ReminderStatus.SCHEDULED.value, _dt(lease_before)),
            )
            if result.rowcount:
                claimed.append(reminder)
        await self.database.commit_if_needed()
        return claimed

    async def update_status(self, reminder_id: str, status: ReminderStatus) -> None:
        await self._execute(
            "UPDATE reminders SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, _dt(utc_now()), reminder_id),
        )

    async def update_remind_at(self, reminder_id: str, remind_at: datetime) -> None:
        await self._execute(
            "UPDATE reminders SET remind_at = ?, claimed_at = NULL, updated_at = ? WHERE id = ?",
            (_dt(remind_at), _dt(utc_now()), reminder_id),
        )

    async def update_schedule(
        self,
        reminder_id: str,
        remind_at: datetime,
        status: ReminderStatus = ReminderStatus.SCHEDULED,
    ) -> None:
        await self._execute(
            """
            UPDATE reminders
            SET remind_at = ?, status = ?, claimed_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (_dt(remind_at), status.value, _dt(utc_now()), reminder_id),
        )

    async def get_by_id(self, reminder_id: str) -> Reminder | None:
        conn = await self.database.connection()
        row = await (
            await conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        ).fetchone()
        return _reminder_from_row(row) if row else None


class ProjectRepository(BaseRepository):
    async def find_or_create(self, name: str) -> Project:
        conn = await self.database.connection()
        row = await (await conn.execute("SELECT * FROM projects WHERE name = ?", (name,))).fetchone()
        if row:
            project = _project_from_row(row)
            now = utc_now()
            await self._execute(
                "UPDATE projects SET last_seen_at = ?, updated_at = ? WHERE id = ?",
                (_dt(now), _dt(now), project.id),
            )
            project.last_seen_at = now
            project.updated_at = now
            return project

        project = Project(name=name)
        await self._execute(
            """
            INSERT INTO projects (
                id, name, summary, status, tags, last_seen_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project.id,
                project.name,
                project.summary,
                project.status.value,
                _json(project.tags),
                _dt(project.last_seen_at),
                _dt(project.created_at),
                _dt(project.updated_at),
            ),
        )
        return project

    async def list_all(self) -> list[Project]:
        conn = await self.database.connection()
        rows = await (await conn.execute("SELECT * FROM projects ORDER BY last_seen_at DESC")).fetchall()
        return [_project_from_row(row) for row in rows]

    async def get_by_id(self, project_id: str) -> Project | None:
        conn = await self.database.connection()
        row = await (await conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,))).fetchone()
        return _project_from_row(row) if row else None


class PersonRepository(BaseRepository):
    async def create(self, person: Person) -> Person:
        await self._execute(
            """
            INSERT INTO people (
                id, display_name, aliases, relationship, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                person.id,
                person.display_name,
                _json(person.aliases),
                person.relationship,
                person.notes,
                _dt(person.created_at),
                _dt(person.updated_at),
            ),
        )
        return person

    async def get_by_id(self, person_id: str) -> Person | None:
        conn = await self.database.connection()
        row = await (await conn.execute("SELECT * FROM people WHERE id = ?", (person_id,))).fetchone()
        return _person_from_row(row) if row else None

    async def find_by_name_or_alias(self, query: str) -> Person | None:
        normalized_query = normalize_lookup(query)
        if not normalized_query:
            return None
        for person in await self.list_all():
            names = [person.display_name, *person.aliases]
            if any(
                normalized_query == normalize_lookup(name)
                or normalized_query in normalize_lookup(name)
                or normalize_lookup(name) in normalized_query
                for name in names
            ):
                return person
        return None

    async def list_all(self) -> list[Person]:
        conn = await self.database.connection()
        rows = await (await conn.execute("SELECT * FROM people ORDER BY display_name")).fetchall()
        return [_person_from_row(row) for row in rows]


class MeetingRepository(BaseRepository):
    async def create(self, meeting: Meeting) -> Meeting:
        await self._execute(
            """
            INSERT INTO meetings (
                id, title, starts_at, ends_at, project_id, source_note_id,
                person_id, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meeting.id, meeting.title, _dt(meeting.starts_at), _dt(meeting.ends_at),
                meeting.project_id, meeting.source_note_id, meeting.person_id, meeting.notes,
                _dt(meeting.created_at), _dt(meeting.updated_at),
            ),
        )
        return meeting

    async def list_upcoming(self, now: datetime) -> list[Meeting]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                "SELECT * FROM meetings WHERE starts_at >= ? ORDER BY starts_at",
                (_dt(now),),
            )
        ).fetchall()
        return [_meeting_from_row(row) for row in rows]

    async def list_by_project(self, project_id: str, limit: int = 10) -> list[Meeting]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM meetings
                WHERE project_id = ?
                ORDER BY starts_at IS NULL, starts_at DESC, created_at DESC
                LIMIT ?
                """,
                (project_id, limit),
            )
        ).fetchall()
        return [_meeting_from_row(row) for row in rows]

    async def list_by_person(self, person_id: str, limit: int = 10) -> list[Meeting]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT DISTINCT m.* FROM meetings m
                LEFT JOIN meeting_people mp ON mp.meeting_id = m.id
                WHERE m.person_id = ? OR mp.person_id = ?
                ORDER BY m.starts_at IS NULL, m.starts_at DESC, m.created_at DESC
                LIMIT ?
                """,
                (person_id, person_id, limit),
            )
        ).fetchall()
        return [_meeting_from_row(row) for row in rows]

    async def add_person(self, meeting_id: str, person_id: str, role: str = "") -> None:
        await self._execute(
            """
            INSERT OR REPLACE INTO meeting_people (meeting_id, person_id, role, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (meeting_id, person_id, role, _dt(utc_now())),
        )


class FollowUpRepository(BaseRepository):
    async def create(self, followup: FollowUp) -> FollowUp:
        await self._execute(
            """
            INSERT INTO followups (
                id, title, due_at, status, person_id, project_id, source_note_id,
                notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                followup.id, followup.title, _dt(followup.due_at), followup.status.value,
                followup.person_id, followup.project_id, followup.source_note_id,
                followup.notes, _dt(followup.created_at), _dt(followup.updated_at),
            ),
        )
        return followup

    async def list_open(self) -> list[FollowUp]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                "SELECT * FROM followups WHERE status = ? ORDER BY due_at IS NULL, due_at, created_at",
                (FollowUpStatus.OPEN.value,),
            )
        ).fetchall()
        return [_followup_from_row(row) for row in rows]

    async def list_open_by_project(self, project_id: str) -> list[FollowUp]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM followups
                WHERE status = ? AND project_id = ?
                ORDER BY due_at IS NULL, due_at, created_at
                """,
                (FollowUpStatus.OPEN.value, project_id),
            )
        ).fetchall()
        return [_followup_from_row(row) for row in rows]

    async def list_open_by_person(self, person_id: str) -> list[FollowUp]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM followups
                WHERE status = ? AND person_id = ?
                ORDER BY due_at IS NULL, due_at, created_at
                """,
                (FollowUpStatus.OPEN.value, person_id),
            )
        ).fetchall()
        return [_followup_from_row(row) for row in rows]


class CommitmentRepository(BaseRepository):
    async def create(self, commitment: Commitment) -> Commitment:
        await self._execute(
            """
            INSERT INTO commitments (
                id, title, direction, status, person_id, project_id, due_at,
                source_note_id, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                commitment.id,
                commitment.title,
                commitment.direction.value,
                commitment.status.value,
                commitment.person_id,
                commitment.project_id,
                _dt(commitment.due_at),
                commitment.source_note_id,
                commitment.notes,
                _dt(commitment.created_at),
                _dt(commitment.updated_at),
            ),
        )
        return commitment

    async def get_by_id(self, commitment_id: str) -> Commitment | None:
        conn = await self.database.connection()
        row = await (
            await conn.execute("SELECT * FROM commitments WHERE id = ?", (commitment_id,))
        ).fetchone()
        return _commitment_from_row(row) if row else None

    async def list_open(self) -> list[Commitment]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM commitments
                WHERE status = ?
                ORDER BY due_at IS NULL, due_at, created_at
                """,
                (CommitmentStatus.OPEN.value,),
            )
        ).fetchall()
        return [_commitment_from_row(row) for row in rows]

    async def list_open_by_project(self, project_id: str) -> list[Commitment]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM commitments
                WHERE status = ? AND project_id = ?
                ORDER BY due_at IS NULL, due_at, created_at
                """,
                (CommitmentStatus.OPEN.value, project_id),
            )
        ).fetchall()
        return [_commitment_from_row(row) for row in rows]

    async def list_open_by_person(self, person_id: str) -> list[Commitment]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM commitments
                WHERE status = ? AND person_id = ?
                ORDER BY due_at IS NULL, due_at, created_at
                """,
                (CommitmentStatus.OPEN.value, person_id),
            )
        ).fetchall()
        return [_commitment_from_row(row) for row in rows]

    async def update_status(self, commitment_id: str, status: CommitmentStatus) -> None:
        await self._execute(
            "UPDATE commitments SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, _dt(utc_now()), commitment_id),
        )


class MemoryItemRepository(BaseRepository):
    async def create(self, item: MemoryItem) -> MemoryItem:
        await self._execute(
            """
            INSERT INTO memory_items (
                id, bucket, kind, content, source_note_id, project_id,
                person_id, confidence, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.bucket.value,
                item.kind.value,
                item.content,
                item.source_note_id,
                item.project_id,
                item.person_id,
                item.confidence,
                item.status.value,
                _dt(item.created_at),
                _dt(item.updated_at),
            ),
        )
        return item

    async def list_by_bucket(self, bucket: MemoryBucket) -> list[MemoryItem]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                "SELECT * FROM memory_items WHERE bucket = ? ORDER BY updated_at DESC",
                (bucket.value,),
            )
        ).fetchall()
        return [_memory_from_row(row) for row in rows]

    async def list_all(self) -> list[MemoryItem]:
        conn = await self.database.connection()
        rows = await (await conn.execute("SELECT * FROM memory_items ORDER BY updated_at DESC")).fetchall()
        return [_memory_from_row(row) for row in rows]

    async def list_by_note(self, note_id: str) -> list[MemoryItem]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute("SELECT * FROM memory_items WHERE source_note_id = ?", (note_id,))
        ).fetchall()
        return [_memory_from_row(row) for row in rows]

    async def list_active(self) -> list[MemoryItem]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                "SELECT * FROM memory_items WHERE status IN (?, ?) ORDER BY updated_at DESC",
                ("candidate", "active"),
            )
        ).fetchall()
        return [_memory_from_row(row) for row in rows]

    async def list_active_by_project(self, project_id: str) -> list[MemoryItem]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM memory_items
                WHERE project_id = ? AND status IN (?, ?)
                ORDER BY updated_at DESC
                """,
                (project_id, "candidate", "active"),
            )
        ).fetchall()
        return [_memory_from_row(row) for row in rows]

    async def list_active_by_person(self, person_id: str) -> list[MemoryItem]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM memory_items
                WHERE person_id = ? AND status IN (?, ?)
                ORDER BY updated_at DESC
                """,
                (person_id, "candidate", "active"),
            )
        ).fetchall()
        return [_memory_from_row(row) for row in rows]

    async def update_status(self, item_id: str, status: str) -> None:
        await self._execute(
            "UPDATE memory_items SET status = ?, updated_at = ? WHERE id = ?",
            (status, _dt(utc_now()), item_id),
        )

    async def delete(self, item_id: str) -> None:
        await self._execute("DELETE FROM memory_items WHERE id = ?", (item_id,))


class ClarificationRequestRepository(BaseRepository):
    async def create(self, request: ClarificationRequest) -> ClarificationRequest:
        await self._execute(
            """
            INSERT INTO clarification_requests (
                id, source_chat_id, source_message_id, entity_type, entity_id,
                field_name, question, status, answer_text, created_at, updated_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.id,
                request.source_chat_id,
                request.source_message_id,
                request.entity_type,
                request.entity_id,
                request.field_name,
                request.question,
                request.status.value,
                request.answer_text,
                _dt(request.created_at),
                _dt(request.updated_at),
                _dt(request.resolved_at),
            ),
        )
        return request

    async def find_pending_for_chat(self, source_chat_id: str) -> ClarificationRequest | None:
        conn = await self.database.connection()
        row = await (
            await conn.execute(
                """
                SELECT * FROM clarification_requests
                WHERE source_chat_id = ? AND status = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (source_chat_id, ClarificationStatus.PENDING.value),
            )
        ).fetchone()
        return _clarification_from_row(row) if row else None

    async def resolve(self, request_id: str, answer_text: str) -> None:
        now = utc_now()
        await self._execute(
            """
            UPDATE clarification_requests
            SET status = ?, answer_text = ?, updated_at = ?, resolved_at = ?
            WHERE id = ?
            """,
            (
                ClarificationStatus.RESOLVED.value,
                answer_text,
                _dt(now),
                _dt(now),
                request_id,
            ),
        )

    async def cancel(self, request_id: str, answer_text: str | None = None) -> None:
        await self._execute(
            """
            UPDATE clarification_requests
            SET status = ?, answer_text = ?, updated_at = ?
            WHERE id = ?
            """,
            (ClarificationStatus.CANCELLED.value, answer_text, _dt(utc_now()), request_id),
        )


class EventLogRepository(BaseRepository):
    async def create(self, event: EventLog) -> EventLog:
        await self._execute(
            """
            INSERT INTO event_logs (
                id, event_type, entity_type, entity_id, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.event_type.value,
                event.entity_type,
                event.entity_id,
                _json(event.payload),
                _dt(event.created_at),
            ),
        )
        return event

    async def list_by_entity(self, entity_type: str, entity_id: str) -> list[EventLog]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                "SELECT * FROM event_logs WHERE entity_type = ? AND entity_id = ? ORDER BY created_at",
                (entity_type, entity_id),
            )
        ).fetchall()
        return [_event_from_row(row) for row in rows]

    async def list_recent(
        self,
        event_type: EventType | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[EventLog]:
        conn = await self.database.connection()
        clauses: list[str] = []
        params: list[Any] = []
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type.value)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(_dt(since))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await (
            await conn.execute(
                f"SELECT * FROM event_logs {where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            )
        ).fetchall()
        return [_event_from_row(row) for row in rows]

    async def exists_recent(
        self,
        event_type: EventType,
        entity_type: str,
        entity_id: str,
        since: datetime,
    ) -> bool:
        conn = await self.database.connection()
        row = await (
            await conn.execute(
                """
                SELECT 1 FROM event_logs
                WHERE event_type = ? AND entity_type = ? AND entity_id = ? AND created_at >= ?
                LIMIT 1
                """,
                (event_type.value, entity_type, entity_id, _dt(since)),
            )
        ).fetchone()
        return row is not None


def parse_model_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def normalize_lookup(value: str) -> str:
    import unicodedata

    lowered = value.lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    ascii_text = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join("".join(char if char.isalnum() else " " for char in ascii_text).split())


def _note_from_row(row: Any) -> Note:
    return Note(
        id=row["id"],
        source=row["source"],
        source_message_id=row["source_message_id"],
        source_chat_id=row["source_chat_id"],
        raw_text=row["raw_text"],
        summary=row["summary"],
        tags=_loads(row["tags"]),
        status=row["status"],
        metadata=_loads(row["metadata"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _task_from_row(row: Any) -> Task:
    return Task(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        status=row["status"],
        priority=row["priority"],
        due_at=_parse_dt(row["due_at"]),
        project_id=row["project_id"],
        person_id=row["person_id"] if "person_id" in row.keys() else None,
        source_note_id=row["source_note_id"],
        confidence=row["confidence"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _reminder_from_row(row: Any) -> Reminder:
    return Reminder(
        id=row["id"],
        title=row["title"],
        remind_at=_parse_dt(row["remind_at"]),
        status=row["status"],
        task_id=row["task_id"],
        source_note_id=row["source_note_id"],
        delivery_channel=row["delivery_channel"],
        confidence=row["confidence"],
        attempt_count=row["attempt_count"],
        claimed_at=_parse_dt(row["claimed_at"]),
        recurrence_rule=row["recurrence_rule"] if "recurrence_rule" in row.keys() else None,
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _project_from_row(row: Any) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        summary=row["summary"],
        status=row["status"],
        tags=_loads(row["tags"]),
        last_seen_at=_parse_dt(row["last_seen_at"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _person_from_row(row: Any) -> Person:
    return Person(
        id=row["id"],
        display_name=row["display_name"],
        aliases=_loads(row["aliases"]),
        relationship=row["relationship"],
        notes=row["notes"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _meeting_from_row(row: Any) -> Meeting:
    return Meeting(
        id=row["id"],
        title=row["title"],
        starts_at=_parse_dt(row["starts_at"]),
        ends_at=_parse_dt(row["ends_at"]),
        project_id=row["project_id"],
        person_id=row["person_id"] if "person_id" in row.keys() else None,
        source_note_id=row["source_note_id"],
        notes=row["notes"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _followup_from_row(row: Any) -> FollowUp:
    return FollowUp(
        id=row["id"],
        title=row["title"],
        due_at=_parse_dt(row["due_at"]),
        status=row["status"],
        person_id=row["person_id"],
        project_id=row["project_id"],
        source_note_id=row["source_note_id"],
        notes=row["notes"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _commitment_from_row(row: Any) -> Commitment:
    return Commitment(
        id=row["id"],
        title=row["title"],
        direction=row["direction"],
        status=row["status"],
        person_id=row["person_id"],
        project_id=row["project_id"],
        due_at=_parse_dt(row["due_at"]),
        source_note_id=row["source_note_id"],
        notes=row["notes"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _memory_from_row(row: Any) -> MemoryItem:
    return MemoryItem(
        id=row["id"],
        bucket=row["bucket"],
        kind=row["kind"],
        content=row["content"],
        source_note_id=row["source_note_id"],
        project_id=row["project_id"],
        person_id=row["person_id"] if "person_id" in row.keys() else None,
        confidence=row["confidence"],
        status=row["status"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _clarification_from_row(row: Any) -> ClarificationRequest:
    return ClarificationRequest(
        id=row["id"],
        source_chat_id=row["source_chat_id"],
        source_message_id=row["source_message_id"],
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        field_name=row["field_name"],
        question=row["question"],
        status=row["status"],
        answer_text=row["answer_text"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        resolved_at=_parse_dt(row["resolved_at"]),
    )


def _event_from_row(row: Any) -> EventLog:
    return EventLog(
        id=row["id"],
        event_type=EventType(row["event_type"]),
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        payload=_loads(row["payload"]),
        created_at=_parse_dt(row["created_at"]),
    )
