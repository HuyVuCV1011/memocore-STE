# Architecture

## Principle

MemoCore is a personal secretary backend. Raw evidence, interpreted memory, operational state, and
user-visible actions are separate by design.

Telegram is the first adapter, not the product boundary.

## Layers

| Layer | Responsibility |
| --- | --- |
| Adapters | Telegram, LLM providers, SQLite runtime, future external tools |
| Services | Capture, extraction, conversation routing, memory lifecycle, reminders, secretary views, events |
| Domain | Typed notes, tasks, reminders, meetings, follow-ups, commitments, projects, people, organizations, decisions, memory, events, schemas |
| Storage | Repositories, transactions, indexes, migrations |

## Capture Flow

```mermaid
flowchart LR
    A["Telegram note"] --> B["Persist raw note"]
    B --> C["Extract structured data"]
    C --> D["Validate schema"]
    D --> E["Persist derived objects in one transaction"]
    E --> F["Record events and reply"]
```

Raw notes are stored before model calls. A repeated Telegram source message returns the existing
capture instead of duplicating work.

## Conversation Flow

Incoming Telegram text passes through a small `ConversationPlanner` before the legacy
`ConversationService` router. The planner owns multi-turn, entity-scoped actions that cannot be
represented safely as a single keyword intent.

```mermaid
flowchart LR
    A["Telegram message"] --> B["Clarification check"]
    B --> C["Resolve conversation focus"]
    C --> D["Conversation planner"]
    D -->|entity-scoped plan| F["Conversation executor"]
    D -->|no plan| E["Conversation router"]
    E -->|known intent| F
    E -->|unknown| M["Model-assisted intent classification"]
    M --> F
    F -->|capture| G["Capture service"]
    F -->|scoped knowledge update| H["Evidence-backed memory write"]
    F -->|query| I["Secretary SQLite retrieval"]
    F -->|correction| J["Memory or task lifecycle"]
    F -->|casual| K["Short acknowledgement"]
    G --> L["Reply"]
    H --> L
    I --> L
    J --> L
    K --> L
```

`ConversationComposer` owns shared prompts and confirmations. `ConversationService` is the
compatibility orchestrator while legacy branches move behind Router, Planner, Context Resolver,
Executor, and Composer boundaries.

Deterministic routes are preferred for high-frequency queries and sensitive mutations. Model
classification is a fallback for less obvious language, and low-confidence write intents should ask
for clarification instead of mutating durable state.

Entity-scoped knowledge updates preserve the raw source note, split explicit list payloads into
separate memory claims, attach the canonical person/project id, and keep the entity as the current
conversation focus. They do not depend on model extraction when the user's update instruction and
payload are explicit.

Typed contextual references such as `dự án này` are resolved before scanning message text for
entity names. This prevents ordinary words such as `nghĩa là` from fuzzy-matching a person named
Nghĩa. A knowledge update is also treated as one reversible batch through its source note, so
“xóa 3 thông tin vừa cập nhật” removes only that batch and leaves unrelated memory untouched.

## Model Boundary

`ExtractionService` and `IntentClassifierService` own prompt construction. Providers implement:

```python
chat(ChatRequest) -> ChatResponse
```

Fallback applies to request failures and invalid model output. Relative dates are resolved in
Python before prompt construction.

## Storage Direction

SQLite is the verified runtime. It supports:

- raw notes and source idempotency;
- tasks, reminders, projects, people, meetings, follow-ups, and commitments;
- person/project links for operational context and meeting preparation;
- first-class organizations and source-linked decisions;
- memory items with lifecycle status;
- event logs for auditability;
- packaged schema bootstrap and migrations.

PostgreSQL plus `pgvector` remains a blueprint for future retrieval, concurrency, backup, and
deployment needs. Do not claim production PostgreSQL support until a runtime adapter and contract
tests exist.

## Runtime Direction

The primary live runtime is the Windows PC. The Telegram bot should run through the PM2 process
`memocore-ste` as a single polling instance. `memocore doctor` is the preflight boundary for
configuration, provider settings, SQLite integrity, Telegram command registration, runtime data,
and PM2 state. See [Windows Runtime Guide](windows-runtime.md).

## Agent Harness Direction

The current extraction and conversation paths are narrow harnesses: they manage bounded context,
provider calls, validation, retries, fallback, transactional persistence, and audit events.

Future calendar, email, file, and worker capabilities should use a broader audited harness rather
than calling external systems directly. See [Agent Harness Direction](agent-harness.md).

These capabilities remain held until the gates in
[Conversation Stability Gates](conversation-stability-gates.md) pass in both automated transcript
evaluation and real Telegram usage.
