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

## Next

### V1.6: Managed Memory

Evidence, revision history, correction workflows, structured retrieval, PostgreSQL runtime adapter, full-text search, pgvector hybrid retrieval, privacy classification.

### V2: Proactive Secretary

Daily briefing, weekly review, recurring reminders, meeting preparation, delegated-work tracking, stale-loop detection, user feedback signals.

### V2.5: Integrations

Calendar, email drafts, documents, files, and voice. Writes and sends require explicit approval policies.

### V3: Controlled Execution

Specialist workers for research, coding, and drafting. Centralized memory ownership, audit logs, retries, approvals, and rollback plans.

### V4: Productization

Backup, restore, import, export, privacy controls, installability, and optional multi-device deployment.

## Guardrails

- Do not collapse memory into raw chat history.
- Do not grant autonomy before observability and approval boundaries exist.
- Do not introduce graph infrastructure without retrieval evidence.
- Measure whether each milestone reduces real administrative effort.
