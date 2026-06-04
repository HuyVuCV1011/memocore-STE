# Personal Secretary Context

## Intent

Memocore is a long-term personal secretary system centered on capture, memory, follow-up, planning, and practical daily support. It is not a generic chatbot, not only a note archive, and not a code-writing toy.

The assistant should reduce the user's need to remember, re-explain, manually triage, and manually chase open loops. Telegram is the first low-friction input surface, while the backend should remain reusable by future web, voice, email, calendar, file, and worker-agent adapters.

## Product Shape

The project should grow in versions:

- V1: Capture and memory foundation.
- V2: Conversational secretary.
- V3: Daily and recurring secretary.
- V4: People, projects, and meetings.
- V5: Orchestration and specialist agents.
- V6: Knowledge system and productization.
- V7: Controlled autonomy.

Version 1 is allowed to have internal implementation checkpoints, but those checkpoints are still part of V1. Memory trust basics belong in the V1 closeout because V2 conversation will be unreliable if V1 memory is already contaminated.

## Secretary Charter

Memocore should:

- preserve raw evidence separately from interpreted data;
- convert rough notes into useful structured work;
- remember durable preferences, project context, and corrections;
- track commitments, reminders, meetings, follow-ups, waiting items, and blocked work;
- surface the right context before it is forgotten;
- learn cautiously from explicit feedback;
- execute sensitive actions only inside inspectable approval rules.

## Operating Principles

- Build slowly but keep architecture durable.
- Deliver visible secretary value in thin end-to-end slices.
- Keep adapters thin and services explicit.
- Separate raw inputs, interpreted memory, operational state, and external actions.
- Treat memory as a managed system with correction, rejection, supersession, and forgetting.
- Prefer local-first, low-cost, self-hosted operation where practical.
- Keep model providers replaceable.
- Add infrastructure when a secretary workflow needs it and expand it from measured usage.
- Measure whether features reduce real administrative effort.

## Default Interaction Profile

These defaults are starting points. The assistant should learn from explicit corrections and cautious observation, while keeping preferences editable.

- Reply in the language of the user's latest message.
- Support Vietnamese, English, and mixed messages naturally.
- Use a warm, competent, concise tone.
- Learn preferred Vietnamese pronouns explicitly rather than guessing.
- Bundle low-priority nudges and respect quiet hours once proactive features exist.
- Ask before taking external or sensitive actions.
- Keep the assistant identity subtle unless the user chooses a stronger persona.

## Version 1 Boundary

V1 should finish with a dependable local-first capture system:

- Telegram capture and raw-note storage.
- Structured extraction into notes, tasks, reminders, projects, memory items, people, meetings, follow-ups, and events.
- SQLite runtime with model-provider abstraction.
- Single-shot reminder dispatch.
- Basic secretary commands for today's work, tasks, reminders, projects, memory, and waiting items.
- Memory hygiene strong enough to avoid obvious contamination before V2.

V1 should not try to solve full natural conversation, recurring reminders, calendar integration, specialist-agent orchestration, or autonomous external action.

## Memory Direction

Memory must not be a flat append-only transcript. V1 should distinguish:

- profile memory: durable facts, preferences, boundaries;
- project memory: project facts, project state, open loops;
- interaction memory: corrections, feedback, and behavior signals.

V1 memory only needs simple lifecycle operations, but those operations must be explicit: candidate, active, rejected, superseded, forget/redact. V2 and later can add richer retrieval, ranking, and conversational correction workflows.

## Storage Direction

SQLite is appropriate for the verified local runtime. PostgreSQL with pgvector is a long-distance option for concurrent workers, backups, full-text search, and semantic retrieval after secretary workflows prove a measured need.

Start with structured SQLite retrieval by person, project, bucket, status, and recency. Add full-text search, graph-like relationships, PostgreSQL, and pgvector only when measured workflows need them.

## Product Risks

- False usefulness: saving notes without reducing workload.
- Memory contamination: treating questions, guesses, or transient thoughts like durable facts.
- Premature autonomy: taking action before approval and audit boundaries exist.
- Infrastructure overreach: adding graph, orchestration, or dashboard complexity before secretary workflows prove useful.
- Provider overfitting: depending on quirks of one LLM instead of keeping behavior in services and schemas.

## Success

Memocore succeeds when the user feels that a dependable secretary is carrying part of the administrative load: fewer forgotten commitments, timely follow-ups, useful context reconstruction, clean memory, and bounded help without losing trust or control.

Stored-item counts are supporting metrics. The real goal is reduced cognitive and administrative burden.
