# MemoCore 0.5.0 Readiness

## Baseline

- Current package version: `0.4.1`.
- Current branch baseline: V1-V4 are documented as delivered.
- Automated verification on June 11, 2026: `195 passed, 1 skipped`.
- Python compilation check: passed for `src` and `tests`.
- Live provider benchmark and live Telegram polling were not exercised because they require
  external services and credentials.

## Current Capability Audit

| Capability | Readiness | Evidence and limits |
| --- | --- | --- |
| Raw Telegram capture | Ready | Immutable notes, source-message idempotency, transactional derived writes. |
| Task, reminder, project, and memory extraction | Ready | Typed extraction schema, provider validation/fallback, repository and integration tests. |
| Conversational queries and corrections | Ready | Deterministic/model-assisted routing, clarification, task updates, memory correction/delete. |
| Briefings and proactive reminders | Ready | Daily/weekly views, recurring reminders, leases, quiet hours, cooldowns, audit events. |
| People/project context retrieval | Ready | Linked repositories and `/person`, `/project`, `/context`, and `/prep` views. |
| Commitments and meeting retrieval | Ready | Storage, linked views, and V4 integration tests are present. |
| People/meeting/follow-up/commitment ingestion | Ready | Typed extraction and transactional capture persistence are implemented with ambiguity guards. |
| Runtime operations | Partial | PM2 single-process guidance exists; no automated health/preflight command. |
| Release automation | Missing | No CI workflow, release tags, coverage gate, or migration smoke job. |
| V5 harness/orchestration | Missing | Direction is documented, but there are no run, tool, policy, approval, or worker contracts. |

## Release Decision

Treat `0.5.0` as the first bounded harness release, not as a complete multi-agent platform.
Deliver one useful read-only workflow end to end before adding specialist workers or external
writes.

The recommended workflow is meeting preparation with a registered read-only calendar source.
It extends an existing V4 user experience and provides a concrete place to measure whether tool
retrieval improves the result.

## Scope

### Required

1. Add typed domain contracts for `HarnessRun`, `ToolDefinition`, `ToolCall`, and `RunEvent`.
2. Add SQLite migrations and repositories for harness runs, tool calls, and append-only events.
3. Add a deterministic tool registry and execution service with input validation and per-tool
   timeout.
4. Add an execution policy with `allow` and `deny`; reserve `require_approval` for a later
   write-capable milestone.
5. Add one read-only calendar adapter behind a protocol, with a fake adapter for tests.
6. Enrich meeting preparation through the harness while preserving the current SQLite-only
   fallback.
7. Add unit and integration coverage for malformed calls, denied tools, timeout, audit trace,
   fallback, and successful meeting-prep enrichment.
8. Add CI for Python 3.12 with install, compile, and offline test jobs.
9. Add a migration smoke test that upgrades an existing `0.4.1` SQLite database.

### V4 Baseline

`0.4.1` closes the ingestion gap with typed candidates, transactional persistence, linked context,
ambiguity guards, audit warnings, idempotency, and retry after failed derived writes.

### Deferred

- Model-selected free-form tool loops.
- Coding, research, document, and browsing workers.
- Email or calendar writes.
- Approval UI and approval-gated execution.
- Arbitrary shell, Python, URL, or file access.
- Durable queues, PostgreSQL, graph storage, and general agent frameworks.

## Proposed Architecture

```text
SecretaryService
  -> HarnessService
      -> ToolRegistry
      -> ExecutionPolicy
      -> ToolAdapter protocol
      -> Harness repositories
```

Suggested modules:

```text
src/memocore/domain/harness.py
src/memocore/services/harness_service.py
src/memocore/services/execution_policy.py
src/memocore/adapters/tools/calendar.py
src/memocore/adapters/storage/harness_repositories.py
```

Keep tool results ephemeral unless an existing secretary service explicitly converts reviewed
content into durable MemoCore state. The harness must not write memory directly.

## Acceptance Gates

- All existing offline tests remain green.
- New harness tests cover success, invalid input, deny, timeout, adapter failure, and audit order.
- Every tool call belongs to one bounded run and produces append-only events.
- Unknown tools and unregistered arguments fail closed.
- Meeting preparation still works when the calendar adapter is disabled or unavailable.
- No external write can be executed through the `0.5.0` registry.
- CI passes on a clean checkout with Python 3.12.
- README, package version, changelog/release notes, and migration documentation agree on scope.

## Delivery Order

1. Release hygiene: CI, migration smoke fixture, and version/release notes.
2. Harness contracts and persistence.
3. Registry, policy, timeout, and audit execution.
4. Read-only calendar adapter and meeting-prep integration.
5. Failure-path tests and documentation closeout.

## Known Risks

- Model extraction quality still depends on explicit names and directional evidence in the note.
- The Telegram runtime is a single process with in-process background loops; long tool calls must
  not block reminder or briefing delivery.
- SQLite schema creation and incremental upgrades coexist in `sqlite.py`; new harness tables should
  use a packaged migration and be tested against an existing database.
- Live provider and Telegram behavior remain outside the default offline suite.
- Pytest completed successfully on Windows but emitted a non-failing temp-directory cleanup
  `PermissionError` after the test summary.
