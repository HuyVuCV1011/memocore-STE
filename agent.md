# Memocore Engineering Guide

## Product Boundary

Memocore is a personal secretary backend, not a generic chatbot. It should reduce the user's need to remember, re-explain, triage, and manually chase open loops.

Telegram is the first adapter. Core behavior belongs in services and repositories so future email, calendar, voice, file, and web interfaces can reuse the same contracts.

## Current Architecture

```text
src/memocore/
  app.py
  config.py
  adapters/
    llm/
      base.py
      ollama_provider.py
      openai_provider.py
      provider_factory.py
    storage/
      sqlite.py
      repositories.py
    telegram/
      bot.py
      handlers.py
  domain/
    models.py
    schemas.py
  prompts/
    system_extraction.md
    user_extraction.md
  services/
    capture_service.py
    event_service.py
    memory_service.py
    reminder_service.py
    secretary_service.py
    task_extraction_service.py
```

## Boundaries

- Adapters translate external protocols.
- Services own assistant behavior.
- Domain models describe notes, work, people, projects, memories, meetings, follow-ups, and events.
- Repositories persist state and do not call models or Telegram.
- Raw notes remain separate from AI-derived data.
- User-visible actions and memory lifecycle changes must be auditable.

LLM service code calls:

```python
await provider.chat(ChatRequest(...))
```

Providers expose `ProviderInfo`, `chat()`, and `health_check()`. Prompt ownership belongs to `ExtractionService`.

## Runtime Configuration

```env
TELEGRAM_BOT_TOKEN=...
DATABASE_PATH=data/memocore.db
MODEL_PROVIDER=ollama
MODEL_NAME=qwen3:14b
MODEL_BASE_URL=http://127.0.0.1:11434
MODEL_API_KEY=
MODEL_STRUCTURED_OUTPUT_MODE=auto
```

The default local extraction model is `qwen3:14b`. Use small models only for smoke testing when reliability is not required.

## Storage Direction

SQLite remains the verified local runtime. Start with structured SQLite retrieval. Add full-text
search, PostgreSQL, and pgvector only when measured secretary workflows demonstrate a retrieval,
concurrency, backup, or deployment need. Do not introduce a separate graph database until
ordinary relational links fail a measured retrieval need.

## Current Secretary Features

- Idempotent raw capture.
- Tasks with candidate, open, waiting, blocked, done, and cancelled states.
- Reminders with leased dispatch claims.
- Projects, people, meetings, follow-ups, memory candidates, and events.
- `/today`, `/waiting`, `/projects`, and `/memory` Telegram commands.

## Near-Term Work

The next implementation milestone is the conversation loop: classify Telegram messages as
capture, question, instruction, correction, or casual conversation; retain bounded recent
context; answer simple questions from SQLite; ask clarifying questions; and replace
extraction-count replies with useful confirmations.

After the conversation loop, deliver automatic briefings and proactive nudges, then people-aware
meeting preparation. Grow the harness alongside integrations: begin with an audited boundary for
calendar reads, then add approvals, retries, idempotency controls, and append-only traces before
write-capable integrations. See [agent harness direction](docs/agent-harness.md).

## Deferred Work

- Broad autonomous tool execution.
- Peer-to-peer agent swarms.
- Separate graph database.
- Multi-user hosting.
- Rich dashboard UI before secretary workflows prove useful.
