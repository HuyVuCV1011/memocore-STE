from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

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
        await conn.executescript(SCHEMA)
        await self._upgrade_existing_schema(conn)
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
    source_note_id TEXT NOT NULL REFERENCES notes(id),
    confidence REAL NOT NULL,
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

CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    bucket TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    source_note_id TEXT NOT NULL REFERENCES notes(id),
    project_id TEXT REFERENCES projects(id),
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
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
"""
