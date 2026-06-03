# Personal Secretary Roadmap

## Product Goal

Memocore should behave like a dependable secretary: capture information, remember evidence-backed context, manage commitments, surface open loops, prepare follow-ups, and execute bounded actions with approval.

## Delivered

### V1: Capture

Telegram capture, structured extraction, SQLite storage, reminders, events.

### V1.1: Provider Reliability

Provider abstraction, Ollama and hosted OpenAI-compatible APIs, external prompts, benchmark harness.

### V1.2: Operational Reliability

Validation-aware fallback, deterministic date context, capture idempotency, transactional derived writes, reminder leases, extraction-quality events.

### V1.5: Secretary Foundation

Waiting and blocked work, meetings, follow-ups, memory lifecycle hooks, secretary summary commands, PostgreSQL migration blueprint.

## Delivery Principle

Organize work around visible secretary capabilities. Build the smallest retrieval, policy, and
audit support needed for each capability, then expand those foundations as real usage demands.

## Next

### S1: Conversation Loop

Natural Telegram dialogue, message intent classification, bounded recent context, answers from
existing SQLite data, clarifying questions, and human-readable confirmations.

### S2: Daily Secretary

Automatic morning briefing, stale-loop nudges, approaching-deadline warnings, weekly review,
optional end-of-day review, quiet hours, and feedback signals.

### S3: People and Meeting Preparation

Relationship-aware commitments, people and project links, meeting preparation summaries,
structured SQLite retrieval, and memory correction workflows.

### S4: Calendar Read Access

Calendar-informed briefings, schedule conflict detection, privacy classification, source
references, and a minimal audited tool boundary for external reads.

### S5: Drafting and Approved Actions

Communication drafts, agenda preparation, explicit approvals, audit records, idempotent calendar
writes and email sends, and bounded research workflows.

### S6: Purposeful Memory and Learning

Preference learning, evidence-backed revisions, pattern recognition, full-text search when
needed, and PostgreSQL or pgvector only when measured retrieval or concurrency needs justify
them.

### S7: Productization and Trust

Backup, restore, import, export, privacy controls, web and mobile-friendly views, additional
ingestion surfaces, and reports of administrative effort saved.

## Guardrails

- Do not collapse memory into raw chat history.
- Do not grant autonomy before observability and approval boundaries exist.
- Do not introduce graph infrastructure without retrieval evidence.
- Do not build generalized infrastructure before a secretary workflow needs it.
- Measure whether each milestone reduces real administrative effort.
