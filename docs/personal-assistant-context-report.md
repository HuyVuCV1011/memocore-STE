# Personal Secretary Context

## Intent

Memocore is a long-term personal secretary system centered on management, follow-up, memory, and practical daily support. It is not a generic chatbot and not merely a note archive.

The assistant should reduce the user's need to remember, re-explain, manually triage, and manually chase open loops. Telegram is the first low-friction capture surface, while the backend remains reusable by future email, calendar, voice, file, and web adapters.

## Secretary Charter

Memocore should:

- preserve raw evidence exactly as received;
- convert rough notes into useful structured work;
- remember durable preferences, project context, and corrections;
- track commitments, meetings, follow-ups, waiting items, and blocked work;
- surface the right context before it is forgotten;
- learn cautiously from explicit feedback;
- execute sensitive actions only inside inspectable approval rules.

## Operating Principles

- Build slowly but keep architecture durable.
- Deliver visible secretary value in thin end-to-end slices.
- Keep adapters thin and services explicit.
- Separate raw inputs, interpreted memory, operational state, and external actions.
- Treat memory as a managed system with correction and forgetting, not append-only text.
- Prefer local-first, low-cost, self-hosted operation where practical.
- Keep model providers replaceable.
- Add infrastructure when a secretary workflow needs it and expand it from measured usage.
- Measure whether features reduce real administrative effort.

## Default Interaction Profile

These defaults are starting points. The assistant should learn from explicit corrections and
cautious observation, while keeping preferences editable.

- Use a warm, competent, concise tone. Avoid stiff forms of address and avoid excessive
  informality.
- Reply in the language of the user's latest message. Support Vietnamese, English, and mixed
  messages naturally.
- Learn preferred Vietnamese pronouns explicitly. Do not guess them from nationality, age, or
  relationship context alone.
- Start with an automatic morning briefing, event-driven reminders, and a weekly review.
- Make the end-of-day review opt-in. Bundle low-priority nudges and respect quiet hours.
- Be proactive about open loops, but ask before taking external actions.
- Begin with a subtle assistant identity. A personal name or stronger persona is optional and
  should be chosen by the user.

## Delivery Priorities

1. Conversational understanding and clarifying questions.
2. Morning briefings, deadline warnings, and stale follow-up nudges.
3. People-aware commitments and meeting preparation.
4. Calendar read access.
5. Communication drafting.
6. Approval-gated writes and sends.
7. Preference learning and deeper retrieval infrastructure.

## Storage Direction

SQLite is appropriate for the verified local runtime. PostgreSQL with pgvector is the long-distance target for concurrent workers, backups, full-text search, and semantic retrieval.

Start with structured SQLite retrieval by person, project, status, and recency. Add full-text
search, PostgreSQL, and pgvector only when measured secretary workflows need them.

Graph-like relationships are useful, but a separate graph database should wait until ordinary relational links fail a measured retrieval need.

## Product Risks

- False usefulness: saving notes without reducing workload.
- Memory contamination: treating guesses like durable facts.
- Premature autonomy: taking actions before approval and audit boundaries exist.
- Infrastructure overreach: adding complexity before secretary workflows prove useful.

## Success

Memocore succeeds when the user feels that a dependable secretary is carrying part of the
administrative load: fewer forgotten commitments, timely follow-ups, useful meeting preparation,
less manual context reconstruction, and bounded work completed without losing trust or control.

Measure outcomes such as commitments recovered, follow-ups surfaced, preparation delivered,
suggestions accepted, and manual work avoided. Stored-item counts are supporting metrics, not the
goal.
