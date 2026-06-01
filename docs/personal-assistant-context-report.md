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
- Keep adapters thin and services explicit.
- Separate raw inputs, interpreted memory, operational state, and external actions.
- Treat memory as a managed system with correction and forgetting, not append-only text.
- Prefer local-first, low-cost, self-hosted operation where practical.
- Keep model providers replaceable.
- Measure whether features reduce real administrative effort.

## Storage Direction

SQLite is appropriate for the verified local runtime. PostgreSQL with pgvector is the long-distance target for concurrent workers, backups, full-text search, and semantic retrieval.

Graph-like relationships are useful, but a separate graph database should wait until ordinary relational links fail a measured retrieval need.

## Product Risks

- False usefulness: saving notes without reducing workload.
- Memory contamination: treating guesses like durable facts.
- Premature autonomy: taking actions before approval and audit boundaries exist.
- Infrastructure overreach: adding complexity before secretary workflows prove useful.

## Success

Memocore succeeds when the user relies on it daily to capture information, recall context, prepare follow-ups, manage commitments, and increasingly coordinate bounded work without losing trust or control.
