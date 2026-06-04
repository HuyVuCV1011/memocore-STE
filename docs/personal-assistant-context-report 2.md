# Personal Assistant Project Context Report

This file is kept as a restored context copy. The canonical context is now aligned with
`docs/personal-assistant-context-report.md`.

## Product Intent

Memocore is a local-first personal secretary, not a generic chatbot and not only a note archive.
It should reduce the user's need to remember, re-explain, triage, and chase open loops manually.

Telegram is the first capture surface because it is fast and habit-friendly, but core logic belongs
in services and repositories so future web, voice, email, calendar, file, and worker-agent adapters
can reuse the same backend.

## Version Direction

The project now uses one consistent version plan:

- V1: Capture and memory foundation.
- V2: Conversational secretary.
- V3: Daily and recurring secretary.
- V4: People, projects, and meetings.
- V5: Orchestration and specialist agents.
- V6: Knowledge system and productization.
- V7: Controlled autonomy.

V1 may contain internal implementation checkpoints, but memory trust basics are still part of V1.
The project should not treat V1.5 as a separate product version.

## V1 Foundation

V1 should provide reliable local capture and structured storage:

- raw notes;
- tasks;
- reminders;
- projects;
- people;
- meetings;
- follow-ups;
- memory items;
- event logs.

It should also include provider abstraction, schema validation, fallback, idempotency, transactional
writes, single-shot reminder dispatch, and command-based secretary views.

## Memory Principle

Memory must be managed, not append-only. V1 should already distinguish profile, project, and
interaction memory, and should support basic lifecycle actions such as candidate, active, rejected,
superseded, delete, and forget/redact.

The system should avoid obvious memory contamination before V2. Questions such as "tôi đã lưu gì về
bản thân" should not become durable memory.

## Deferred Work

The following are real future directions, but not V1 requirements:

- full conversation routing;
- recurring reminders;
- automatic briefings;
- deep people-aware meeting preparation;
- calendar or email integrations;
- specialist-agent orchestration;
- autonomous external actions;
- graph infrastructure before retrieval value is proven.

## Success Criteria

Memocore succeeds when it reduces cognitive and administrative load: fewer forgotten commitments,
cleaner memory, timely reminders, useful context retrieval, and bounded help without losing user
trust or control.
