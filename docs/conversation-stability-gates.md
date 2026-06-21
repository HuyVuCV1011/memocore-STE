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

`ConversationService` remains the compatibility orchestrator while legacy actions move behind
these boundaries incrementally.

## Required Gates

1. Every reported production misunderstanding becomes a transcript fixture.
2. Transcript fixtures must cover intent, entity target, persistence, rollback, and leakage.
3. Typed references such as `dự án này` must beat incidental token/name matches.
4. Writes must be source-linked, auditable, idempotent, and reversible by batch.
5. Project, person, organization, and decision retrieval must stay entity-constrained.
6. Full offline tests, migration smoke tests, compile checks, and `memocore doctor` must pass.
7. Real Telegram usage must complete a review window without unresolved high-severity routing or
   wrong-entity writes.

## Orchestration Hold

Calendar, email, browser/file tools, specialist workers, and multi-agent orchestration remain held.
Passing automated tests alone does not release this hold; the real Telegram review window and
wrong-entity audit must also be clean.
