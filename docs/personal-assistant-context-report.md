# Personal Secretary Context

## Intent

MemoCore is a long-term personal secretary system centered on capture, memory, follow-up,
planning, and practical daily support. It is not a generic chatbot, a flat note archive, or a
free-form autonomous agent.

The assistant should reduce the user's need to remember, re-explain, manually triage, and manually
chase open loops.

## Product Shape

| Version | Product focus |
| --- | --- |
| V1 | Capture and memory foundation |
| V2 | Conversational secretary |
| V3 | Daily and recurring secretary |
| V4 | People, projects, and meetings |
| V5 | Orchestration and specialist workers |
| V6 | Knowledge system and productization |
| V7 | Controlled autonomy |

## Secretary Charter

MemoCore should:

- preserve raw evidence separately from interpreted data;
- convert rough notes into useful structured work;
- remember durable preferences, project context, and corrections;
- track commitments, reminders, meetings, follow-ups, waiting items, and blocked work;
- surface the right context before it is forgotten;
- learn cautiously from explicit feedback;
- execute sensitive actions only inside inspectable approval rules.

## Operating Principles

- Build thin end-to-end secretary experiences.
- Keep adapters thin and services explicit.
- Separate raw inputs, interpreted memory, operational state, and external actions.
- Treat memory as managed state with correction, rejection, supersession, and forgetting.
- Prefer local-first, low-cost, self-hosted operation where practical.
- Keep model providers replaceable.
- Add infrastructure when a measured secretary workflow needs it.
- Measure whether features reduce real administrative effort.

## Interaction Profile

Starting defaults:

- Reply in the language of the user's latest message.
- Support Vietnamese, English, and mixed messages naturally.
- Use a warm, competent, concise tone.
- Learn preferred pronouns explicitly rather than guessing.
- Ask before taking external or sensitive actions.
- Keep assistant identity subtle unless the user chooses a stronger persona.

## Memory Direction

Memory must not be a flat append-only transcript. MemoCore distinguishes:

| Bucket | Purpose |
| --- | --- |
| `profile` | Durable user facts, preferences, boundaries, and identity details |
| `project` | Project facts, project state, decisions, and open loops |
| `interaction` | Corrections, feedback, and behavior signals |

Memory lifecycle operations must remain explicit: candidate, active, rejected, superseded, deleted,
and forgotten/redacted.

## Product Risks

- False usefulness: saving notes without reducing workload.
- Memory contamination: treating questions, guesses, or transient thoughts as durable facts.
- Premature autonomy: taking action before approval and audit boundaries exist.
- Infrastructure overreach: adding graph, orchestration, or dashboards before workflows prove value.
- Provider overfitting: depending on one model's quirks instead of service and schema behavior.

## Success Criteria

MemoCore succeeds when it reduces cognitive and administrative load:

- fewer forgotten commitments;
- cleaner memory;
- timely reminders and follow-ups;
- useful context reconstruction;
- bounded help without loss of trust or control.

Stored-item counts are supporting metrics. Reduced administrative burden is the real goal.
