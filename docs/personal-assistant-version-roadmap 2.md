# Personal Assistant Version Roadmap

This file is kept as a restored planning copy. The canonical roadmap is now aligned with
`docs/personal-assistant-version-roadmap.md`.

## Versions

- V1: Capture and memory foundation.
- V2: Conversational secretary.
- V3: Daily and recurring secretary.
- V4: People, projects, and meetings.
- V5: Orchestration and specialist agents.
- V6: Knowledge system and productization.
- V7: Controlled autonomy.

## V1 Scope

V1 is one product version, not a separate V1.5 milestone. It may have internal implementation
checkpoints, but they all belong to the same V1 foundation:

- Telegram capture and immutable raw notes.
- Structured extraction into notes, tasks, reminders, projects, memory items, meetings,
  follow-ups, and event logs.
- SQLite local runtime.
- Provider abstraction and provider switching.
- Validation-aware fallback and transactional derived writes.
- Idempotent capture and leased single-shot reminder dispatch.
- Secretary commands for today, tasks, reminders, projects, memory, and waiting items.
- Basic memory lifecycle: candidate, active, rejected, superseded, delete/forget.
- V1 memory hygiene safeguards so obvious questions or corrections do not poison memory.

## V1 Out Of Scope

- Full natural-language conversation routing.
- Recurring reminders.
- Automatic morning briefings.
- Deep meeting preparation.
- Calendar, email, file, or voice integrations.
- Specialist-agent orchestration.
- Autonomous external actions.

## V2: Conversational Secretary

V2 adds a conversation layer that classifies Telegram messages before choosing a workflow. It should
route captures, questions, instructions, corrections, clarifications, and casual/no-op messages.
Natural messages such as "hôm nay tôi cần làm gì" or "tôi đã lưu gì về bản thân" should answer from
SQLite instead of creating new tasks or memory.

## V3: Daily And Recurring Secretary

V3 adds recurring reminders, morning briefings, weekly reviews, deadline nudges, stale-loop nudges,
quiet hours, and better separation of upcoming, overdue, sent, and recurring reminders.

## V4: People, Projects, And Meetings

V4 links people, projects, meetings, follow-ups, decisions, and memory. It should answer what the
user owes others, what others owe the user, and what context is needed before a meeting.

## V5: Orchestration And Specialist Agents

V5 introduces a supervisor layer and bounded worker handoffs for coding, research, drafting,
documents, and browsing, with execution logs, verification, and approval boundaries.

## V6: Knowledge System And Productization

V6 adds richer entity modeling, backup, restore, import, export, privacy controls, installability,
and dashboard/product surfaces after core secretary workflows are proven.

## V7: Controlled Autonomy

V7 adds approval-gated actions such as drafts, email/calendar writes, policy outcomes, idempotency,
audit records, post-action verification, and evaluation harnesses.
