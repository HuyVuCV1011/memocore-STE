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
| Domain | Typed notes, tasks, reminders, meetings, follow-ups, commitments, projects, people, memory, events, schemas |
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

Incoming Telegram text passes through `ConversationService` before capture or mutation.

```mermaid
flowchart LR
    A["Telegram message"] --> B["Clarification check"]
    B --> C["Conversation service"]
    C --> D["Deterministic route"]
    D -->|known intent| F["Action executor"]
    D -->|unknown| E["Model-assisted intent classification"]
    E --> F
    F -->|capture| G["Capture service"]
    F -->|query| H["Secretary SQLite retrieval"]
    F -->|correction| I["Memory or task lifecycle"]
    F -->|casual| J["Short acknowledgement"]
    G --> K["Reply"]
    H --> K
    I --> K
    J --> K
```

Deterministic routes are preferred for high-frequency queries and sensitive mutations. Model
classification is a fallback for less obvious language, and low-confidence write intents should ask
for clarification instead of mutating durable state.

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
