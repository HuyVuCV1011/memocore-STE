# MemoCore V4 Acceptance Gate Matrix

## Audit Snapshot

This matrix records the independent Professor and Examiner audit performed on 2026-07-16. It is
the evidence output of phase R0 in `docs/v4-remaining-execution-plan.md`.

The audit was read-only. It did not access `.env`, runtime SQLite data, Telegram sessions, or start
a second bot. The Professor ran 92 relevant offline tests. The Examiner ran 55 W5-W7 tests plus
Ruff, Mypy, module-size, release metadata, documentation-link, and tracked-secret checks. PR 10
hosted Windows, Linux, dependency-audit, and CodeQL checks were passing at audit time.

Status meanings:

- `PROVEN`: implementation and acceptance evidence cover the gate.
- `IMPLEMENTED_UNPROVEN`: the path exists but the acceptance behavior lacks direct evidence.
- `PARTIAL`: a meaningful subset exists, but one or more promised behaviors are absent.
- `MISSING`: the acceptance behavior is not implemented or not represented truthfully.

## Workstream Summary

| Workstream | Status | Strongest evidence | Remaining acceptance gap |
| --- | --- | --- | --- |
| W0 Release/runtime hygiene | PARTIAL | Runtime commit/dirty/schema descriptor, dirty-tree deploy guard, Windows/Linux CI, and release tag gate. | Combined code-and-database rollback procedure and final clean release candidate evidence. |
| W1 Production evidence | PARTIAL | Structured feedback, review-window report, quality trends, and trust-event counters. | Observed days currently accept arbitrary events rather than proving normal Telegram use; high/critical failure-to-transcript enforcement and metadata sanitization are absent. |
| W2 Unified Review Center | PARTIAL | Memory, alias, clarification, feedback, system, recent undo, commitment, project health, and quality views are reachable. | Typed `ReviewItem`, complete task-hygiene projections, migration/reconciliation warnings, and guarded batch review contract are absent. |
| W3 Backup/restore/export | PARTIAL | Online SQLite backup, checksum/integrity manifest, bounded prune, temp restore, drill, JSON, Markdown, and maintenance guard. | Scheduled/pre-deploy backup, verified safety backup, free-space/schema compatibility, post-restore migrate/doctor, complete drill semantics, and restore-failure review signals. |
| W4 Commitment/waiting/project closure | PARTIAL | Conservative person-scoped closure, ambiguity handling, audited events, undo, work actions, and project risk projection. | Full commitment state model and one transactional linked task/follow-up/commitment/project reconciliation operation. |
| W5 Briefing/closeout | PARTIAL | Briefing is distinct from today; waiting/blocked isolation, routine, collision, and goal scenarios are tested; closeout has preview/snapshot/undo. | Ranking evidence is not persisted; closeout remains deadline rollover rather than natural multi-artifact closeout; closeout-to-next-briefing regression is absent. |
| W6 Recurrence/reminder/nudge | PARTIAL | Interval day/week recurrence, backlog choice, lease-based reminder delivery, pre-deadline warning, snooze, bundling, and limits. | Monthly/weekday/end/count and future/whole-series semantics, DST/month-end tests, meeting-relative snooze, urgent bundle separation, and dismissal-to-review policy. |
| W7 Search/timeline | PARTIAL | Cross-domain retrieval and source/operation explanation work for covered happy paths. | Canonical entity/time filters, wrong-entity leakage tests, undo/supersession validity, and privacy-safe event rendering are absent. |
| W8 Maintainability/quality | PARTIAL | CI gates compile, migrations, tests, lint, type targets, coverage, audit, secret scan, and docs; focused services exist. | Large compatibility modules do not trend down, repositories remain monolithic, and package/start plus real previous-release migration smoke are absent. |

## Proven Sub-Gates

- `/briefing` and `/today` are structurally distinct.
- Waiting and blocked work is not included in immediate `next_actions`.
- Interval day/week recurrence creates one tested next occurrence in the covered cases.
- “Why was this created?” returns source and operation evidence in covered timeline tests.
- New `/search` and `/endday` behavior is delegated from handlers to services.
- Hosted CI currently covers the primary compile, migration, test, lint, type, coverage, audit,
  secret, and documentation gates.

These proven items must retain regression coverage while the partial gates are completed.

## Prioritized Delivery Queue

### P0: User-Target Safety

1. **Completed 2026-07-16:** make `/today` a factual, bounded agenda with one heading, one occurrence
   per task, clearly mapped actions, navigation parity, and no distant-future priority fallback.
2. Change review-window observation semantics to count evidence of normal Telegram use, not any
   arbitrary event.
3. Replace token-only timeline scope with canonical entity/time constraints and add shared-name,
   cross-project, and incidental-match leakage regressions.
4. Stop timeline rendering from concatenating raw event payload values.

### P1: Recovery And Durable-State Safety

1. Make pre-restore safety backups verified and fail closed.
2. Add free-space and schema/application compatibility checks before restore.
3. Run required migrations and doctor after atomic replacement before normal runtime resumes.
4. Surface backup and restore verification failures in review and doctor.
5. Reconcile linked task, follow-up, commitment, and project state through one audited operation.

### P2: Daily Workflow Completion

1. Persist briefing selection evidence and add a closeout-to-next-briefing regression.
2. Extend closeout from deadline rollover to a natural preview of completions, waiting state,
   memory, tomorrow priority, and project next actions.
3. Add dismissal counters and promotion to review so nudges cannot become spam.
4. Complete recurrence monthly/weekday/end/count and one/future/whole-series semantics with DST and
   month-end tests.
5. Represent timeline undo, supersession, and current validity explicitly.

### P3: Product Completeness And Maintainability

1. Introduce the typed `ReviewItem` projection and guarded batch review actions.
2. Complete task, migration, restore, and reconciliation review categories.
3. Continue behavior-by-behavior extraction from compatibility services and split repository
   responsibilities by domain.
4. Ratchet module budgets downward after each extraction.
5. Add package/start smoke and a fixture from the real previous-release schema.
6. Document and drill a rollback that restores both the code revision and a compatible verified
   database backup.

## Release Blocking Evidence

V4 remains `REVISE` until all complete-plan gates are proven. In particular, the production review
window must not pass solely because unrelated synthetic or maintenance events span 14 dates. The
final window must prove normal Telegram usage, zero wrong-entity durable writes, zero unintended
writes, no unresolved high/critical failures, complete regression coverage for discovered severe
failures, and a current verified backup and restore drill.

No original V4 proposal item is removed by this prioritization. A lower priority indicates delivery
order, not deferral.
