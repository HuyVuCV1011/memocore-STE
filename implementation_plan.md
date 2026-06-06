# MemoCore Implementation Plan

## Goal

Build a local-first personal secretary that captures rough notes, remembers durable context, tracks commitments, and proactively surfaces open loops. Telegram is the first interface, not the product boundary.

## Version Plan

### V1: Capture And Memory Foundation

V1 includes the original capture loop, reliability work, secretary views, and the memory-trust closeout needed before conversational behavior.

Delivered foundations:

- Telegram capture with immutable raw notes.
- Structured extraction for tasks, reminders, projects, and memory candidates.
- SQLite persistence and event logs.
- Reminder scheduling and delivery.
- Provider-agnostic `ModelProvider.chat()` contract.
- Ollama and OpenAI-compatible providers.
- Configuration-driven provider factory and optional fallback.
- External prompt files and extraction benchmark.
- Hosted providers use provider-specific default URLs.
- Invalid JSON and schema validation failures trigger fallback providers.
- Relative weekday dates are computed in Python.
- Telegram capture is idempotent by source message.
- Derived writes are transactional.
- Reminder dispatch claims work with a lease before sending.
- Likely incomplete extractions generate audit events.
- First-class meeting and follow-up storage.
- Task states include waiting and blocked work.
- Memory candidates support activation and supersession.
- Telegram commands: `/today`, `/waiting`, `/projects`, `/memory`.
- PostgreSQL and pgvector migration blueprint for long-distance storage.

V1 closeout focus:

- Keep obvious questions and commands from contaminating memory.
- Support simple memory rejection, supersession, forgetting, and redaction.
- Keep task/reminder/project views clear enough for manual use.
- Preserve raw inputs separately from interpreted memory and operational state.

## Delivery Principle

Build thin end-to-end secretary experiences. Each milestone must reduce administrative effort for
the user. Add infrastructure when a secretary behavior needs it, not as a prerequisite for
proving that behavior.

## Delivered V2: Conversational Secretary

V2 turns Telegram from a capture-only surface into a conversational secretary interface.

Delivered foundations:

- Deterministic and model-assisted intent routing for capture, question, instruction, correction, clarification, and casual/no-op messages.
- Natural-language queries over existing SQLite data for agendas, tasks, reminders, projects, and memory.
- Safer task completion, deadline updates, bulk task cancellation, memory deletion, and correction flows.
- Clarification requests for ambiguous task selection and weak matches.
- Audit events for conversation notes, user feedback, and conversation-created clarifications.
- Guardrails so low-confidence or ambiguous write intents ask for clarification instead of creating or mutating durable objects.

## Next Versions

### V3: Daily And Recurring Secretary

1. Send an automatic morning briefing with due work, overdue work, reminders, open follow-ups,
   and upcoming meetings.
2. Add stale follow-up and approaching-deadline nudges.
3. Add a weekly review and an optional end-of-day review.
4. Bundle low-priority nudges and respect quiet hours.
5. Record whether suggestions were accepted, edited, ignored, or rejected.

### V4: People, Projects, And Meetings

1. Link people, meetings, follow-ups, projects, and memory items.
2. Track what the user owes other people and what other people owe the user.
3. Generate a meeting preparation summary from previous notes, open commitments, and relevant
   project context.
4. Add structured SQLite retrieval by person, project, status, recency, and durability.
5. Add correction and supersession workflows for inaccurate memory.

### V5: Orchestration And Specialist Agents

1. Add a supervisor layer for bounded delegation to specialist workers.
2. Define worker roles for coding, research, drafting, document, and browsing tasks.
3. Add structured handoff payloads, execution logs, retries, timeouts, fallback, and verification.
4. Keep durable memory ownership centralized in the main assistant.
5. Require approval boundaries for high-impact or external actions.

### V6: Knowledge System And Productization

1. Add richer entity modeling for people, projects, topics, decisions, and recurring concepts.
2. Add graph-like relationships only when measured retrieval value justifies them.
3. Add backup, restore, export, privacy controls, installability, and setup flows.
4. Add a web dashboard or mobile-friendly overview after core secretary workflows are proven.
5. Add PostgreSQL and pgvector only when retrieval, concurrency, or backup needs justify them.

### V7: Controlled Autonomy

1. Draft follow-up messages, email replies, and meeting agendas from stored context.
2. Add approval records and execution policy outcomes: allow, deny, and require approval.
3. Add approval-gated email sends and calendar writes with idempotency keys.
4. Add post-action verification and compensating actions where integrations support them.
5. Add bounded research and drafting workflows with explicit budgets and stop conditions.

## Explicitly Deferred

- Separate graph database.
- Free-form autonomous execution.
- Multi-agent peer swarms.
- Broad dashboard UI before secretary workflows prove useful.
- Multi-user hosting infrastructure.
