# Changelog

All notable user-visible changes to MemoCore are tracked here. This project follows a pragmatic
`Unreleased` first workflow until a release tag is cut.

## Unreleased

### Added

- V4 trustworthy daily secretary plan covering release hygiene, review loops, backup/restore,
  commitment/waiting workflows, Project Health, briefing intelligence, daily closeout, recurrence,
  reminders, nudges, unified search/timeline, and quality gates.
- Telegram `/search` and natural timeline/source/decision queries backed by a cross-domain
  timeline projection.
- `/search` now recognizes "latest / lần gần nhất" style questions and returns the most recent
  human-readable trace instead of a long mixed result list.
- Telegram `/endday` closeout preview and confirmation for active tasks, open follow-ups, and open
  commitments with snapshot validation before writing.
- Verified SQLite backup, restore dry-run, maintenance-aware atomic restore, restore outcome and
  drill reports, retention pruning, grouped Markdown/JSON export, scheduled backup, doctor backup
  check, and review warning surfaces.
- Explicit preferred time windows for briefing/reminder/follow-up/focus behavior. MemoCore does not
  silently infer permanent preferred windows from behavior.
- Pre-deadline task warnings, bundled nudge digests, per-run nudge limits, natural reminder snooze,
  and interval recurrence rules.
- CI quality gates for source linting, targeted type checking, module-size budgets, clean and
  previous-release migration smoke tests, coverage-gated offline tests, dependency audit, and
  tracked-file secret scan.
- Markdown documentation link checking for local repo docs while ignoring private/local artifact
  folders.
- Local V4 readiness gate that reports release metadata, working-tree cleanliness, module-size
  budgets, production review-window status, and verified backup/restore-drill evidence.
- Runtime version descriptor in `memocore doctor`, including package version, Git commit, dirty
  flag, and latest applied SQLite schema migration.
- Windows PM2 restart guard that blocks dirty deployments by default and stamps deploy commit,
  dirty flag, schema version, and timestamp into PM2 env.
- `memocore review-window --days 14` and doctor review-window reporting for the production trust
  gate.
- Release metadata check and tag-triggered GitHub release gate for package version and changelog
  agreement.

### Changed

- Telegram's visible slash menu is now limited to `/today`, `/work`, `/context`, `/search`, and
  `/review`; `/briefing`, `/memory`, and `/capture` remain available as hidden shortcuts.
- `/today`, `/work`, and `/briefing` now share one deterministic work classification model so
  overdue, due-today, recurring, waiting, blocked, unscheduled, and upcoming tasks are handled
  consistently across views.
- `/briefing` now presents evidence-backed judgment distinct from `/today`, including analysis,
  signals, and recommended next actions without exposing raw scores.
- `/briefing` now keeps attention signals distinct from recommended next actions, and separates
  recurring routines into their own lane when harder deadlines or commitments need focus.
- `/briefing` now treats routine-only days as rhythm maintenance rather than a main strategic
  priority, and treats waiting-only days as open-loop decisions instead of do-now work.
- Briefing judgment logic now lives in a focused service module, keeping the Telegram secretary
  orchestrator small enough for future UX changes without weakening module-size gates.
- `/work` no longer repeats the same overdue task in both the recommended next-action list and the
  overdue detail section.
- `/work` waiting and commitment tabs now show actionable inline controls for follow-ups and
  commitments, including complete, reschedule, cancel, and undo-supported updates.
- User-facing task rankings no longer expose raw priority scores, including hidden shortcut views
  such as `/weekly`.
- `/review` is now decision-first, with work hygiene and 30-day quality signals below the primary
  review count.
- `/review` now includes a compact "Gần đây" undo surface for recent reversible work operations,
  with inline undo buttons and no backend IDs in the displayed text.
- `/review` Project Health now separates projects that need a near-term next-action decision from
  lower-pressure hygiene backlog, and groups large project lists instead of filling the Telegram
  screen with every project name.
- `/review` Project Health now focuses missing-next-action prompts on actionable leaf projects,
  avoiding portfolio/capability containers, active parents with child projects, and unclassified
  context records.
- Hidden `/weekly` project hygiene now uses the same actionable leaf-project rule as `/review`.
- `/endday` no longer automatically rolls undated tasks or waiting/blocked items into tomorrow.
- `/endday` closeout previews now support grouped confirmation, so the user can move all items or
  only tasks, follow-ups, or commitments.
- `/endday` now includes a compact closeout checklist and quick links to open tasks and waiting
  loops, so the ritual prompts completion, waiting, and tomorrow-priority decisions instead of
  only bulk-rescheduling due items.
- `/endday` closeout apply events now carry restore snapshots and can be undone through the work
  undo flow, skipping items changed after the closeout instead of overwriting newer edits.
- Natural fulfillment messages such as “Alex đã gửi rồi” can now close the single matching
  waiting task, follow-up, or commitment for that person; if multiple open loops match, MemoCore
  asks the user to choose instead of guessing.
- Natural open-loop closure events now record restore snapshots so accidental task, follow-up, or
  commitment closure can be undone through the work undo flow.
- `/review` and project/person context surfaces include more trust and Project Health signals.
- Recurring task completion and missed-backlog handling use guarded policies so MemoCore does not
  silently skip or duplicate recurrence work.

### Fixed

- Batch task updates and daily closeout confirmations revalidate status/version snapshots before
  writing, reducing stale-preview overwrite risk.
- Runtime backup and quality checks are now surfaced through `memocore doctor`.
