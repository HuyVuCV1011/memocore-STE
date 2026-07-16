# Conversation Stability Gates

MemoCore must prove reliable conversation and memory behavior before calendar, email, tool
orchestration, or multi-agent work begins.

## Current Boundary

The conversation pipeline is split into five explicit responsibilities:

| Component | Responsibility |
| --- | --- |
| `ConversationRouter` | Select deterministic, planned, or model-assisted intent. |
| `ConversationPlanner` | Build multi-turn entity-scoped plans and reversible update batches. |
| `ReferenceResolver` | Resolve canonical project, person, organization, task, and chat focus. |
| `ConversationExecutor` | Dispatch resolved intents only to registered action handlers. |
| `ConversationComposer` | Own reusable user-facing prompts and confirmations. |
| `ConversationFrameBuilder` | Build bounded turn, artifact, focus, and clarification context. |
| `ScheduleSemantics` | Normalize future, recurring, weekday, and time-range meaning. |
| `ActivityReconciliationService` | Keep linked task, meeting, person, and project projections consistent. |

`ConversationService` remains the compatibility orchestrator while legacy actions move behind
these boundaries incrementally.

Query dispatch, task mutations, memory lifecycle operations, clarification fallback, and shared
response copy now have dedicated executors/workflows. The compatibility service still hosts a few
specialized handler implementations, but orchestration no longer owns their dispatch policy.

## Transcript Corpus

The offline corpus contains 41 isolated conversations. Each fixture can assert intent, reply,
durable-write deltas, focused entity, final task state, final memory state, and batch rollback.
No-write assertions fingerprint tasks, reminders, memory, projects, people, meetings, follow-ups,
commitments, organizations, decisions, knowledge relations, activity links, and clarifications.
The corpus covers read-only queries, casual/no-op input, ambiguous writes, person/project/
organization focus, focus switching, scoped knowledge updates, wrong-entity isolation, task
mutations, recurring schedule queries, future-completion wording, task merge corrections, timezone
explanations, and rollback integrity.
It also includes fuzzy recurring-task completion taken from a real Telegram failure.

The release metrics are:

| Metric | Gate |
| --- | --- |
| Wrong intent | 0 known high-severity failures |
| Wrong entity write | 0 |
| Unintended task/memory/reminder write | 0 |
| Rollback integrity | 100% for covered batches |

Every production failure must be added to this corpus before its fix is merged.

## Required Gates

1. Every reported production misunderstanding becomes a transcript fixture.
2. Transcript fixtures must cover intent, entity target, persistence, rollback, and leakage.
3. Typed references such as `dự án này` must beat incidental token/name matches.
4. Writes must be source-linked, auditable, idempotent, and reversible by batch.
5. Project, person, organization, and decision retrieval must stay entity-constrained.
6. Full offline tests, migration smoke tests, compile checks, and `memocore doctor` must pass.
7. Real Telegram usage must complete an exact 14-local-day review window, evidenced by
   authenticated owner-private interaction rather than unrelated events, without unresolved
   high-severity routing or wrong-entity writes.
8. Every production turn must preserve the assistant outcome and affected artifact ids so later
   corrections can refer to the exact prior operation.
9. Clarification choices must remain active for numeric and exact-title replies and must never
   switch assistant language because the reply is short.
10. Default Telegram views must not expose internal relationship codes, source ids, confidence,
    or evidence metadata.
11. Mutating one projection of an activity must reconcile linked task/meeting/entity state and be
   undoable from one event snapshot.
12. Dynamic or vague batch task scopes must preview their exact targets and revalidate status/version
    snapshots before writing.
13. Recurrence backlog must never be silently completed; preserving or skipping missed occurrences
    requires an explicit user choice.
14. Task-reference metrics must omit raw messages, titles, and other user-authored content.
15. Pull requests must pass the Python 3.12 offline quality gate on Windows and Linux, including
   compilation, migration smoke tests, and the full non-provider test suite.
16. Production feedback must retain only allowlisted structural metadata. Every current-window
    high/critical or high-trust failure must link to a sanitized regression fixture; historical
    valid links remain auditable after they leave the active window.

## Orchestration Hold

Calendar, email, browser/file tools, specialist workers, and multi-agent orchestration remain held.
Passing automated tests alone does not release this hold; the real Telegram review window and
wrong-entity audit must also be clean.
