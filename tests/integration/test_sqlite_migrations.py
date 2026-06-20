import sqlite3

from memocore.adapters.storage.sqlite import Database


async def test_initialize_adds_person_columns_before_creating_indexes(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                source_note_id TEXT,
                status TEXT,
                due_at TEXT
            );
            CREATE TABLE meetings (id TEXT PRIMARY KEY);
            CREATE TABLE memory_items (
                id TEXT PRIMARY KEY,
                bucket TEXT
            );
            """
        )

    database = Database(db_path)
    await database.initialize()
    conn = await database.connection()

    for table in ("tasks", "meetings", "memory_items"):
        columns = {
            row["name"] for row in await (await conn.execute(f"PRAGMA table_info({table})")).fetchall()
        }
        assert "person_id" in columns

    indexes = {
        row["name"]
        for row in await (
            await conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE '%_person%'"
            )
        ).fetchall()
    }
    assert {"idx_tasks_person", "idx_meetings_person", "idx_memory_items_person"} <= indexes
    project_columns = {
        row["name"]
        for row in await (await conn.execute("PRAGMA table_info(projects)")).fetchall()
    }
    memory_columns = {
        row["name"]
        for row in await (await conn.execute("PRAGMA table_info(memory_items)")).fetchall()
    }
    assert "aliases" in project_columns
    assert {
        "source_type",
        "observed_at",
        "valid_from",
        "valid_until",
        "last_confirmed_at",
        "sensitivity",
        "revision_of_id",
    } <= memory_columns
    await database.close()


async def test_initialize_normalizes_legacy_candidate_project_status(tmp_path):
    db_path = tmp_path / "legacy-project.db"
    database = Database(db_path)
    await database.initialize()
    conn = await database.connection()
    await conn.execute(
        """
        INSERT INTO projects (
            id, name, summary, status, tags, last_seen_at, created_at, updated_at
        ) VALUES ('legacy', 'Legacy', '', 'candidate', '[]', '2026-01-01', '2026-01-01', '2026-01-01')
        """
    )
    await conn.commit()
    await database.close()

    reopened = Database(db_path)
    await reopened.initialize()
    conn = await reopened.connection()
    row = await (await conn.execute("SELECT status FROM projects WHERE id = 'legacy'")).fetchone()

    assert row["status"] == "active"
    await reopened.close()
