# Personal Assistant Roadmap

## Product Goal

MemoCore should become a dependable local-first personal secretary: capture rough inputs, preserve
evidence, manage trustworthy memory, surface open loops, and later coordinate tools under explicit
approval boundaries.

## Version Status

| Version | Status | Theme |
| --- | --- | --- |
| V1 | Delivered | Capture and memory foundation |
| V2 | Delivered | Conversational secretary |
| V3 | Delivered | Daily and recurring secretary |
| V4 | Active | Trustworthy daily secretary workflows |
| V5 | Held | Orchestration and specialist workers |
| V6 | Future | Knowledge system and productization |
| V7 | Future | Controlled autonomy |

## V1: Capture And Memory Foundation

V1 proves the local-first secretary foundation:

- Telegram capture and immutable raw notes.
- Structured extraction into notes, tasks, reminders, projects, people, meetings, follow-ups,
  memory items, and event logs.
- SQLite local runtime.
- Provider abstraction and provider switching.
- Schema validation, fallback, transactional writes, idempotent capture, and reminder leases.
- Basic secretary commands for today, tasks, reminders, projects, memory, and waiting work.
- Memory lifecycle basics: candidate, active, rejected, superseded, delete, and forget.

## V2: Conversational Secretary

V2 turns Telegram from a capture-only surface into a conversational secretary interface:

- Deterministic and model-assisted intent routing.
- Natural-language SQLite queries for agendas, tasks, reminders, projects, and memory.
- Clarification for ambiguous task selection and weak matches.
- Safer task completion, deadline updates, bulk cancellation, memory deletion, and correction
  flows.
- Guardrails to prevent ordinary questions from becoming durable tasks or memory.

## V3: Daily And Recurring Secretary

V3 makes MemoCore proactive:

- Recurring reminders: daily and weekday-specific weekly reminders.
- Morning briefing and weekly review.
- Stale-loop and overdue-work nudges.
- Quiet hours and nudge cooldowns.
- Audit events for proactive sends and recurring reminder reschedules.

Delivered V3 scope includes manual and scheduled briefings, weekly reviews, daily/weekly recurring
reminders, stale-loop nudges, quiet hours, cooldowns, audit events, and an end-of-day review.
Interval recurrence, bundled nudge digests, explicit suggestion feedback signals, and pre-deadline
warnings are intentionally deferred.

## V4: People, Projects, And Meetings

V4 deepens operational context:

- People profiles and relationship-aware commitments.
- What the user owes others and what others owe the user.
- Meeting preparation summaries from previous notes, open commitments, and project context.
- Project snapshots, decision logs, and retrieval by person, project, status, recency, and
  durability.

Delivered V4 scope includes people aliases, linked person/project ids on operational records,
commitments with direction, meeting participant links, `/people`, `/commitments`, `/person`,
`/project`, `/context`, and `/prep` secretary views, and structured SQLite retrieval for meeting
preparation. Capture also ingests explicitly evidenced people, meetings, follow-ups, and
directional commitments while skipping ambiguous durable writes. Bulk personal-context import
remains review-gated and separate from live database writes.

The active V4 deepening track adds:

- A compact six-command Telegram menu with inline hubs and hidden power-user shortcuts.
- Ranked `/today`, `/work`, `/briefing`, and `/weekly` views for priorities and open loops.
- Evidence-backed person, project, context, and meeting-preparation views.
- Review-gated person/project alias hygiene; no silent entity merges.
- Paginated memory overview, topic slices, review/stale queues, and audited memory actions.
- Morning, end-of-day, and weekly rituals plus lightweight goal-aware prioritization.
- `memocore doctor` as the runtime/SQLite/Telegram preflight before PM2 restart.

V4 remains active until these workflows are stable with real Telegram usage and the remaining
manual review paths are proven. V5 should not begin merely because the underlying harness design
exists.

The V4 exit gate now explicitly includes fixture-driven transcript evaluation, wrong-entity
regressions, reversible knowledge batches, and canonical project/person/organization/decision
retrieval. See [Conversation Stability Gates](conversation-stability-gates.md).

## V5: Orchestration And Specialist Workers

V5 is intentionally held while V4 is deepened. When resumed, it introduces bounded delegation:

- Supervisor service and structured worker handoffs.
- Specialist workers for repository analysis, research, drafting, documents, or browsing.
- Execution logs, retries, timeouts, fallback, and verification.
- Approval boundaries before high-impact or external actions.
- Centralized durable memory ownership in the main secretary services.

## V6: Knowledge System And Productization

V6 makes the system easier to operate and share:

- Richer entity modeling for people, projects, topics, decisions, and recurring concepts.
- Backup, restore, import, export, privacy controls, and setup flows.
- Web dashboard or mobile-friendly overview after core secretary workflows are proven.
- PostgreSQL and `pgvector` only when measured retrieval, concurrency, or backup needs justify
  them.

## V7: Controlled Autonomy

V7 allows bounded action without losing user control:

- Draft messages, email replies, agendas, and documents.
- Approval-gated email sends and calendar writes.
- Policy outcomes: allow, deny, require approval.
- Idempotency keys, audit records, post-action verification, and compensating actions.
- Evaluation harnesses for memory, planning, retrieval, and action quality.

## Cross-Version Guardrails

- Do not collapse memory into raw chat history.
- Do not treat model guesses as durable facts without lifecycle controls.
- Do not add autonomy before observability, policy, and approval boundaries exist.
- Do not add graph or orchestration infrastructure before a secretary workflow needs it.
- Measure whether each version reduces administrative effort, not just stored-item counts.
