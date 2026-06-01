# Memocore Implementation Plan

## Goal

Build a local-first personal secretary that captures rough notes, remembers durable context, tracks commitments, and proactively surfaces open loops. Telegram is the first interface, not the product boundary.

## Delivered

### V1: Capture Loop

- Telegram capture with immutable raw notes.
- Structured extraction for tasks, reminders, projects, and memory candidates.
- SQLite persistence and event logs.
- Reminder scheduling and delivery.

### V1.1: Extraction Reliability

- Provider-agnostic `ModelProvider.chat()` contract.
- Ollama and OpenAI-compatible providers.
- Configuration-driven provider factory and optional fallback.
- External prompt files and extraction benchmark.

### V1.2: Reliability Foundation

- Hosted providers use provider-specific default URLs.
- Invalid JSON and schema validation failures trigger fallback providers.
- Relative weekday dates are computed in Python.
- Telegram capture is idempotent by source message.
- Derived writes are transactional.
- Reminder dispatch claims work with a lease before sending.
- Likely incomplete extractions generate audit events.

### V1.5: Secretary Foundation

- First-class meeting and follow-up storage.
- Task states include waiting and blocked work.
- Memory candidates support activation and supersession.
- Telegram commands: `/today`, `/waiting`, `/projects`, `/memory`.
- PostgreSQL and pgvector migration blueprint for long-distance storage.

## Next Milestones

### V1.6: Managed Memory Retrieval

1. Add memory evidence and revision tables to the runtime repositories.
2. Add structured retrieval by person, project, status, recency, and durability.
3. Add PostgreSQL runtime adapter and verified SQLite import command.
4. Add full-text and pgvector hybrid retrieval.
5. Add privacy classification for hosted model calls.

### V2: Proactive Secretary

1. Daily briefing and weekly review.
2. Waiting-for and delegated-work tracking.
3. Meeting preparation and recurring reminders.
4. Feedback signals for accepted, edited, ignored, and rejected suggestions.

### V2.5: Integrations

1. Calendar read access, then approval-gated writes.
2. Email read and draft workflows, then approval-gated sends.
3. Document, file, and voice ingestion.

### V3: Controlled Execution

1. Specialist workers for research, coding, and drafting.
2. Approval records, execution logs, retries, and rollback plans.
3. Centralized memory ownership in the secretary service.

## Explicitly Deferred

- Separate graph database.
- Free-form autonomous execution.
- Multi-agent peer swarms.
- Broad dashboard UI.
- Multi-user hosting infrastructure.
