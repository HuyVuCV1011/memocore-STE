# MemoCore 0.5.0 Readiness

## Baseline

- Current package version: `0.4.1`.
- Current branch baseline: V1-V4 are documented as delivered.
- Automated verification on June 21, 2026: `279 passed, 1 skipped`.
- Current local verification on July 15, 2026: `434 passed, 1 skipped`.
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
| Organization and decision knowledge | Ready | First-class SQLite models, repositories, extraction candidates, links, and retrieval evidence. |
| Transcript evaluation | Active | Fixture-driven multi-turn regressions cover contextual entity priority, scoped updates, and batch rollback; real Telegram review is still required. |
| Runtime operations | Ready | PM2 single-process guidance exists; `memocore doctor` checks runtime version, config, SQLite, verified backups, latest restore drill, Telegram commands, runtime data, provider config, and PM2 before restart. |
| Release automation | Partial | Hosted CI workflow now runs Python 3.12 compile, ruff source lint, targeted mypy, module-size guard, Markdown link check, clean and previous-release migration smoke tests, coverage-gated offline tests, pip-audit, tracked-file secret scan, and release metadata checks on Windows and Linux. `scripts/quality/v4_readiness_gate.py --strict --require-clean` reports the local release-only gates, including review-window and backup/restore evidence. Tag pushes matching `v*` run the release gate and require the tag to match the package version plus a matching changelog section. Final release still requires a clean committed candidate and the production review-window gate. |
| V5 harness/orchestration | Missing | Direction is documented, but there are no run, tool, policy, approval, or worker contracts. |

## Release Decision

Keep `0.5.0` and the harness track on hold until the conversation stability gates pass. Automated
tests are necessary but insufficient: real Telegram usage must complete a review window without
unresolved high-severity routing or wrong-entity writes.

After that gate, the recommended first harness workflow remains meeting preparation with a
registered read-only calendar source. It must not introduce external writes or specialist workers.

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
8. Keep CI active for Python 3.12 with install, compile, migration smoke, and offline test jobs.
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

1. Release hygiene: CI, version/release notes, and ongoing doctor/migration smoke coverage.
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
