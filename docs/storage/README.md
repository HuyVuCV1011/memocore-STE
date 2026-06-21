# Storage Migrations

SQLite is the verified local runtime. The schema bootstrap in `src/memocore/adapters/storage/sqlite.py`
upgrades existing databases and records packaged migrations in `schema_migrations`.

| Migration | Purpose |
| --- | --- |
| `001_clarification_requests.sql` | Persistent clarification workflow. |
| `002_task_recurrence_and_list_context.sql` | Recurring task state and numbered task references. |
| `003_conversation_context.sql` | Canonical chat focus and resolved conversation turns. |
| `004_knowledge_entities.sql` | Organizations, decisions, and linked knowledge evidence. |

`postgres/001_secretary_foundation.sql` is the reviewed PostgreSQL and pgvector blueprint for the long-distance backend. It is intentionally not wired into runtime configuration yet. Keep SQLite as the verified runtime while secretary workflows are being proven. Add a PostgreSQL adapter, import command, and server-backed contract tests when measured retrieval, concurrency, backup, or deployment needs justify the migration.

Do not claim PostgreSQL production support until those tests pass against a real PostgreSQL instance.
