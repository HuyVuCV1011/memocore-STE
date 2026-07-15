# MemoCore V4 Trustworthy Daily Secretary Plan

## Purpose

This plan completes MemoCore V4 as a trustworthy single-owner daily secretary. It consolidates the
remaining product, reliability, data-protection, maintenance, and runtime work into one delivery
sequence without expanding into V5 orchestration, autonomous agents, or external write tools.

V4 is complete only when MemoCore can:

1. detect uncertain or inconsistent state;
2. show the user what needs review;
3. safely apply or undo corrections;
4. close open loops across tasks, commitments, projects, and daily rituals;
5. explain why information or priorities were surfaced;
6. recover its durable data from a verified backup; and
7. prove its reliability through production evidence, not only offline tests.

## Current Baseline

The current V4 baseline already provides:

- immutable Telegram note capture and source-message idempotency;
- typed extraction for tasks, reminders, projects, memory, people, meetings, follow-ups,
  commitments, organizations, and decisions;
- SQLite migrations `001` through `008` in the current live database;
- deterministic and model-assisted conversation routing;
- clarification, task reference resolution, snapshot-validated batches, and guarded undo;
- daily and weekly briefings, reminders, recurrence, quiet hours, and proactive nudges;
- person, project, context, meeting-preparation, memory, work, and review views;
- source-linked knowledge, event logs, memory lifecycle operations, and entity confirmation;
- a 41-conversation transcript regression corpus;
- a healthy PM2-managed Telegram runtime; and
- a passing offline suite of 434 tests at the latest implementation check.

The baseline is strong enough for continued daily use, but it is not yet sufficient to declare V4
complete. The remaining gap is operational trust: measuring failures in real use, resolving review
items consistently, recovering data, closing work loops, and reducing the risk of changes inside
large compatibility modules.

## Delivery Principles

1. **Trust before breadth.** Do not add calendar, email, browser, file, voice, or worker
   orchestration until the V4 production gates pass.
2. **Single-owner by default.** Do not introduce multi-user roles, tenant boundaries, or RBAC.
3. **Review before ambiguous writes.** Weak entity, task, time, or batch matches must become review
   items or clarifications.
4. **Every durable change is attributable.** Preserve source, affected artifact identifiers,
   before/after state, and the operation event needed for undo.
5. **No hidden correction.** Automatic reconciliation may fix projections, but user-authored meaning
   must not be silently rewritten.
6. **User views stay clean.** Default Telegram responses must hide internal IDs, confidence scores,
   relationship codes, and backend metadata.
7. **Evidence beats declarations.** Tests, production review metrics, restore drills, and deployment
   provenance determine readiness.
8. **Keep SQLite until measured needs justify a change.** Backup, retention, and verified restore
   come before a database migration.
9. **Keep the visible Telegram menu compact.** Extend existing hubs and inline actions instead of
   adding many top-level commands.
10. **Southern Vietnamese voice.** User-facing replies should use concise Southern Vietnamese tone,
    including `dạ` and `nha`, and avoid `nhé`.

## Scope Map

The work is organized into eight workstreams:

| ID | Workstream | Primary outcome |
| --- | --- | --- |
| W0 | Release and runtime hygiene | Every running build is reproducible, testable, and identifiable. |
| W1 | Production evidence and quality loop | Real failures become measurable review items and regressions. |
| W2 | Unified Review Center | All uncertainty and corrective work can be resolved from one hub. |
| W3 | Data protection and recovery | SQLite data can be backed up, verified, restored, and exported. |
| W4 | Commitment, waiting, and project closure | MemoCore actively closes open loops rather than only listing them. |
| W5 | Briefing and daily closeout intelligence | Daily rituals interpret priorities and safely update state. |
| W6 | Recurrence, reminder, and nudge completion | Time-based workflows cover practical recurring and reminder needs. |
| W7 | Unified search and timeline | The user can trace what happened, why, and from which source. |
| W8 | Maintainability and quality gates | Large modules shrink behind tested boundaries and CI becomes complete. |

## W0: Release And Runtime Hygiene

### Goal

Convert the current working tree into a traceable release candidate before adding more behavior.

### Deliverables

- Review and isolate the current feedback-loop and CI changes on a dedicated branch.
- Reconcile README, architecture, roadmap, readiness, and implementation-plan claims with runtime
  behavior.
- Commit the CI workflow so GitHub, not only the local checkout, executes it.
- Add a runtime version descriptor containing package version, Git commit, dirty-state flag, and
  schema version.
- Extend `memocore doctor` to display the runtime version descriptor.
- Record the deployed commit in the PM2 runtime environment.
- Add release notes or a changelog entry for every user-visible behavior change.
- Define a rollback procedure that restores both code revision and a compatible database backup.
- Prevent deployment from a dirty working tree unless an explicit development override is used.

### Acceptance gates

- `main` has no unexplained tracked or untracked product changes.
- CI passes on Windows and Linux from a clean checkout.
- README and readiness documentation agree on CI and release status.
- `memocore doctor` reports the exact running commit and whether it is dirty.
- A failed deployment can return to the previous code and database snapshot using documented
  commands.

Current V4 implementation: `memocore doctor` reports package version, current Git commit,
dirty-state flag, and the latest applied SQLite schema migration. The Windows restart script refuses
dirty-tree deploys unless `-AllowDirty` or `MEMOCORE_ALLOW_DIRTY_DEPLOY=1` is used, and it stamps
PM2 env with deploy commit, dirty flag, schema version, and deployment time. CI runs release
metadata checks, and tag pushes matching `v*` run the release gate requiring tag/version/changelog
agreement before creating a GitHub release.

## W1: Production Evidence And Quality Loop

### Goal

Prove reliability through a real Telegram review window and convert every failure into durable,
privacy-safe evidence.

### Event model

Standardize the following signals:

- `accepted`: a suggested action or interpretation was accepted;
- `edited`: the suggestion was accepted after modification;
- `rejected`: the suggestion was explicitly declined;
- `ignored`: the review item was dismissed without applying it;
- `correction`: the user corrected a previous assistant outcome;
- `wrong_intent`: the assistant selected the wrong behavior class;
- `wrong_entity`: a read or write targeted the wrong person, project, organization, task, or memory;
- `unintended_write`: durable state changed when it should not have;
- `clarification_failed`: a clarification did not resolve the ambiguity;
- `undo_used`: the user reversed a durable operation; and
- `restore_used`: recovery was needed after a release or data problem.

Events must store structural metadata and artifact IDs but not raw Telegram messages, task titles,
or private content in quality metrics.

### Deliverables

- Finish the structured feedback event migration away from ad-hoc JSONL logging.
- Store outcome, affected artifact IDs, source turn reference, operation ID, and resolution status.
- Add a quality summary for the last 7, 14, and 30 days.
- Distinguish open corrective feedback from informational accepted/rejected signals.
- Add severity levels:
  - `critical`: wrong durable entity write or unrecoverable data loss;
  - `high`: unintended write, destructive wrong intent, or broken undo;
  - `medium`: failed clarification, wrong read scope, or repeated correction;
  - `low`: copy, ranking, or presentation issue.
- Create a sanitized transcript-fixture template from a production failure.
- Require every critical or high production failure to gain a regression fixture before its fix is
  merged.
- Add a weekly quality report to `/review` with counts, trends, and unresolved items.
- Define the production review window as at least 14 consecutive days of normal use.

### Release metrics

| Metric | V4 gate |
| --- | --- |
| Known critical failures | 0 unresolved |
| Known high-severity failures | 0 unresolved |
| Wrong-entity durable writes | 0 during the review window |
| Unintended writes | 0 during the review window |
| Covered rollback integrity | 100% |
| High/critical production failures with transcript regressions | 100% |
| Open correction items older than 7 days | 0 |
| Backup verification | Latest scheduled backup verified |

### Acceptance gates

- Real Telegram use produces structured feedback signals without recording private message text in
  metrics.
- The quality report can identify unresolved high-severity events and their artifact types.
- The transcript suite includes every high-severity production misunderstanding found during the
  review window.
- The full review window completes with no unresolved critical/high event and no wrong-entity
  durable write.

Current V4 implementation: `memocore review-window --days 14` and `memocore doctor` report the
review-window gate from structured event logs, including observed days, corrections, open
corrections, failed clarifications, undo events, wrong-entity durable writes, unintended writes, and
unresolved high/critical trust events. Add `--require-passed` when the command is used as a release
gate. The gate remains collecting until real Telegram use provides the required consecutive
observation days.

## W2: Unified Review Center

### Goal

Turn `/review` into the single place where the user sees and resolves everything MemoCore is unsure
about.

### Combined review queues

The Review Center must include:

1. uncertain extractions and low-confidence write candidates;
2. tasks without a deadline, project, next action, or clear status;
3. ambiguous person, project, and organization aliases;
4. stale, conflicting, duplicate, or weakly sourced memory;
5. user corrections and other open feedback items;
6. pending and failed clarifications;
7. recent operations that remain eligible for undo;
8. commitments without direction, owner, due date, or follow-up state;
9. projects without a next action or recent activity; and
10. failed backup, restore, migration, or reconciliation checks.

### UX design

Keep `/review` as one visible command with inline sections:

- `Ưu tiên`: critical/high items and blockers;
- `Chưa chắc`: clarification and extraction uncertainty;
- `Tên & liên kết`: entity aliases and possible duplicates;
- `Ghi nhớ`: stale, conflicting, duplicate, and unsourced memory;
- `Công việc`: undated tasks, commitments, waiting items, and inactive projects;
- `Phản hồi`: corrections, rejected suggestions, and quality issues;
- `Gần đây`: reversible operations and undo availability; and
- `Hệ thống`: backup, migration, runtime, and reconciliation warnings.

Every item should support the smallest safe action set, such as confirm, edit, reject, ignore,
merge, choose target, add deadline, add project, mark resolved, or undo. Batch actions must preview
exact targets and revalidate their versions before writing.

### Data and service changes

- Introduce a typed `ReviewItem` projection rather than adding review-specific columns to every
  domain table.
- Derive review items from source entities and event logs; persist only workflow state that cannot
  be reconstructed safely.
- Add `review_item_id`, category, severity, source artifact, reason code, created time, status,
  assigned action, and resolution event.
- Add a `ReviewService` registry so each category owns its query, presentation, and action handlers.
- Preserve pagination and avoid loading unbounded event history.
- Add idempotency to every resolution action.

Current V4 implementation: `/review` is a decision-first hub for memory review, alias/entity
confirmation, pending clarifications, feedback, system warnings, Project Health, and recent
reversible work operations. The `Gần đây` tab lists only a small number of still-undoable
operations and exposes inline undo actions through the same guarded work undo flow.

### Acceptance gates

- Every queue listed above is reachable from `/review` without a new top-level Telegram command.
- Resolving an item removes it from the open count and records an audit event.
- Rejecting or ignoring an alias suggestion prevents it from reappearing without new evidence.
- Review actions do not expose internal IDs, confidence values, or private source content.
- Batch review actions are previewed, snapshot-validated, auditable, and undoable where technically
  reversible.

## W3: Data Protection, Backup, Restore, And Export

### Goal

Make data recovery a V4 product capability rather than a future operational task.

### CLI surface

Add:

```text
memocore backup
memocore backup --no-verify
memocore backups list
memocore backups prune --keep 14
memocore restore <backup-id> --dry-run
memocore restore <backup-id> --confirm --maintenance
memocore restore-drill
memocore export --format json
memocore export --format markdown
python scripts/quality/v4_readiness_gate.py --strict --require-clean
```

### Backup behavior

- Use the SQLite online backup API or an equivalent transaction-safe mechanism; never copy a live
  database file without consistency protection.
- Store a manifest with backup ID, creation time, schema versions, application version, source
  database checksum, backup checksum, size, and verification result.
- Verify `PRAGMA integrity_check`, required tables, migration ledger, and manifest checksum.
- Keep backups outside the live database directory.
- Support configurable retention by count and age.
- Default to daily local backups and a backup before migration, restore, or deployment.
- Encrypt off-device backups before upload or transfer.
- Never commit backups, manifests containing private paths, exports, or restored databases.

### Restore behavior

- Refuse restore while the Telegram process is writing unless maintenance mode is active.
- Always create a pre-restore safety backup.
- Run restore as dry-run first: decrypt, checksum, integrity check, schema compatibility, and free
  disk-space check.
- Restore to a temporary file, verify it, then atomically swap database paths.
- Preserve the failed/current database for forensic recovery.
- Start the application only after migrations and `memocore doctor` pass.
- Record restore and restore-drill outcomes in local operation reports without storing private
  content.

### Export behavior

- JSON export is machine-readable and includes stable IDs, relationships, timestamps, and source
  references.
- Markdown export is human-readable and grouped by projects, people, tasks, commitments, decisions,
  memory, reminders, meetings, follow-ups, and notes.
- Exports must distinguish raw evidence from interpreted/canonical memory.
- Redacted export options must omit raw notes and message identifiers.

### Scheduled recovery checks

- Run daily backup and verification.
- Run a periodic restore drill into a temporary database.
- Surface failed backup or restore verification in `/review` and `memocore doctor`.
- Track last successful backup, last verified backup, and last restore drill.

### Acceptance gates

- A live database can be backed up without stopping normal reads.
- A backup can restore into a clean temporary directory and pass all integrity and migration checks.
- A corrupted or incompatible backup fails closed without replacing the live database.
- A restore drill demonstrates that tasks, reminders, memory, entities, and event logs remain
  readable.
- Retention deletes only verified backup files inside the configured backup directory.
- Exported data can be read without MemoCore and contains enough context for manual recovery.

## W4: Commitment, Waiting, And Project Closure

### Goal

Make MemoCore actively close open loops across people and projects.

### Commitment lifecycle

Use explicit states:

- `open`: the commitment exists but is not yet waiting or completed;
- `waiting`: action is expected from another person;
- `due`: the commitment has reached its follow-up or due time;
- `fulfilled`: the promised outcome was delivered;
- `cancelled`: the commitment no longer applies; and
- `unclear`: direction, person, project, or expected outcome needs review.

Every commitment should support:

- direction: `user_owes`, `owed_to_user`, or `mutual`;
- person and optional project;
- promised outcome;
- due date and follow-up date;
- last contact/activity time;
- source evidence;
- linked task, meeting, follow-up, or decision; and
- completion/cancellation evidence.

### Natural closure behavior

Handle statements such as:

- “Alex đã gửi rồi”;
- “Tôi đã trả lời khách hàng”;
- “Việc này không cần theo nữa”;
- “Nhắc tôi hỏi lại vào thứ Sáu”;
- “MindX vẫn đang chờ tôi gửi báo cáo”; and
- “Chuyển việc đang chờ Alex sang Minh”.

If more than one item matches, show candidates and ask instead of choosing the first result.
Completion must reconcile linked task, follow-up, commitment, and project activity state through one
audited operation.

Current V4 implementation: MemoCore handles conservative person-scoped fulfillment/cancellation
messages for waiting tasks, follow-ups, and commitments. A message such as “Alex đã gửi rồi” closes
the single matching open loop for Alex and records a `TASK_DONE`, `FOLLOWUP_DONE`, or
`COMMITMENT_DONE` event. If more than one open loop matches the person, MemoCore lists candidates
and asks the user to specify which one instead of guessing.

### Project Health

Add a project-health projection containing:

- current goal or desired outcome;
- next action;
- overdue and near-due tasks;
- blocked and waiting items;
- open commitments in both directions;
- latest decision;
- key people;
- stale, conflicting, or weakly sourced memory;
- last activity time;
- inactivity duration; and
- review reasons.

Project health should classify projects as:

- `on_track`;
- `needs_next_action`;
- `waiting`;
- `blocked`;
- `at_risk`;
- `stale`; or
- `needs_review`.

This state is derived, not manually duplicated. A project with no next action should create a review
item or a low-frequency prompt rather than silently remaining inactive.

Current V4 implementation: `/review` Project Health treats missing-next-action prompts as leaf-level
work hygiene. Portfolio and capability containers, active parents with active child projects, and
unclassified context records are kept out of the immediate decision inbox so Telegram does not turn
structural project hierarchy into a noisy work queue.

### Telegram experience

- Extend `/work` with `Đang chờ`, `Tôi còn nợ`, and `Dự án cần chú ý`.
- Extend `/project <name>` with health, next action, open loops, and recent decisions.
- Extend `/person <name>` with commitments in both directions and last contact.
- Allow inline fulfill, cancel, reschedule follow-up, choose owner, and create next-action actions.
- Keep internal health codes hidden; render concise Vietnamese labels and explanations.

### Acceptance gates

- A user can answer “Tôi đang chờ ai?” and “Ai đang chờ tôi?” from canonical commitment state.
- Natural fulfillment closes the correct linked work or asks for clarification.
- Project health never marks a project healthy when it has overdue critical work or unresolved
  high-severity review items.
- Updating one activity projection reconciles linked task, follow-up, commitment, and project state.
- Every closure and reconciliation operation is auditable and safely undoable where possible.

## W5: Briefing And Daily Closeout Intelligence

### Goal

Make `/briefing` interpret the day and make `/endday` safely prepare tomorrow.

### Briefing responsibilities

`/today` remains a factual agenda. `/briefing` must add judgment:

- select the top one to three actions;
- explain briefly why each action matters;
- detect deadline collisions and overloaded time windows;
- identify blocked work and work waiting on others;
- detect projects with no next action;
- compare planned work with active goals;
- identify commitments requiring follow-up;
- avoid recommending action on work that cannot progress;
- surface only the highest-value risks; and
- suggest a realistic next step.

Recommended sections:

- `Nhận định`;
- `Nên làm trước`;
- `Đang chờ hoặc bị chặn`;
- `Điểm cần chú ý`; and
- `Bước tiếp theo`.

### Ranking model

Use a deterministic score before optional model-written explanation. Inputs should include:

- deadline proximity and overdue duration;
- explicit priority;
- goal alignment;
- project health;
- blocking or waiting state;
- commitment direction;
- estimated duration when known;
- recurrence state;
- last nudge and cooldown;
- recent user deferrals; and
- whether the item can actually be acted on now.

Persist ranking factors and selected artifact IDs for audit, but do not expose raw scores by default.
The model may summarize the selected facts; it must not invent priority evidence.

### Daily closeout

`/endday` should ask a compact set of questions:

1. What was completed?
2. What should move to tomorrow?
3. Who or what are you waiting for?
4. What should MemoCore remember?
5. What is tomorrow's main priority?

The user may answer naturally in one message. MemoCore should produce a preview containing proposed:

- task completions;
- deadline moves;
- new or updated waiting items;
- commitment changes;
- reviewed memory candidates;
- tomorrow priorities; and
- project next actions.

Nothing durable is changed until the preview is confirmed when multiple artifacts or ambiguous
references are involved. The confirmed closeout is one audited batch with guarded partial undo.

Current V4 implementation: `/today`, `/work`, and `/briefing` share a deterministic work
classification model so overdue, due-today, recurring, waiting, blocked, unscheduled, and upcoming
items are not ranked differently across surfaces. Waiting and blocked work is shown separately and
is not presented as immediately actionable. `/endday` previews and confirms tomorrow rollover for
dated active tasks, dated open follow-ups, and dated open commitments in one guarded batch. Undated
tasks and waiting/blocked items are not moved automatically. Each item is snapshot-validated before
writing, so MemoCore skips anything changed after preview instead of overwriting newer state.
The preview supports grouped confirmation: move every eligible item, or move only tasks,
follow-ups, or commitments. Confirmed closeout events store restore snapshots for the selected
groups, and closeout undo restores only items that still match the closeout state; items changed
after the closeout are skipped.

### Acceptance gates

- `/briefing` and `/today` produce observably different outputs.
- Every recommendation is traceable to stored task, commitment, project, goal, or schedule evidence.
- Blocked/waiting work is not incorrectly presented as immediately actionable.
- A daily-closeout message can update multiple artifact types through preview, confirmation,
  reconciliation, and undo.
- Tomorrow's briefing reflects the confirmed closeout without duplicating or losing work.

## W6: Recurrence, Reminder, And Nudge Completion

### Goal

Finish the practical time-management behaviors deferred from earlier versions.

### Interval recurrence

Support:

- every N days;
- every N weeks;
- selected weekdays;
- monthly by day of month;
- optional end date or occurrence count; and
- one-occurrence versus whole-series edits.

Keep recurrence structured. Do not store only natural-language recurrence text.

### Reminder behavior

- Add pre-deadline warnings, such as one day or two hours before due time.
- Support natural snooze, including “chiều mai”, “sau cuộc họp”, and “thứ Sáu tuần sau”.
- Ask whether a recurring change applies to one occurrence, future occurrences, or the whole series.
- Preserve the existing explicit choice for missed recurrence backlogs.
- Prevent duplicate reminders through leases and idempotency keys.

### Nudge policy

- Bundle multiple low-priority nudges into one digest.
- Keep urgent and explicitly requested reminders separate.
- Set per-artifact and per-category maximum nudge frequency.
- Stop nudging after repeated dismissal and create a review item instead.
- Respect quiet hours and user timezone.
- Learn only from explicit behavior signals initially; do not silently infer permanent preferred
  hours from a few interactions.
- Allow the user to configure preferred briefing, focus, and follow-up windows.

Current V4 implementation: briefing time, reminder default time, focus window, follow-up nudge
window, quiet hours, nudge cooldown, nudge bundle threshold, and per-run nudge limit are explicit
settings. Follow-up windows only gate stale follow-up nudges; task deadline warnings remain
eligible outside the follow-up window unless quiet hours block them.

### Acceptance gates

- Interval recurrence creates exactly one correct next occurrence.
- DST, timezone, month-end, and missed-backlog cases have deterministic tests.
- Editing a series never silently changes historical occurrences.
- Pre-deadline reminders and snoozes do not create duplicate delivery.
- Low-priority nudges are bundled without delaying urgent reminders.
- Repeatedly dismissed nudges stop and move to review rather than becoming spam.

## W7: Unified Search And Timeline

### Goal

Answer cross-domain questions about what happened, why it happened, and where the evidence came
from.

### Supported questions

- “Tuần trước tôi đã quyết định gì về MemoCore?”
- “Lần gần nhất nói chuyện với Alex là khi nào?”
- “Tại sao task này được tạo?”
- “Dự án này đã thay đổi deadline mấy lần?”
- “Cho tôi xem mọi thứ liên quan đến MindX trong tháng này.”
- “Việc nào tôi đã undo gần đây?”
- “Thông tin này đến từ đâu?”
- “Những commitment nào đã quá hạn rồi được đóng?”

### Retrieval model

- Build a normalized timeline projection over notes, tasks, reminders, meetings, follow-ups,
  commitments, decisions, memory, review events, and operation events.
- Keep entity IDs and source relationships canonical.
- Filter by entity, artifact type, event type, time range, status, and project hierarchy.
- Prefer structured SQLite filters and full-text search before vector retrieval.
- Preserve the difference between source evidence, interpreted state, and later correction.
- Include superseded and undone events in the audit timeline but mark their current validity.

### Presentation

- Default to concise summaries grouped by date or entity.
- Show human-readable source labels such as “tin nhắn Telegram”, “cuộc họp”, or “thay đổi task”.
- Offer an inline `Nguồn` or `Lịch sử` expansion without exposing database IDs.
- Explain “why” using event and source relationships, not model speculation.
- Paginate long timelines and enforce Telegram message-size limits.

### Acceptance gates

- Queries stay constrained to the requested person, project, organization, task, or time range.
- Wrong-entity leakage tests cover names shared across projects and incidental text matches.
- “Why was this created?” returns the source and operation chain.
- Undo and supersession are represented accurately in historical and current-state views.
- Default output contains no internal IDs, raw confidence, or backend relationship codes.

## W8: Maintainability And Quality Gates

### Goal

Reduce change risk while preserving behavior throughout the V4 completion work.

### Service extraction sequence

Do not perform a single large rewrite. Extract one tested behavior at a time from large compatibility
modules.

Recommended boundaries:

- `FeedbackService`: signal recording, resolution, severity, and quality summaries;
- `ReviewRegistry` and category handlers: review queries and actions;
- `BackupService`: backup, verification, retention, restore drill reports, dry-run, maintenance-aware
  atomic restore, and manifests;
- `CommitmentLifecycleService`: waiting, fulfillment, cancellation, and follow-up;
- `ProjectHealthService`: derived health and next-action detection;
- `BriefingRankingService`: deterministic prioritization and evidence;
- `DailyCloseoutService`: multi-artifact preview and batch execution;
- `RecurrencePolicy`: interval and series-edit semantics;
- `TimelineQueryService`: cross-domain history retrieval; and
- repository modules split by domain instead of one continuously growing file.

`ConversationService` should remain a compatibility facade while each extracted service gains direct
unit and integration coverage. Remove legacy paths only after transcript parity passes.

### CI quality gate

Require:

- Python 3.12 installation on Windows and Linux;
- source and test compilation;
- local V4 readiness report for release-only gates;
- migration smoke tests from both a clean database and the previous release schema;
- full offline tests excluding live-provider markers;
- linting;
- static type checking for new or modified modules;
- module-size budgets for the large compatibility modules;
- a coverage threshold, introduced from the measured baseline rather than an arbitrary number;
- dependency audit;
- secret scan;
- Markdown link and documentation-consistency checks for repo docs; and
- packaging/install smoke test.

### Test strategy

For every feature, cover:

- unit behavior;
- repository persistence and migration;
- service integration;
- Telegram action routing and presentation;
- idempotency;
- ambiguous reference handling;
- rollback or compensating behavior;
- timezone behavior where applicable;
- privacy-safe telemetry; and
- transcript regression for natural multi-turn use.

### Acceptance gates

- No new product behavior is added directly to Telegram handlers when it belongs in a service.
- Extracted services have narrow typed inputs and outputs.
- `ConversationService` line count and responsibility count trend downward across milestones.
- Repositories are separated without changing transaction ownership or hydration behavior.
- CI blocks merge when compile, migration, test, lint, type, audit, secret, or docs gates fail.
- A clean checkout can install, test, migrate, and start without relying on untracked local files.

## Milestone Sequence

### M0: Stabilize The Current Baseline

Includes W0 and the current feedback-loop work.

Exit criteria:

- current changes are reviewed and committed;
- CI is active on GitHub;
- docs agree with runtime behavior;
- tests, compile, migrations, audit, and doctor pass; and
- the running commit is identifiable.

### M1: Establish The Trust Loop

Includes W1 and the first complete version of W2.

Exit criteria:

- all review categories are visible in one hub;
- feedback and corrections are structured and resolvable;
- severity and weekly quality summaries exist; and
- production failures have a defined transcript workflow.

### M2: Make Data Recoverable

Includes W3.

Exit criteria:

- scheduled backup is active;
- backup verification and restore dry-run pass;
- a full restore drill succeeds; and
- backup failures appear in review and doctor.

### M3: Close Work Loops

Includes W4.

Exit criteria:

- commitments have a complete lifecycle;
- waiting questions work in both directions;
- natural fulfillment safely reconciles linked artifacts; and
- project health identifies missing next actions, blockers, and stale projects.

### M4: Complete Daily Secretary Behavior

Includes W5 and W6.

Exit criteria:

- briefing interprets priorities instead of repeating today;
- daily closeout safely updates tomorrow's state;
- interval recurrence, pre-deadline warnings, snooze, and nudge bundling pass edge-case tests; and
- repeated nudges cannot become spam.

### M5: Make History Explainable

Includes W7 and continued W8 extraction.

Exit criteria:

- cross-domain timeline questions are entity- and time-constrained;
- source and operation history explain why current state exists;
- large services have begun shrinking behind typed boundaries; and
- CI contains the complete quality gate.

### M6: V4 Production Review Window

Uses every workstream; it does not introduce new scope.

Exit criteria:

- at least 14 consecutive days of normal Telegram use;
- no unresolved critical or high-severity failures;
- zero wrong-entity durable writes;
- every discovered high-severity failure has a transcript regression;
- no open correction older than seven days;
- latest scheduled backup and restore drill are verified;
- CI and doctor remain green; and
- the user confirms that briefing, review, and closeout reduce manual work.

## V4 Definition Of Done

V4 is complete only when all statements below are true:

- The current release is committed, reproducible, documented, and identifiable at runtime.
- CI covers compile, migrations, offline tests, lint, type checks, dependency audit, secret scan,
  docs consistency, and installation.
- `/review` unifies uncertainty, entity hygiene, memory quality, task quality, commitments,
  corrections, undo, and system warnings.
- Feedback is structured, artifact-linked, privacy-safe, and resolvable.
- Every critical/high production failure becomes a transcript regression.
- Backup runs automatically, verifies integrity, and has passed a real restore drill.
- Export supports both machine-readable and human-readable recovery.
- Commitments and waiting items close naturally and reconcile linked work.
- Project health exposes goals, next actions, overdue work, blockers, waiting, decisions, people,
  memory risks, and inactivity.
- `/briefing` provides evidence-backed judgment distinct from `/today`.
- `/endday` previews and safely applies cross-artifact updates for tomorrow.
- Recurrence supports practical intervals and safe series edits.
- Reminders support pre-deadline warnings and natural snooze.
- Low-priority nudges bundle, respect limits, and stop after repeated dismissal.
- Timeline queries explain what happened, why, and from which source without leaking backend noise.
- `ConversationService`, repository, capture, secretary, and clarification responsibilities are
  progressively reduced behind tested services.
- The production review window passes with no unresolved high-severity trust failure.

## Explicitly Deferred Until After V4

- Multi-agent or specialist-worker orchestration.
- Automatic email sending or calendar writes.
- Browser, arbitrary file, shell, or external-action tools.
- A broad web dashboard.
- Automatic bulk import of private Telegram or Markdown archives.
- A separate graph database.
- PostgreSQL or vector infrastructure without measured SQLite limitations.
- Autonomous workflows that execute durable or external writes without confirmation.
- Multi-user hosting and role-based access control.
- Model-driven permanent behavior learning without explicit user evidence and review.

The first post-V4 capability may be one read-only calendar source for meeting preparation. It must
remain unavailable until the V4 production review window, backup/restore gate, and release quality
gate all pass.

## Coverage Of The Original Proposal

This plan intentionally retains every item from the review and feature proposal:

| Original item | Covered by |
| --- | --- |
| Package the current feedback loop and CI | W0, M0 |
| Repair documentation drift | W0 |
| Run a 2–4 week review window | W1, M6; minimum enforceable gate is 14 days |
| Track wrong intent, wrong entity, correction, clarification failure, and undo | W1, W2 |
| Convert every production failure into a transcript fixture | W1 |
| Add verified SQLite backup and restore | W3 |
| Add JSON and Markdown export | W3 |
| Reduce `ConversationService` and repository size incrementally | W8 |
| Add coverage, lint, type, secret, release, and CI gates | W0, W8 |
| Complete the Review Center | W2 |
| Review uncertain work, missing task metadata, aliases, memory, corrections, clarifications, and undo | W2 |
| Deepen commitment and waiting workflows | W4 |
| Answer who is waiting for whom | W4 |
| Automatically close the correct waiting item from natural language | W4 |
| Add controlled overdue follow-up | W4, W6 |
| Make briefing select and explain one to three priorities | W5 |
| Detect deadline collisions, blockers, waiting, missing next actions, and goal misalignment | W5 |
| Keep briefing distinct from today | W5 |
| Add Project Health | W4 |
| Include goal, next action, overdue, waiting, commitment, decision, people, memory risk, and inactivity | W4 |
| Support every N days and every N weeks | W6 |
| Add pre-deadline warning | W6 |
| Add natural snooze | W6 |
| Bundle low-priority nudges | W6 |
| Limit repeated nudges | W6 |
| Support preferred work/reminder windows without unsafe silent learning | W6 |
| Ask one occurrence versus whole recurring series | W6 |
| Add a multi-artifact daily closeout | W5 |
| Preview closeout changes before writing | W5 |
| Add unified search and timeline | W7 |
| Answer decision, contact, origin, deadline-history, and project-history questions | W7 |
| Show sources without backend IDs or confidence noise | W7 |
| Avoid V5, multi-agent, broad dashboard, graph DB, and autonomous writes | Explicit deferrals |
