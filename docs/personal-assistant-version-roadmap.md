# Personal Assistant Version Roadmap

## Product Goal

Memocore should become a dependable personal secretary: it captures rough inputs, turns them into structured work, manages trustworthy memory, surfaces open loops, and later coordinates tools or specialist agents under clear approval boundaries.

## Version 1: Capture And Memory Foundation

Version 1 proves the local-first secretary foundation. It includes the original capture loop, provider reliability, operational reliability, basic secretary views, and the minimum memory hygiene needed before moving to conversational behavior.

### V1 Internal Phases

These are implementation checkpoints inside Version 1, not separate product versions:

- Foundation capture: Telegram capture, immutable raw notes, structured extraction, SQLite persistence, event logs.
- Provider reliability: Ollama plus OpenAI-compatible hosted providers, model profiles, external prompts, benchmark harness.
- Operational reliability: schema validation, fallback on invalid model output, transactional derived writes, idempotent capture, reminder leases, deterministic relative-date context.
- Secretary views: tasks, reminders, projects, memory, waiting and blocked work, follow-ups, meetings, and compact Telegram commands.
- Memory trust closeout: memory buckets, candidate lifecycle, correction/supersession basics, forget/redact support, and safeguards against obvious memory contamination.

### V1 Done Means

- A Telegram note can create linked notes, tasks, reminders, projects, and memory candidates.
- Single-shot reminders are scheduled and dispatched reliably.
- Existing data can be inspected through Telegram commands such as `/today`, `/tasks`, `/reminders`, `/projects`, `/memory`, and `/waiting`.
- Basic memory updates are manageable: bad memory can be rejected, forgotten, or superseded by an explicit correction.
- V1 does not silently treat obvious questions like durable memory.
- Raw input, interpreted memory, operational state, and audit events remain separate.

### V1 Explicitly Does Not Include

- Full natural-language conversation routing.
- Recurring reminders such as every week or every two hours.
- Automatic daily briefings.
- Deep people-aware meeting preparation.
- Calendar or email integration.
- Specialist-agent orchestration.
- Autonomous external actions.

## Version 2: Conversational Secretary

Version 2 turns Telegram from a capture-only surface into a conversational secretary interface.

Scope:

- Intent routing for capture, question, instruction, correction, clarification, and casual/no-op messages.
- Natural-language queries over existing SQLite data.
- Queries such as "hôm nay tôi cần làm gì", "tôi đã lưu gì về bản thân", and "project Memocore còn gì chưa xong".
- Better Vietnamese clarification and confirmation messages.
- Safer state transitions such as marking ambiguous work done only after clarification.
- No accidental task or memory creation from ordinary questions.

## Version 3: Daily And Recurring Secretary

Version 3 makes Memocore proactive in daily and weekly operation.

Scope:

- Recurring reminders: daily, weekly, weekday-specific, and interval-based reminders.
- Morning briefing, weekly review, optional end-of-day review.
- Stale-loop nudges, approaching-deadline warnings, and quiet hours.
- Better separation of upcoming, overdue, sent, and recurring reminders.
- Feedback signals for accepted, edited, ignored, or rejected suggestions.

## Version 4: People, Projects, And Meetings

Version 4 deepens operational context around people and work.

Scope:

- People profiles and relationship-aware commitments.
- What the user owes others and what others owe the user.
- Meeting preparation summaries from previous notes, open commitments, and project context.
- Project snapshots, decision logs, and structured retrieval by person, project, status, recency, and durability.
- Richer follow-up workflows by person and project.

## Version 5: Orchestration And Specialist Agents

Version 5 introduces a supervisor layer that can delegate bounded work to specialist workers while preserving central memory ownership.

Scope:

- Supervisor service and structured worker handoffs.
- Specialist workers for repository analysis, research, drafting, documents, or browsing.
- Execution logging, retries, timeouts, fallback, and verification.
- Approval boundaries before high-impact or external actions.
- Workers can propose memory updates, but the main assistant decides what becomes durable.

## Version 6: Knowledge System And Productization

Version 6 turns the prototype into a more portable and shareable personal assistant system.

Scope:

- Richer entity modeling for people, projects, topics, decisions, and recurring concepts.
- Graph-like relationships only when they improve measured retrieval value.
- Backup, restore, import, export, privacy controls, and setup flows.
- Web dashboard or mobile-friendly overview once core secretary workflows are proven.
- PostgreSQL and pgvector only when measured retrieval, concurrency, or backup needs justify them.

## Version 7: Controlled Autonomy

Version 7 allows bounded action while maintaining user trust.

Scope:

- Draft messages, emails, agendas, and documents.
- Approval-gated email sends and calendar writes.
- Policy outcomes: allow, deny, require approval.
- Idempotency keys, audit records, post-action verification, and compensating actions.
- Evaluation harnesses for memory, planning, retrieval, and action quality.

## Cross-Version Guardrails

- Do not collapse memory into raw chat history.
- Do not treat model guesses as durable facts without lifecycle controls.
- Do not add autonomy before observability, policy, and approval boundaries exist.
- Do not add graph or orchestration infrastructure before a secretary workflow needs it.
- Measure whether each version reduces real administrative effort, not just stored-item counts.
