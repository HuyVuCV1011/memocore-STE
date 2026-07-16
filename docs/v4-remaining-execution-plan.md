# MemoCore V4 Remaining Execution Plan

## Purpose

This document is the working plan for finishing the remaining V4 work after the implementation
completed on 2026-07-15. It supplements the complete scope in
`docs/v4-trustworthy-daily-secretary-plan.md`; it does not replace or reduce that scope.

Live Telegram QA with the secondary account is parked for now. Its runner, cases, reports, and
local session remain separate from the V4 completion path and must not block the work below.

## Current Evidence Snapshot

As of 2026-07-16, the local V4 readiness gate reports:

- release metadata: pass at version `0.4.1`;
- working tree: clean before this planning update;
- large-module budgets: pass;
- backup and restore: latest backup verified and a restore drill recorded; and
- production review window: collecting at 11 of 14 observed days, with zero unresolved high
  severity events, zero wrong-entity writes, and zero unintended writes.

This snapshot is evidence, not a declaration that every product acceptance gate is complete. The
remaining work starts with a gap audit so already-delivered behavior is verified rather than rebuilt.

## Scope Distinction

Two different meanings of multi-agent work must remain separate:

1. **Development sub-agents are allowed now.** They may plan, implement, review, and test bounded
   repository changes under the workflow below.
2. **Multi-agent behavior inside MemoCore remains deferred.** V4 must not add autonomous specialist
   workers, peer swarms, external tool execution, or durable writes delegated to product agents.

The sub-agent workflow is a software-delivery technique. It is not a new MemoCore feature.

## Completion Strategy

### Phase R0: Re-establish The Exact Remaining Baseline

Goal: turn yesterday's unfinished work into an evidence-backed checklist.

- Compare every W0-W8 acceptance gate in the complete V4 plan with source, tests, runtime evidence,
  and Telegram output.
- Mark each gate `proven`, `implemented but unproven`, `partial`, or `missing`.
- Confirm the open PR and CI state and separate product gaps from release-only gaps.
- Preserve the current production database and PM2 runtime; do not run tests or experimental bots
  against live data.
- Record any real failure as a privacy-safe quality event and transcript regression candidate.

Exit gate: one prioritized gap list exists, with evidence and exact tests for every non-proven item.

### Phase R1: Consolidate Telegram Daily Surfaces

Goal: make `/today`, `/work`, and `/briefing` feel like one coherent product without duplicating one
another.

- Keep `/today` factual and time-oriented: today's schedule, due work, and immediate reminders.
- Keep `/work` operational: actionable queues, waiting/blocked work, commitments, and project risks,
  with state-changing actions behind explicit controls.
- Keep `/briefing` interpretive: one to three priorities, reasons, collisions, blockers, goal fit,
  and one realistic next step.
- Audit duplicated items, ordering, headings, message length, inline buttons, empty states, and
  navigation between the three surfaces.
- Standardize concise Southern Vietnamese voice: use `dạ` and `nha`, avoid backend terms, raw IDs,
  confidence values, repetitive greetings, and excessive `nhé`.
- Add golden rendering or presentation tests for empty, normal, overloaded, waiting-only,
  routine-only, overdue-heavy, and mixed-project days.
- Convert every confirmed UX defect from real Telegram output into a fixture before changing the
  renderer or ranking rules.

Exit gate: each command has a distinct user job, shared facts remain consistent, and Telegram tests
prove that the three outputs are observably different without contradicting one another.

### Phase R2: Close Functional Acceptance Gaps

Goal: finish any W2-W7 behavior found partial or missing by R0, in this order.

1. Review Center: verify every review queue, resolution action, audit event, idempotency rule, and
   guarded batch action.
2. Commitment and waiting lifecycle: verify both waiting directions, natural fulfillment,
   ambiguity handling, reconciliation, and undo.
3. Project Health: verify goals, next actions, overdue work, blockers, waiting, commitments,
   decisions, people, memory risk, inactivity, and leaf-project noise control.
4. Briefing and `/endday`: verify evidence-backed priority judgment, preview, confirmation,
   snapshot validation, reconciliation, and partial undo.
5. Recurrence, reminders, and nudges: verify interval rules, one-versus-series changes, month-end,
   timezone/DST, backlog handling, pre-deadline warnings, snooze, bundling, dismissal limits, and
   quiet hours.
6. Search and timeline: verify entity/time constraints, origin and deadline history, undo and
   supersession, pagination, source labels, and absence of backend metadata.
7. Backup, restore, and export: verify scheduled backup evidence, retention boundaries, corrupted
   backup failure, clean-directory restore, JSON/Markdown readability, and review/doctor warnings.

Each item must be delivered as a narrow vertical slice: behavior, service boundary, persistence if
needed, Telegram presentation, tests, docs, and audit/undo behavior where applicable.

Exit gate: all complete-plan acceptance gates are either proven or have an explicit user-approved
deferral; no proposal item is silently dropped.

### Phase R3: Maintainability And Regression Hardening

Goal: reduce the cost and risk of the final fixes without starting a broad rewrite.

- Extract only the behavior touched by an active gap from compatibility modules.
- Keep `ConversationService` as a facade while moving narrow logic into typed services.
- Split repository responsibilities by domain only when a current change needs that boundary.
- Add unit, repository, integration, Telegram presentation, idempotency, ambiguity, timezone,
  privacy, rollback, and transcript coverage proportional to each change.
- Run targeted tests first, then the full offline suite for service, repository, schema, or broad
  routing changes.
- Enforce compile, lint, type, module budget, coverage baseline, dependency audit, secret scan,
  docs consistency, migration, and packaging gates.

Exit gate: no new business behavior lives only in Telegram handlers, module budgets pass, and the
full quality gate passes from a clean checkout.

### Phase R4: Production Proof And V4 Closeout

Goal: finish V4 with operational evidence rather than test-suite confidence alone.

- Continue normal Telegram use until the review window reaches at least 14 of 14 required days.
- Triage every correction, failed clarification, unintended write, wrong entity, undo, or restore.
- Require a sanitized transcript regression for every high or critical production failure.
- Verify there are no unresolved critical/high events, wrong-entity durable writes, unintended
  writes, or corrections older than seven days.
- Run a fresh verified backup and restore drill.
- Run the strict clean-tree V4 readiness gate, full CI, doctor, and deployment provenance checks.
- Review final `/today`, `/work`, `/briefing`, `/review`, and `/endday` output in Telegram.
- Reconcile README, roadmap, implementation plan, readiness notes, and changelog with actual state.
- Merge, deploy the exact clean commit, and confirm PM2 runs one identifiable instance.

Exit gate: every item in the V4 Definition of Done is proven, the user accepts the daily workflow,
and V4 can be marked complete.

## Professor-Executor Sub-Agent Loop

Use this loop for each bounded item in R0-R4. The names describe responsibilities and do not require
hard-coding one model forever.

### Roles

- **Director:** the primary Codex task owns scope, prioritization, production safety, and final
  acceptance.
- **Professor (`Sol`):** independently traces the design, defines the rubric, finds logic risks, and
  grades the result. It should not make the main implementation unless asked to rescue a blocker.
- **Executor (`5.5/Codex`):** implements only the approved brief, adds tests, and reports deviations.
- **Checker:** runs the exact verification commands and reduces failures to reproducible evidence.

### Loop

1. Director writes one bounded handoff packet with goal, allowed files, current behavior, expected
   behavior, tests, risks, output, and stop conditions.
2. Professor reviews the design before code for medium, large, routing, persistence, or mutation
   changes. Small documentation or narrow test changes may skip this pre-review.
3. Executor implements the smallest complete vertical slice and does not broaden scope silently.
4. Checker runs targeted verification and returns commands, results, and minimal failure evidence.
5. Professor grades the diff as `PASS`, `REVISE`, or `BLOCK` against the original rubric, with file-
   and behavior-specific reasons.
6. Executor receives only the unresolved findings and revises the same slice.
7. After at most two normal revision loops, Director resolves disagreement, narrows the task, or
   escalates the design. Do not create an endless agent conversation.
8. Director performs final QA, confirms clean scope, updates the plan evidence, and only then commits
   or deploys.

### Mandatory Escalation To The Professor

Return for design review when any of these occurs:

- a change may write to the wrong entity or bypass clarification;
- database schema, migration, transaction, restore, or recurrence semantics change;
- Telegram authorization, callback mutation, undo, or batch confirmation changes;
- the implementation needs files outside the approved scope;
- offline tests and real Telegram behavior disagree;
- the same test fails after two focused fixes; or
- the executor and reviewer disagree about data safety or acceptance behavior.

### Safe Parallelism

Parallel work is allowed only for independent lanes such as read-only code tracing, UX fixture
analysis, documentation consistency, and test-gap inventory. Do not let agents concurrently:

- edit the same files or overlapping business behavior;
- mutate the same SQLite database;
- start multiple bot pollers with one Telegram token;
- deploy while another agent is testing; or
- review an uncommitted moving diff without a fixed patch or commit reference.

## Per-Task Handoff Template

```text
Goal:
Acceptance rubric:
Allowed files:
Files already inspected:
Current behavior and evidence:
Expected behavior:
Required tests:
Production/data risks:
Output required:
Stop and escalation conditions:
Fixed diff or commit for review:
```

## Immediate Next Queue

1. Run R0 and produce the acceptance-gate matrix.
2. Use R1 as the first Professor-Executor trial because Telegram daily-surface overlap is the most
   visible remaining product question.
3. Implement only gaps proven by that audit, one vertical slice at a time.
4. In parallel with normal daily use, let the production review window advance from 11/14 to 14/14.
5. Finish R3 and R4, then declare V4 complete or report the exact failed gate.

The secondary-account Live Telegram QA path stays parked until the user explicitly resumes it.

## R0 Audit Result

The independent Professor and Examiner audit is recorded in
`docs/v4-acceptance-gate-matrix.md`. R0 found every W0-W8 workstream partially delivered, with
specific proven sub-gates and a prioritized P0-P3 completion queue. The Examiner verdict is
`REVISE`; V4 is not ready to close solely on the current 11/14 review-window count.

The first R1 vertical slice passed the Professor-Executor-Examiner loop on 2026-07-16. `/today` is
now a factual, bounded agenda with one dated heading, a 48-hour next-milestone horizon, at most five
directly actionable tasks, numbered actions aligned to the visible list, shared command/navigation
behavior, and matching conversational task references. The remaining R1 work is `/work` hub density
and multi-routine `/briefing` judgment.
