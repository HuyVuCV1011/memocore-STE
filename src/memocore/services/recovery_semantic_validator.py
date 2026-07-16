from __future__ import annotations

from datetime import datetime
import sqlite3

from memocore.domain.knowledge import (
    DecisionStatus,
    KnowledgeEntityType,
    KnowledgeRelationStatus,
)
from memocore.domain.models import (
    ClarificationStatus,
    CommitmentDirection,
    CommitmentStatus,
    EventType,
    FollowUpStatus,
    MemoryBucket,
    MemoryKind,
    MemoryStatus,
    NoteStatus,
    ProjectStatus,
    ProjectType,
    ReminderStatus,
    TaskStatus,
)


REQUIRED_TABLES = (
    "notes",
    "projects",
    "people",
    "tasks",
    "reminders",
    "memory_items",
    "followups",
    "commitments",
    "meetings",
    "decisions",
    "event_logs",
)
REQUIRED_COLUMNS = {
    "notes": {"id", "raw_text", "tags", "metadata"},
    "tasks": {"id", "source_note_id", "project_id", "person_id", "recurrence_series_id"},
    "memory_items": {"id", "source_note_id", "project_id", "person_id", "status"},
    "meetings": {"id", "source_note_id", "project_id", "person_id"},
    "commitments": {"id", "direction", "status", "person_id", "project_id"},
}


def semantic_database_check(
    database_path, *, packaged_migrations: set[str]
) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError("SQLite integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Database contains invalid entity links")
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing = sorted(set(REQUIRED_TABLES) - tables)
        if missing:
            raise RuntimeError(f"Database missing required tables: {', '.join(missing)}")
        applied = {
            row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        if applied != packaged_migrations:
            raise RuntimeError("Database migration ledger is not application-compatible")
        for table, expected in REQUIRED_COLUMNS.items():
            columns = {
                row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            missing_columns = sorted(expected - columns)
            if missing_columns:
                raise RuntimeError(
                    f"Database missing required {table} fields: {', '.join(missing_columns)}"
                )
        _assert_domain_invariants(conn, tables)
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in REQUIRED_TABLES
        }
    finally:
        conn.close()


def _assert_domain_invariants(conn: sqlite3.Connection, tables: set[str]) -> None:
    allowed = {
        "notes.status": {item.value for item in NoteStatus},
        "tasks.status": {item.value for item in TaskStatus},
        "tasks.priority": {"low", "medium", "high"},
        "reminders.status": {item.value for item in ReminderStatus},
        "followups.status": {item.value for item in FollowUpStatus},
        "commitments.status": {item.value for item in CommitmentStatus},
        "commitments.direction": {item.value for item in CommitmentDirection},
        "projects.status": {item.value for item in ProjectStatus},
        "projects.project_type": {item.value for item in ProjectType},
        "memory_items.bucket": {item.value for item in MemoryBucket},
        "memory_items.kind": {item.value for item in MemoryKind},
        "memory_items.status": {item.value for item in MemoryStatus},
        "memory_items.conflict_state": {"none", "conflict", "resolved"},
        "decisions.status": {item.value for item in DecisionStatus},
        "knowledge_relations.source_type": {item.value for item in KnowledgeEntityType},
        "knowledge_relations.target_type": {item.value for item in KnowledgeEntityType},
        "knowledge_relations.status": {item.value for item in KnowledgeRelationStatus},
        "clarification_requests.status": {item.value for item in ClarificationStatus},
        "event_logs.event_type": {item.value for item in EventType},
    }
    for field, values in allowed.items():
        table, column = field.split(".")
        placeholders = ",".join("?" for _ in values)
        invalid = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL "
            f"AND {column} NOT IN ({placeholders})",
            tuple(sorted(values)),
        ).fetchone()[0]
        if invalid:
            raise RuntimeError(f"Database contains invalid {field} values")
    json_fields = {
        "notes": {"tags": "array", "metadata": "object"},
        "projects": {"aliases": "array", "tags": "array"},
        "people": {"aliases": "array"},
        "organizations": {"aliases": "array", "tags": "array"},
        "event_logs": {"payload": "object"},
        "task_list_contexts": {"task_ids": "array"},
        "chat_contexts": {"last_result_entity_ids": "array"},
        "conversation_turns": {"result_entity_ids": "array", "plan_json": "object"},
    }
    for table, fields in json_fields.items():
        for field, expected_type in fields.items():
            invalid = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE CASE "
                f"WHEN json_valid({field}) THEN json_type({field}) != ? ELSE 1 END",
                (expected_type,),
            ).fetchone()[0]
            if invalid:
                raise RuntimeError(f"Database contains invalid {table}.{field} JSON")
    for table in tables:
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if "confidence" in columns:
            invalid = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE confidence IS NOT NULL "
                "AND (confidence < 0 OR confidence > 1)"
            ).fetchone()[0]
            if invalid:
                raise RuntimeError(f"Database contains invalid {table}.confidence values")
        timestamp_fields = [
            column
            for column in columns
            if column in {"created_at", "updated_at", "decided_at"}
            or column.endswith("_at")
        ]
        for field in timestamp_fields:
            for (value,) in conn.execute(
                f"SELECT {field} FROM {table} WHERE {field} IS NOT NULL"
            ).fetchall():
                try:
                    datetime.fromisoformat(value)
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"Database contains invalid {table}.{field} timestamps"
                    ) from exc
