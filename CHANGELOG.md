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

- `/briefing` now presents evidence-backed judgment distinct from `/today`, including analysis,
  signals, and recommended next actions without exposing raw scores.
- `/review` and project/person context surfaces include more trust and Project Health signals.
- Recurring task completion and missed-backlog handling use guarded policies so MemoCore does not
  silently skip or duplicate recurrence work.

### Fixed

- Batch task updates and daily closeout confirmations revalidate status/version snapshots before
  writing, reducing stale-preview overwrite risk.
- Runtime backup and quality checks are now surfaced through `memocore doctor`.
