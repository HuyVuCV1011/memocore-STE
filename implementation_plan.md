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

## Delivery Principle

Build thin end-to-end secretary experiences. Each milestone must reduce administrative effort for
the user. Add infrastructure when a secretary behavior needs it, not as a prerequisite for
proving that behavior.

## Next Milestones

### S1: Conversation Loop

1. Classify Telegram messages as capture, question, instruction, correction, or casual
   conversation.
2. Add a conversation service with bounded recent context.
3. Answer simple questions from existing SQLite data.
4. Ask clarifying questions when required task, reminder, or meeting fields are missing.
5. Replace extraction-count replies with concise confirmations and useful follow-up questions.

### S2: Daily Secretary

1. Send an automatic morning briefing with due work, overdue work, reminders, open follow-ups,
   and upcoming meetings.
2. Add stale follow-up and approaching-deadline nudges.
3. Add a weekly review and an optional end-of-day review.
4. Bundle low-priority nudges and respect quiet hours.
5. Record whether suggestions were accepted, edited, ignored, or rejected.

### S3: People and Meeting Preparation

1. Link people, meetings, follow-ups, projects, and memory items.
2. Track what the user owes other people and what other people owe the user.
3. Generate a meeting preparation summary from previous notes, open commitments, and relevant
   project context.
4. Add structured SQLite retrieval by person, project, status, recency, and durability.
5. Add correction and supersession workflows for inaccurate memory.

### S4: Calendar Read Access

1. Add read-only calendar access for briefings and meeting preparation.
2. Detect schedule conflicts and suggest resolutions.
3. Store source references and privacy classifications for retrieved context.
4. Add a minimal registered-tool boundary and append-only run events for external reads.

### S5: Drafting and Approved Actions

1. Draft follow-up messages, email replies, and meeting agendas from stored context.
2. Add approval records and execution policy outcomes: allow, deny, and require approval.
3. Add approval-gated email sends and calendar writes with idempotency keys.
4. Add post-action verification and compensating actions where integrations support them.
5. Add bounded research and drafting workflows with explicit budgets and stop conditions.

### S6: Purposeful Memory and Learning

1. Learn language, tone, pronoun, notification, schedule, and communication preferences from
   explicit feedback and cautious observation.
2. Add evidence and revision history for durable memory.
3. Add full-text search when structured retrieval is insufficient.
4. Add PostgreSQL, pgvector hybrid retrieval, and a verified SQLite import command only when
   measured retrieval or concurrency needs justify them.
5. Add pattern recognition for recurring follow-up gaps and workload signals.

### S7: Productization and Trust

1. Add backup, restore, export, and privacy controls.
2. Add a web dashboard and mobile-friendly overview.
3. Add document, file, voice, and email ingestion.
4. Report administrative effort saved: forgotten commitments prevented, follow-ups surfaced,
   context prepared, and manual work avoided.

## Explicitly Deferred

- Separate graph database.
- Free-form autonomous execution.
- Multi-agent peer swarms.
- Broad dashboard UI before secretary workflows prove useful.
- Multi-user hosting infrastructure.
