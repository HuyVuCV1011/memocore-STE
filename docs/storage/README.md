# Storage Migrations

SQLite is the verified local runtime. The schema bootstrap in `src/memocore/adapters/storage/sqlite.py` upgrades existing local databases automatically for V1.5 columns.

`postgres/001_secretary_foundation.sql` is the reviewed PostgreSQL and pgvector blueprint for the long-distance backend. It is intentionally not wired into runtime configuration yet: the next infrastructure milestone is a PostgreSQL adapter, import command, and server-backed contract tests.

Do not claim PostgreSQL production support until those tests pass against a real PostgreSQL instance.
