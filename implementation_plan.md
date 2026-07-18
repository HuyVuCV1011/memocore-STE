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
- Telegram command handlers for daily work, waiting items, projects, and memory.
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

## Delivered V3: Daily And Recurring Secretary

V3 makes MemoCore proactive in the local Telegram runtime.

Delivered foundations:

- Manual `/briefing` and `/weekly` secretary views.
- Automatic morning briefing, sent once per chat per day.
- Automatic weekly review, sent once per chat per configured weekday.
- Recurring reminder support for daily and weekday-specific weekly reminders.
- Recurring reminders are rescheduled after successful delivery.
- Proactive nudges for overdue tasks and stale or overdue follow-ups.
- Quiet hours and per-entity cooldowns for proactive nudges.
- Event logs for briefing, weekly review, nudge, reminder sent, and reminder reschedule events.

Deferred from V3 but now handled in V4:

- Pre-deadline warnings before a task becomes overdue.
- Bundling many due nudges into one digest with a per-run limit.
- Natural-language reminder snooze for existing reminders.

## Active V4: Trustworthy Daily Secretary

V4 deepens operational context without importing private datasets automatically.

Delivered foundations:

- SQLite/domain support for linked `person_id` on tasks, meetings, and memory items.
- `Commitment` domain model and repository for `user_owes`, `owed_to_user`, and `mutual` commitments.
- `meeting_people` links for meeting participants beyond the primary person field.
- Repository retrieval by person, project, status, due date, and recency for tasks, meetings,
  follow-ups, commitments, and memory.
- Secretary views for `/people`, `/commitments`, `/person <name>`, `/project <name>`,
  `/context <person-or-project>`, and `/prep <person-or-project>`.
- Meeting preparation summaries assembled from linked commitments, tasks, follow-ups, meetings,
  and memory.
- V4 intent routing for people, commitments, context, and meeting-prep queries without turning
  them into captured notes.
- Structured capture ingestion for explicitly named people, meetings, follow-ups, and directional
  commitments, with safe person/project linking.
- Ambiguous or low-confidence V4 operational candidates are skipped with audit warnings instead
  of silently mutating durable state.
- Failed transactional derived writes can be retried using the same source message id.
- Compact visible Telegram menu: `/today`, `/work`, `/context`, `/search`, and `/review`;
  existing specialist commands such as `/briefing`, `/memory`, and `/capture` remain available
  as hidden shortcuts.
- Inline hubs for work, context, capture, and high-volume memory navigation.
- Ranked open-loop views, including top priorities, overdue work, waiting items, commitments, and
  goal-aware daily selection.
- Evidence metadata in person, project, context, meeting-preparation, and memory review views.
- Review-gated person/project alias decisions without automatic silent merges.
- Cross-domain `/search` and natural timeline/origin/decision queries over notes, tasks,
  reminders, meetings, follow-ups, commitments, memory, decisions, and audit events.
- Pre-deadline task warnings plus bundled/limited proactive nudge delivery with per-item audit
  events.
- Reminder snooze through conversation, including contextual phrases like "nhắc lại chiều mai".
- Memory review/stale/topic slices with pagination and audited keep/reject/stale/supersede actions.
- Structured accepted, edited, rejected, ignored, and correction feedback linked to the originating
  turn reference and affected artifact, with resolvable quality-review items.
- Verified SQLite backup, restore, and export as a V4 data-safety gate before wider productization.
- Interval recurrence for scheduled work, using structured rules such as `interval:2d` and
  `interval:3w`.
- Morning briefing, end-of-day review, weekly review, and lightweight goal tracking.
- Runtime preflight through `memocore doctor` before PM2 restart.

Deferred from V4:

- Automatic bulk import of personal Markdown/Telegram exports into the live database.
- Dedicated review UI for approving personal context seed items.
- Cross-entity graph database; SQLite links are enough for the current secretary workflows.

V4 remains the active product track. Real Telegram usage and memory/entity review quality should be
validated before orchestration work starts.

Current stabilization work adds explicit Router, Planner, Context Resolver, Executor, and Composer
boundaries; first-class organization and decision records; reversible knowledge-update batches;
and fixture-driven transcript evaluation. Calendar, email, tools, and multi-agent work remain held
until `docs/conversation-stability-gates.md` passes.

The task-mutation stabilization layer now includes one resolver for numbered, listed, temporal, and
title references; privacy-safe resolution metrics; previewed and snapshot-validated batch
completion; recurrence-backlog choices; and guarded partial batch undo.

## Held Versions

### V5: Orchestration And Specialist Agents

V5 is intentionally postponed. When resumed, the first `0.5.0` milestone should prove the audited
execution boundary with one read-only workflow before specialist workers are introduced.

1. Add typed harness runs, registered tools, tool calls, execution policy, and append-only events.
2. Add SQLite persistence, validation, timeout, failure, fallback, and audit coverage.
3. Integrate one read-only calendar tool into meeting preparation.
4. Keep durable memory ownership centralized in the main assistant.
5. Add specialist worker roles only after the read-only harness acceptance gates pass.
6. Require approval boundaries before any high-impact or external write action.

See `docs/version-0.5-readiness.md` for the verified baseline and release gates.

### V6: Knowledge System And Productization

1. Add richer entity modeling for people, projects, topics, decisions, and recurring concepts.
2. Add graph-like relationships only when measured retrieval value justifies them.
3. Add privacy controls, installability, setup flows, and packaging polish after V4 recovery proves reliable.
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
