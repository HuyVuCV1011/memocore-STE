from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from memocore.adapters.storage.sqlite import Database
from memocore.domain.models import (
    ClarificationRequest,
    ClarificationStatus,
    ChatContext,
    Commitment,
    CommitmentStatus,
    EventLog,
    EventType,
    FollowUp,
    FollowUpStatus,
    Meeting,
    MemoryBucket,
    MemoryItem,
    MemoryStatus,
    Note,
    NoteStatus,
    Person,
    Project,
    ProjectStatus,
    ProjectType,
    Reminder,
    ReminderStatus,
    Task,
    TaskStatus,
    utc_now,
)
from memocore.domain.recurrence import next_recurrence_occurrence


ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class TaskListContext:
    source_chat_id: str
    task_ids: tuple[str, ...]
    source_view: str
    updated_at: datetime


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


def _rank_entity_matches(
    query: str,
    entities: list[ModelT],
    name_field: str,
    aliases_field: str,
) -> list[ModelT]:
    normalized_query = normalize_lookup(query)
    if not normalized_query:
        return []
    query_tokens = normalized_query.split()
    exact: list[ModelT] = []
    token_matches: list[ModelT] = []
    for entity in entities:
        values = [getattr(entity, name_field), *getattr(entity, aliases_field)]
        normalized_values = [normalize_lookup(value) for value in values]
        if normalized_query in normalized_values:
            exact.append(entity)
            continue
        if any(
            _contains_token_sequence(value.split(), query_tokens)
            for value in normalized_values
        ):
            token_matches.append(entity)
    return exact or token_matches


def _contains_token_sequence(value_tokens: list[str], query_tokens: list[str]) -> bool:
    if not query_tokens or len(query_tokens) > len(value_tokens):
        return False
    width = len(query_tokens)
    return any(
        value_tokens[index : index + width] == query_tokens
        for index in range(len(value_tokens) - width + 1)
    )


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

    async def list_recent(self, limit: int = 100) -> list[Note]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                "SELECT * FROM notes ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
        return [_note_from_row(row) for row in rows]

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
                person_id, source_note_id, confidence, recurrence_rule,
                recurrence_series_id, recurrence_occurrence_at, duration_minutes,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                task.recurrence_rule,
                task.recurrence_series_id,
                _dt(task.recurrence_occurrence_at),
                task.duration_minutes,
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

    async def list_all(self) -> list[Task]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                "SELECT * FROM tasks ORDER BY updated_at DESC, created_at DESC"
            )
        ).fetchall()
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

    async def update_duration(self, task_id: str, duration_minutes: int | None) -> None:
        await self._execute(
            "UPDATE tasks SET duration_minutes = ?, updated_at = ? WHERE id = ?",
            (duration_minutes, _dt(utc_now()), task_id),
        )

    async def update_title(self, task_id: str, title: str) -> None:
        await self._execute(
            "UPDATE tasks SET title = ?, updated_at = ? WHERE id = ?",
            (title, _dt(utc_now()), task_id),
        )

    async def update_links(
        self,
        task_id: str,
        *,
        person_id: str | None,
        project_id: str | None,
    ) -> None:
        await self._execute(
            """
            UPDATE tasks
            SET person_id = ?, project_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (person_id, project_id, _dt(utc_now()), task_id),
        )

    async def update_priority(self, task_id: str, priority: str) -> None:
        await self._execute(
            "UPDATE tasks SET priority = ?, updated_at = ? WHERE id = ?",
            (priority, _dt(utc_now()), task_id),
        )

    async def delete(self, task_id: str) -> None:
        await self._execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    async def update_recurrence(self, task_id: str, recurrence_rule: str | None) -> None:
        task = await self.get_by_id(task_id)
        if task is None:
            return
        series_id = task.recurrence_series_id or (task.id if recurrence_rule else None)
        occurrence_at = task.due_at if recurrence_rule else None
        await self._execute(
            """
            UPDATE tasks
            SET recurrence_rule = ?, recurrence_series_id = ?,
                recurrence_occurrence_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                recurrence_rule,
                series_id,
                _dt(occurrence_at),
                _dt(utc_now()),
                task_id,
            ),
        )

    async def find_recurrence_occurrence(
        self, series_id: str, occurrence_at: datetime
    ) -> Task | None:
        conn = await self.database.connection()
        row = await (
            await conn.execute(
                """
                SELECT * FROM tasks
                WHERE recurrence_series_id = ? AND recurrence_occurrence_at = ?
                LIMIT 1
                """,
                (series_id, _dt(occurrence_at)),
            )
        ).fetchone()
        return _task_from_row(row) if row else None

    async def reschedule_recurrence_occurrence(
        self,
        task_id: str,
        due_at: datetime,
    ) -> bool:
        task = await self.get_by_id(task_id)
        if task is None or not task.recurrence_rule:
            return False
        series_id = task.recurrence_series_id or task.id
        existing = await self.find_recurrence_occurrence(series_id, due_at)
        if existing is not None and existing.id != task_id:
            return False
        await self._execute(
            """
            UPDATE tasks
            SET due_at = ?, recurrence_occurrence_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (_dt(due_at), _dt(due_at), _dt(utc_now()), task_id),
        )
        return True

    async def complete_and_schedule_next(
        self, task_id: str
    ) -> tuple[Task | None, Task | None, bool]:
        task = await self.get_by_id(task_id)
        if task is None:
            return None, None, False
        if str(task.status) == TaskStatus.DONE.value:
            if task.recurrence_rule and task.due_at:
                occurrence_at = task.recurrence_occurrence_at or task.due_at
                next_due = next_recurrence_occurrence(
                    occurrence_at, task.recurrence_rule
                )
                series_id = task.recurrence_series_id or task.id
                existing = await self.find_recurrence_occurrence(
                    series_id, next_due
                )
                if existing is not None:
                    return task, existing, False
                next_task = Task(
                    title=task.title,
                    description=task.description,
                    status=TaskStatus.OPEN,
                    priority=task.priority,
                    due_at=next_due,
                    project_id=task.project_id,
                    person_id=task.person_id,
                    source_note_id=task.source_note_id,
                    confidence=task.confidence,
                    recurrence_rule=task.recurrence_rule,
                    recurrence_series_id=series_id,
                    recurrence_occurrence_at=next_due,
                    duration_minutes=task.duration_minutes,
                )
                await self.create(next_task)
                return task, next_task, True
            return task, None, False

        async with self.database.transaction():
            await self.update_status(task.id, TaskStatus.DONE.value)
            if not task.recurrence_rule or task.due_at is None:
                return await self.get_by_id(task.id), None, False
            occurrence_at = task.recurrence_occurrence_at or task.due_at
            next_due = next_recurrence_occurrence(
                occurrence_at,
                task.recurrence_rule,
            )
            series_id = task.recurrence_series_id or task.id
            existing = await self.find_recurrence_occurrence(series_id, next_due)
            if existing is not None:
                return await self.get_by_id(task.id), existing, False
            next_task = Task(
                title=task.title,
                description=task.description,
                status=TaskStatus.OPEN,
                priority=task.priority,
                due_at=next_due,
                project_id=task.project_id,
                person_id=task.person_id,
                source_note_id=task.source_note_id,
                confidence=task.confidence,
                recurrence_rule=task.recurrence_rule,
                recurrence_series_id=series_id,
                recurrence_occurrence_at=next_due,
                duration_minutes=task.duration_minutes,
            )
            await self.create(next_task)
            return await self.get_by_id(task.id), next_task, True


class TaskListContextRepository(BaseRepository):
    async def save(
        self,
        source_chat_id: str,
        task_ids: list[str],
        source_view: str,
        *,
        now: datetime | None = None,
    ) -> None:
        saved_at = _dt(now or utc_now())
        await self._execute(
            """
            INSERT INTO task_list_contexts (
                source_chat_id, task_ids, source_view, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_chat_id) DO UPDATE SET
                task_ids = excluded.task_ids,
                source_view = excluded.source_view,
                updated_at = excluded.updated_at
            """,
            (source_chat_id, _json(task_ids), source_view, saved_at, saved_at),
        )

    async def get(self, source_chat_id: str) -> list[str]:
        context = await self.get_context(source_chat_id)
        return list(context.task_ids) if context else []

    async def get_context(
        self,
        source_chat_id: str,
        *,
        max_age: timedelta | None = None,
        now: datetime | None = None,
    ) -> TaskListContext | None:
        conn = await self.database.connection()
        row = await (
            await conn.execute(
                """
                SELECT source_chat_id, task_ids, source_view, updated_at
                FROM task_list_contexts
                WHERE source_chat_id = ?
                """,
                (source_chat_id,),
            )
        ).fetchone()
        if row is None:
            return None
        updated_at = _parse_dt(row["updated_at"])
        if updated_at is None:
            return None
        current = now or utc_now()
        if max_age is not None and updated_at < current - max_age:
            return None
        return TaskListContext(
            source_chat_id=row["source_chat_id"],
            task_ids=tuple(_loads(row["task_ids"])),
            source_view=row["source_view"],
            updated_at=updated_at,
        )


class ChatContextRepository(BaseRepository):
    async def get(self, source_chat_id: str) -> ChatContext | None:
        conn = await self.database.connection()
        row = await (
            await conn.execute(
                "SELECT * FROM chat_contexts WHERE source_chat_id = ?",
                (source_chat_id,),
            )
        ).fetchone()
        if row is None:
            return None
        return ChatContext(
            source_chat_id=row["source_chat_id"],
            focused_entity_type=row["focused_entity_type"],
            focused_entity_id=row["focused_entity_id"],
            last_intent=row["last_intent"],
            last_result_entity_ids=_loads(row["last_result_entity_ids"]),
            updated_at=_parse_dt(row["updated_at"]),
            expires_at=_parse_dt(row["expires_at"]),
        )

    async def save(self, context: ChatContext) -> None:
        await self._execute(
            """
            INSERT INTO chat_contexts (
                source_chat_id, focused_entity_type, focused_entity_id,
                last_intent, last_result_entity_ids, updated_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_chat_id) DO UPDATE SET
                focused_entity_type = excluded.focused_entity_type,
                focused_entity_id = excluded.focused_entity_id,
                last_intent = excluded.last_intent,
                last_result_entity_ids = excluded.last_result_entity_ids,
                updated_at = excluded.updated_at,
                expires_at = excluded.expires_at
            """,
            (
                context.source_chat_id,
                context.focused_entity_type,
                context.focused_entity_id,
                context.last_intent,
                _json(context.last_result_entity_ids),
                _dt(context.updated_at),
                _dt(context.expires_at),
            ),
        )

    async def append_turn(
        self,
        *,
        source_chat_id: str,
        source_message_id: str | None,
        raw_text: str,
        intent: str,
        focused_entity_type: str | None,
        focused_entity_id: str | None,
        result_entity_ids: list[str] | None = None,
        assistant_reply: str | None = None,
        plan: dict[str, Any] | None = None,
    ) -> None:
        await self._execute(
            """
            INSERT INTO conversation_turns (
                id, source_chat_id, source_message_id, raw_text, intent,
                focused_entity_type, focused_entity_id, result_entity_ids,
                assistant_reply, plan_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                source_chat_id,
                source_message_id,
                raw_text,
                intent,
                focused_entity_type,
                focused_entity_id,
                _json(result_entity_ids or []),
                assistant_reply,
                _json(plan or {}),
                _dt(utc_now()),
            ),
        )

    async def list_recent_turns(
        self, source_chat_id: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT raw_text, intent, focused_entity_type, focused_entity_id,
                       result_entity_ids, assistant_reply, plan_json, created_at
                FROM conversation_turns
                WHERE source_chat_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (source_chat_id, limit),
            )
        ).fetchall()
        return [
            {
                "raw_text": row["raw_text"],
                "intent": row["intent"],
                "focused_entity_type": row["focused_entity_type"],
                "focused_entity_id": row["focused_entity_id"],
                "result_entity_ids": _loads(row["result_entity_ids"]),
                "assistant_reply": row["assistant_reply"],
                "plan": _loads(row["plan_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]


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

    async def list_all(self) -> list[Reminder]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                "SELECT * FROM reminders ORDER BY updated_at DESC, created_at DESC"
            )
        ).fetchall()
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
        if row is None:
            rows = await (await conn.execute("SELECT * FROM projects")).fetchall()
            normalized = normalize_lookup(name)
            row = next(
                (
                    candidate
                    for candidate in rows
                    if normalized
                    in {
                        normalize_lookup(alias)
                        for alias in _loads(candidate["aliases"])
                    }
                ),
                None,
            )
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
        project.status = ProjectStatus.REVIEW
        project.project_type = None
        await self._execute(
            """
            INSERT INTO projects (
                id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at,
                project_type, parent_project_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project.id,
                project.name,
                _json(project.aliases),
                project.summary,
                project.status.value,
                _json(project.tags),
                _dt(project.last_seen_at),
                _dt(project.created_at),
                _dt(project.updated_at),
                project.project_type.value if project.project_type else None,
                project.parent_project_id,
            ),
        )
        return project

    async def list_roots(self) -> list[Project]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                "SELECT * FROM projects WHERE parent_project_id IS NULL AND status NOT IN (?, ?) ORDER BY last_seen_at DESC",
                ("archived", "review")
            )
        ).fetchall()
        return [_project_from_row(row) for row in rows]

    async def list_children(self, parent_id: str) -> list[Project]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                "SELECT * FROM projects WHERE parent_project_id = ? ORDER BY last_seen_at DESC",
                (parent_id,)
            )
        ).fetchall()
        return [_project_from_row(row) for row in rows]

    async def list_by_type_or_status(
        self,
        project_type: ProjectType | None = None,
        status: ProjectStatus | None = None,
    ) -> list[Project]:
        conn = await self.database.connection()
        query = "SELECT * FROM projects"
        clauses = []
        params = []
        if project_type:
            clauses.append("project_type = ?")
            params.append(project_type.value)
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY last_seen_at DESC"
        rows = await (await conn.execute(query, tuple(params))).fetchall()
        return [_project_from_row(row) for row in rows]

    async def update_taxonomy(
        self,
        project_id: str,
        project_type: ProjectType,
        status: ProjectStatus,
        parent_project_id: str | None,
    ) -> None:
        if isinstance(project_type, str):
            project_type = ProjectType(project_type)
        if isinstance(status, str):
            status = ProjectStatus(status)
        parent_project_id = parent_project_id or None

        async with self.database.transaction():
            project = await self.get_by_id(project_id)
            if not project:
                raise ValueError("Project not found")

            # Check if there is any real change (idempotency check)
            if (
                project.project_type == project_type
                and project.status == status
                and project.parent_project_id == parent_project_id
            ):
                return

            # Ngăn project tự làm parent của chính nó
            if parent_project_id and parent_project_id == project_id:
                raise ValueError("Project cannot be its own parent")

            # Ngăn cycle
            if parent_project_id:
                curr_id = parent_project_id
                visited = {project_id}
                while curr_id:
                    if curr_id in visited:
                        raise ValueError("Cycle detected in project hierarchy")
                    visited.add(curr_id)
                    parent_project = await self.get_by_id(curr_id)
                    if not parent_project:
                        break
                    curr_id = parent_project.parent_project_id

            conn = await self.database.connection()
            # Cập nhật taxonomy
            now = utc_now()
            await conn.execute(
                """
                UPDATE projects
                SET project_type = ?, status = ?, parent_project_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    project_type.value,
                    status.value,
                    parent_project_id,
                    _dt(now),
                    project_id,
                ),
            )

            # Ghi audit event log
            event_id = str(uuid4())
            payload = {
                "old_type": project.project_type.value if project.project_type else None,
                "new_type": project_type.value,
                "old_status": project.status.value if isinstance(project.status, ProjectStatus) else str(project.status),
                "new_status": status.value,
                "old_parent_id": project.parent_project_id,
                "new_parent_id": parent_project_id,
            }
            await conn.execute(
                """
                INSERT INTO event_logs (id, event_type, entity_type, entity_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    "project_taxonomy_updated",
                    "project",
                    project_id,
                    _json(payload),
                    _dt(now),
                ),
            )

    async def list_all(self) -> list[Project]:
        conn = await self.database.connection()
        rows = await (await conn.execute("SELECT * FROM projects ORDER BY last_seen_at DESC")).fetchall()
        return [_project_from_row(row) for row in rows]

    async def find_matches(self, query: str) -> list[Project]:
        return _rank_entity_matches(query, await self.list_all(), "name", "aliases")

    async def find_by_name_or_alias(self, query: str) -> Project | None:
        matches = await self.find_matches(query)
        return matches[0] if len(matches) == 1 else None

    async def get_by_id(self, project_id: str) -> Project | None:
        conn = await self.database.connection()
        row = await (await conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,))).fetchone()
        return _project_from_row(row) if row else None

    async def update_aliases(self, project_id: str, aliases: list[str]) -> None:
        await self._execute(
            "UPDATE projects SET aliases = ?, updated_at = ? WHERE id = ?",
            (_json(aliases), _dt(utc_now()), project_id),
        )


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
        matches = await self.find_matches(query)
        return matches[0] if len(matches) == 1 else None

    async def find_matches(self, query: str) -> list[Person]:
        return _rank_entity_matches(query, await self.list_all(), "display_name", "aliases")

    async def update_aliases(self, person_id: str, aliases: list[str]) -> None:
        await self._execute(
            "UPDATE people SET aliases = ?, updated_at = ? WHERE id = ?",
            (_json(aliases), _dt(utc_now()), person_id),
        )

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

    async def get_by_id(self, meeting_id: str) -> Meeting | None:
        conn = await self.database.connection()
        row = await (
            await conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
        ).fetchone()
        return _meeting_from_row(row) if row else None

    async def list_all(self) -> list[Meeting]:
        conn = await self.database.connection()
        rows = await (await conn.execute("SELECT * FROM meetings ORDER BY starts_at DESC")).fetchall()
        return [_meeting_from_row(row) for row in rows]

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

    async def list_by_note(self, note_id: str) -> list[Meeting]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute("SELECT * FROM meetings WHERE source_note_id = ?", (note_id,))
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

    async def update_activity(
        self,
        meeting_id: str,
        *,
        title: str,
        person_id: str | None,
        project_id: str | None,
    ) -> None:
        await self._execute(
            """
            UPDATE meetings
            SET title = ?, person_id = ?, project_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (title, person_id, project_id, _dt(utc_now()), meeting_id),
        )
        await self._execute(
            "DELETE FROM meeting_people WHERE meeting_id = ?",
            (meeting_id,),
        )
        if person_id is not None:
            await self.add_person(meeting_id, person_id)


class ActivityLinkRepository(BaseRepository):
    """Persistent identity links between task and meeting projections."""

    async def link(
        self,
        task_id: str,
        meeting_id: str,
        relation_type: str = "same_activity",
    ) -> None:
        now = _dt(utc_now())
        await self._execute(
            """
            INSERT INTO activity_links (
                task_id, meeting_id, relation_type, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(task_id, meeting_id) DO UPDATE SET
                relation_type = excluded.relation_type,
                updated_at = excluded.updated_at
            """,
            (task_id, meeting_id, relation_type, now, now),
        )

    async def meeting_ids_for_task(self, task_id: str) -> list[str]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT meeting_id FROM activity_links
                WHERE task_id = ? AND relation_type = 'same_activity'
                ORDER BY created_at
                """,
                (task_id,),
            )
        ).fetchall()
        return [row["meeting_id"] for row in rows]

    async def task_ids_for_meeting(self, meeting_id: str) -> list[str]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT task_id FROM activity_links
                WHERE meeting_id = ? AND relation_type = 'same_activity'
                ORDER BY created_at
                """,
                (meeting_id,),
            )
        ).fetchall()
        return [row["task_id"] for row in rows]

    async def linked_meeting_ids(self, task_ids: list[str]) -> set[str]:
        if not task_ids:
            return set()
        placeholders = ",".join("?" for _ in task_ids)
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                f"""
                SELECT meeting_id FROM activity_links
                WHERE relation_type = 'same_activity'
                  AND task_id IN ({placeholders})
                """,
                tuple(task_ids),
            )
        ).fetchall()
        return {row["meeting_id"] for row in rows}


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

    async def get_by_id(self, followup_id: str) -> FollowUp | None:
        conn = await self.database.connection()
        row = await (
            await conn.execute("SELECT * FROM followups WHERE id = ?", (followup_id,))
        ).fetchone()
        return _followup_from_row(row) if row else None

    async def list_open(self) -> list[FollowUp]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                "SELECT * FROM followups WHERE status = ? ORDER BY due_at IS NULL, due_at, created_at",
                (FollowUpStatus.OPEN.value,),
            )
        ).fetchall()
        return [_followup_from_row(row) for row in rows]

    async def update_due_at(self, followup_id: str, due_at: datetime | None) -> None:
        await self._execute(
            "UPDATE followups SET due_at = ?, updated_at = ? WHERE id = ?",
            (_dt(due_at), _dt(utc_now()), followup_id),
        )

    async def update_status(self, followup_id: str, status: FollowUpStatus) -> None:
        await self._execute(
            "UPDATE followups SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, _dt(utc_now()), followup_id),
        )

    async def list_by_note(self, note_id: str) -> list[FollowUp]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute("SELECT * FROM followups WHERE source_note_id = ?", (note_id,))
        ).fetchall()
        return [_followup_from_row(row) for row in rows]

    async def list_all(self) -> list[FollowUp]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                "SELECT * FROM followups ORDER BY updated_at DESC, created_at DESC"
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

    async def list_by_note(self, note_id: str) -> list[Commitment]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute("SELECT * FROM commitments WHERE source_note_id = ?", (note_id,))
        ).fetchall()
        return [_commitment_from_row(row) for row in rows]

    async def list_all(self) -> list[Commitment]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                "SELECT * FROM commitments ORDER BY updated_at DESC, created_at DESC"
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

    async def update_due_at(self, commitment_id: str, due_at: datetime | None) -> None:
        await self._execute(
            "UPDATE commitments SET due_at = ?, updated_at = ? WHERE id = ?",
            (_dt(due_at), _dt(utc_now()), commitment_id),
        )


class MemoryItemRepository(BaseRepository):
    async def create(self, item: MemoryItem) -> MemoryItem:
        await self._execute(
            """
            INSERT INTO memory_items (
                id, bucket, kind, content, source_note_id, project_id,
                person_id, organization_id, decision_id, confidence, status, source_type, observed_at, valid_from,
                valid_until, last_confirmed_at, sensitivity, revision_of_id,
                canonical_memory_id, conflict_state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.bucket.value,
                item.kind.value,
                item.content,
                item.source_note_id,
                item.project_id,
                item.person_id,
                item.organization_id,
                item.decision_id,
                item.confidence,
                item.status.value,
                item.source_type,
                _dt(item.observed_at),
                _dt(item.valid_from),
                _dt(item.valid_until),
                _dt(item.last_confirmed_at),
                item.sensitivity,
                item.revision_of_id,
                item.canonical_memory_id,
                item.conflict_state,
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

    async def get_by_id(self, item_id: str) -> MemoryItem | None:
        conn = await self.database.connection()
        row = await (
            await conn.execute("SELECT * FROM memory_items WHERE id = ?", (item_id,))
        ).fetchone()
        return _memory_from_row(row) if row else None

    async def confirm(self, item_id: str, confirmed_at: datetime) -> None:
        await self._execute(
            """
            UPDATE memory_items
            SET status = ?, last_confirmed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (MemoryStatus.ACTIVE.value, _dt(confirmed_at), _dt(utc_now()), item_id),
        )

    async def delete(self, item_id: str) -> None:
        await self._execute(
            "UPDATE memory_items SET revision_of_id = NULL WHERE revision_of_id = ?",
            (item_id,),
        )
        await self._execute("DELETE FROM memory_items WHERE id = ?", (item_id,))

    async def mark_conflict(self, item_ids: list[str]) -> None:
        for item_id in item_ids:
            await self._execute(
                "UPDATE memory_items SET conflict_state = 'conflict', updated_at = ? WHERE id = ?",
                (_dt(utc_now()), item_id),
            )

    async def select_canonical(self, canonical_id: str, related_ids: list[str]) -> None:
        await self._execute(
            """
            UPDATE memory_items
            SET status = ?, canonical_memory_id = NULL, conflict_state = 'resolved', updated_at = ?
            WHERE id = ?
            """,
            (MemoryStatus.ACTIVE.value, _dt(utc_now()), canonical_id),
        )
        for item_id in related_ids:
            if item_id == canonical_id:
                continue
            await self._execute(
                """
                UPDATE memory_items
                SET status = ?, canonical_memory_id = ?, conflict_state = 'resolved', updated_at = ?
                WHERE id = ?
                """,
                (MemoryStatus.SUPERSEDED.value, canonical_id, _dt(utc_now()), item_id),
            )


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

    async def list_pending(self, limit: int = 20) -> list[ClarificationRequest]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM clarification_requests
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (ClarificationStatus.PENDING.value, limit),
            )
        ).fetchall()
        return [_clarification_from_row(row) for row in rows]

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

    async def update_prompt(
        self,
        request_id: str,
        *,
        field_name: str,
        question: str,
    ) -> None:
        await self._execute(
            """
            UPDATE clarification_requests
            SET field_name = ?, question = ?, updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (
                field_name,
                question,
                _dt(utc_now()),
                request_id,
                ClarificationStatus.PENDING.value,
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

    async def get_by_id(self, event_id: str) -> EventLog | None:
        conn = await self.database.connection()
        row = await (
            await conn.execute("SELECT * FROM event_logs WHERE id = ?", (event_id,))
        ).fetchone()
        return _event_from_row(row) if row else None

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
        organization_id=row["organization_id"] if "organization_id" in row.keys() else None,
        decision_id=row["decision_id"] if "decision_id" in row.keys() else None,
        source_note_id=row["source_note_id"],
        confidence=row["confidence"],
        recurrence_rule=row["recurrence_rule"] if "recurrence_rule" in row.keys() else None,
        recurrence_series_id=(
            row["recurrence_series_id"] if "recurrence_series_id" in row.keys() else None
        ),
        recurrence_occurrence_at=(
            _parse_dt(row["recurrence_occurrence_at"])
            if "recurrence_occurrence_at" in row.keys()
            else None
        ),
        duration_minutes=(
            row["duration_minutes"] if "duration_minutes" in row.keys() else None
        ),
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
        aliases=_loads(row["aliases"]) if "aliases" in row.keys() else [],
        summary=row["summary"],
        status=row["status"],
        tags=_loads(row["tags"]),
        last_seen_at=_parse_dt(row["last_seen_at"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        project_type=ProjectType(row["project_type"]) if ("project_type" in row.keys() and row["project_type"]) else None,
        parent_project_id=row["parent_project_id"] if ("parent_project_id" in row.keys() and row["parent_project_id"]) else None,
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
        organization_id=(row["organization_id"] if "organization_id" in row.keys() else None),
        decision_id=row["decision_id"] if "decision_id" in row.keys() else None,
        confidence=row["confidence"],
        status=row["status"],
        source_type=row["source_type"] if "source_type" in row.keys() else "user_note",
        observed_at=_parse_dt(row["observed_at"]) if "observed_at" in row.keys() else None,
        valid_from=_parse_dt(row["valid_from"]) if "valid_from" in row.keys() else None,
        valid_until=_parse_dt(row["valid_until"]) if "valid_until" in row.keys() else None,
        last_confirmed_at=(
            _parse_dt(row["last_confirmed_at"])
            if "last_confirmed_at" in row.keys()
            else None
        ),
        sensitivity=row["sensitivity"] if "sensitivity" in row.keys() else "normal",
        revision_of_id=row["revision_of_id"] if "revision_of_id" in row.keys() else None,
        canonical_memory_id=(
            row["canonical_memory_id"] if "canonical_memory_id" in row.keys() else None
        ),
        conflict_state=row["conflict_state"] if "conflict_state" in row.keys() else "none",
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
