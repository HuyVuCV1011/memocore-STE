from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from importlib.resources import files

import aiosqlite


class Database:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._connection: aiosqlite.Connection | None = None
        self._transaction_depth = 0

    async def initialize(self) -> None:
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await self.connection()
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.executescript(MIGRATION_LEDGER_SCHEMA)
        await conn.executescript(SCHEMA)
        await self._upgrade_existing_schema(conn)
        await self._normalize_legacy_values(conn)
        await conn.executescript(POST_UPGRADE_INDEXES)
        await self._apply_migrations(conn)
        await conn.commit()

    async def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            self._connection = await aiosqlite.connect(self.db_path)
            self._connection.row_factory = aiosqlite.Row
        return self._connection

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def _upgrade_existing_schema(self, conn: aiosqlite.Connection) -> None:
        rows = await (await conn.execute("PRAGMA table_info(reminders)")).fetchall()
        columns = {row["name"] for row in rows}
        if "attempt_count" not in columns:
            await conn.execute("ALTER TABLE reminders ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0")
        if "claimed_at" not in columns:
            await conn.execute("ALTER TABLE reminders ADD COLUMN claimed_at TEXT")
        if "recurrence_rule" not in columns:
            await conn.execute("ALTER TABLE reminders ADD COLUMN recurrence_rule TEXT")
        await self._ensure_column(conn, "tasks", "person_id", "TEXT REFERENCES people(id)")
        await self._ensure_column(conn, "tasks", "recurrence_rule", "TEXT")
        await self._ensure_column(conn, "tasks", "recurrence_series_id", "TEXT")
        await self._ensure_column(conn, "tasks", "recurrence_occurrence_at", "TEXT")
        await self._ensure_column(conn, "projects", "aliases", "TEXT NOT NULL DEFAULT '[]'")
        await self._ensure_column(conn, "meetings", "person_id", "TEXT REFERENCES people(id)")
        await self._ensure_column(conn, "memory_items", "person_id", "TEXT REFERENCES people(id)")
        await self._ensure_column(conn, "memory_items", "source_type", "TEXT NOT NULL DEFAULT 'user_note'")
        await self._ensure_column(conn, "memory_items", "observed_at", "TEXT")
        await self._ensure_column(conn, "memory_items", "valid_from", "TEXT")
        await self._ensure_column(conn, "memory_items", "valid_until", "TEXT")
        await self._ensure_column(conn, "memory_items", "last_confirmed_at", "TEXT")
        await self._ensure_column(conn, "memory_items", "sensitivity", "TEXT NOT NULL DEFAULT 'normal'")
        await self._ensure_column(
            conn,
            "memory_items",
            "revision_of_id",
            "TEXT REFERENCES memory_items(id)",
        )

    async def _ensure_column(
        self,
        conn: aiosqlite.Connection,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        rows = await (await conn.execute(f"PRAGMA table_info({table_name})")).fetchall()
        columns = {row["name"] for row in rows}
        if column_name not in columns:
            await conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    async def _apply_migrations(self, conn: aiosqlite.Connection) -> None:
        migration_dir = files("memocore.adapters.storage").joinpath("migrations/sqlite")
        for migration in sorted(migration_dir.iterdir(), key=lambda path: path.name):
            if not migration.name.endswith(".sql"):
                continue
            applied = await (
                await conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?",
                    (migration.name,),
                )
            ).fetchone()
            if applied:
                continue
            await conn.executescript(migration.read_text(encoding="utf-8"))
            await conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
                (migration.name,),
            )

    async def _normalize_legacy_values(self, conn: aiosqlite.Connection) -> None:
        await conn.execute("UPDATE projects SET status = 'active' WHERE status = 'candidate'")

    async def commit_if_needed(self) -> None:
        if self._transaction_depth == 0:
            await (await self.connection()).commit()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        conn = await self.connection()
        outermost = self._transaction_depth == 0
        if outermost:
            await conn.execute("BEGIN")
        self._transaction_depth += 1
        try:
            yield
        except Exception:
            self._transaction_depth -= 1
            if outermost:
                await conn.rollback()
            raise
        else:
            self._transaction_depth -= 1
            if outermost:
                await conn.commit()


MIGRATION_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_message_id TEXT,
    source_chat_id TEXT,
    raw_text TEXT NOT NULL,
    summary TEXT NOT NULL,
    tags TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    aliases TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    tags TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    due_at TEXT,
    project_id TEXT REFERENCES projects(id),
    person_id TEXT REFERENCES people(id),
    source_note_id TEXT NOT NULL REFERENCES notes(id),
    confidence REAL NOT NULL,
    recurrence_rule TEXT,
    recurrence_series_id TEXT,
    recurrence_occurrence_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    remind_at TEXT,
    status TEXT NOT NULL,
    task_id TEXT REFERENCES tasks(id),
    source_note_id TEXT NOT NULL REFERENCES notes(id),
    delivery_channel TEXT NOT NULL,
    confidence REAL NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    recurrence_rule TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    aliases TEXT NOT NULL,
    relationship TEXT NOT NULL,
    notes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    starts_at TEXT,
    ends_at TEXT,
    project_id TEXT REFERENCES projects(id),
    person_id TEXT REFERENCES people(id),
    source_note_id TEXT NOT NULL REFERENCES notes(id),
    notes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS followups (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    due_at TEXT,
    status TEXT NOT NULL,
    person_id TEXT REFERENCES people(id),
    project_id TEXT REFERENCES projects(id),
    source_note_id TEXT REFERENCES notes(id),
    notes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commitments (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    direction TEXT NOT NULL,
    status TEXT NOT NULL,
    person_id TEXT REFERENCES people(id),
    project_id TEXT REFERENCES projects(id),
    due_at TEXT,
    source_note_id TEXT REFERENCES notes(id),
    notes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meeting_people (
    meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (meeting_id, person_id)
);

CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    bucket TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    source_note_id TEXT NOT NULL REFERENCES notes(id),
    project_id TEXT REFERENCES projects(id),
    person_id TEXT REFERENCES people(id),
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'user_note',
    observed_at TEXT,
    valid_from TEXT,
    valid_until TEXT,
    last_confirmed_at TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    revision_of_id TEXT REFERENCES memory_items(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_logs (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_source_note_id ON tasks(source_note_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_at);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, remind_at);
CREATE INDEX IF NOT EXISTS idx_reminders_source_note_id ON reminders(source_note_id);
CREATE INDEX IF NOT EXISTS idx_memory_items_bucket ON memory_items(bucket);
CREATE INDEX IF NOT EXISTS idx_event_logs_entity ON event_logs(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_followups_status_due ON followups(status, due_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notes_source_message
ON notes(source, source_chat_id, source_message_id)
WHERE source_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS task_list_contexts (
    source_chat_id TEXT PRIMARY KEY,
    task_ids TEXT NOT NULL,
    source_view TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_contexts (
    source_chat_id TEXT PRIMARY KEY,
    focused_entity_type TEXT,
    focused_entity_id TEXT,
    last_intent TEXT,
    last_result_entity_ids TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id TEXT PRIMARY KEY,
    source_chat_id TEXT NOT NULL,
    source_message_id TEXT,
    raw_text TEXT NOT NULL,
    intent TEXT NOT NULL,
    focused_entity_type TEXT,
    focused_entity_id TEXT,
    result_entity_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
"""


POST_UPGRADE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_tasks_person ON tasks(person_id);
CREATE INDEX IF NOT EXISTS idx_meetings_person ON meetings(person_id);
CREATE INDEX IF NOT EXISTS idx_memory_items_person ON memory_items(person_id);
CREATE INDEX IF NOT EXISTS idx_commitments_person_status ON commitments(person_id, status);
CREATE INDEX IF NOT EXISTS idx_commitments_project_status ON commitments(project_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_recurrence_occurrence
ON tasks(recurrence_series_id, recurrence_occurrence_at)
WHERE recurrence_series_id IS NOT NULL AND recurrence_occurrence_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_conversation_turns_chat_created
ON conversation_turns(source_chat_id, created_at);
"""
