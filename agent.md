# MemoCore Engineering Guide

This guide is for agents and contributors working inside the MemoCore repository. Keep it current
whenever runtime behavior, ownership boundaries, or verification commands change.

## Product Boundary

MemoCore is a local-first personal secretary backend, not a generic chatbot. It should reduce the
user's need to remember, re-explain, triage, and chase open loops manually.

Telegram is the first adapter. Core behavior belongs in services and repositories so future email,
calendar, voice, file, web, and worker adapters can reuse the same contracts.

## Architecture Map

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
      migrations/sqlite/
    telegram/
      bot.py
      handlers.py
  domain/
    models.py
    schemas.py
  prompts/
    system_extraction.md
    user_extraction.md
    system_intent_classification.md
    user_intent_classification.md
  services/
    capture_service.py
    clarification_service.py
    conversation_service.py
    event_service.py
    intent_classifier_service.py
    memory_service.py
    reminder_service.py
    secretary_service.py
    task_extraction_service.py
```

## Ownership Boundaries

- Adapters translate external protocols.
- Services own assistant behavior.
- Repositories persist state and do not call models or Telegram.
- Domain models and schemas describe notes, tasks, reminders, projects, people, meetings,
  follow-ups, memory, events, and structured model output.
- Raw notes remain separate from AI-derived data.
- User-visible actions and memory lifecycle changes must be auditable through event logs.
- Prompt ownership belongs in extraction and intent services, not providers.

LLM service code calls:

```python
await provider.chat(ChatRequest(...))
```

Providers expose `ProviderInfo`, `chat()`, and `health_check()`.

## Runtime Configuration

```env
TELEGRAM_BOT_TOKEN=...
DATABASE_PATH=data/memocore.db
LOG_LEVEL=INFO
USER_TIMEZONE=Asia/Ho_Chi_Minh

MODEL_PROVIDER=ollama
MODEL_NAME=qwen3:14b
MODEL_BASE_URL=
MODEL_API_KEY=
MODEL_TIMEOUT_SECONDS=60
MODEL_TEMPERATURE=0
MODEL_STRUCTURED_OUTPUT_MODE=auto
```

Default local model: `qwen3:14b`.

Hosted providers use provider-specific keys when available:

- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `DEEPSEEK_API_KEY`
- `OPENROUTER_API_KEY`
- `GROQ_API_KEY`

`MODEL_API_KEY` remains a generic active-provider override.

## Windows Runtime Rule

The Windows PC is the primary live runtime. Run the Telegram bot as one PM2-managed process named
`memocore-ste`. Do not run a second manual bot while PM2 is online.

Use:

```powershell
pm2 list
.\scripts\windows\restart-memocore.ps1
.\scripts\windows\logs-memocore.ps1
```

Avoid while PM2 is online:

```powershell
.\.venv\Scripts\memocore run --provider groq
python -m memocore.cli.main run --provider groq
```

Telegram long polling allows only one active `getUpdates` consumer per bot token. Duplicate bot
instances cause:

```text
Conflict: terminated by other getUpdates request; make sure that only one bot instance is running
```

Read [Windows Runtime Guide](docs/windows-runtime.md) before changing live-service commands.

## Development Setup

Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -e ".[dev]"
Copy-Item .env.example .env
```

Linux or macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
```

Never copy `.env`, `data/`, API keys, Telegram tokens, cookies, browser sessions, or local
databases into Git.

## Verification

Full suite:

```powershell
.\.venv\Scripts\pytest -q
```

Targeted examples:

```powershell
.\.venv\Scripts\pytest tests\unit\test_config.py -q
.\.venv\Scripts\pytest tests\unit\test_extraction_service.py -q
.\.venv\Scripts\pytest tests\integration\test_conversation_service.py -q
.\.venv\Scripts\pytest tests\integration\test_capture_flow.py -q
```

Provider profile check:

```powershell
.\.venv\Scripts\memocore models
```

The live extraction benchmark is opt-in because it can call real providers:

```bash
MEMOCORE_RUN_LIVE_BENCHMARK=1 .venv/bin/pytest tests/benchmark/test_extraction_benchmark.py -v
```

## Coding Conventions

- Keep I/O async end to end.
- Use Pydantic/domain models for structured data.
- Validate model output before persistence.
- Persist derived objects related to one note inside `database.transaction()`.
- Record event logs for user-visible and lifecycle transitions.
- Preserve idempotency by `source`, `source_chat_id`, and `source_message_id`.
- Keep Vietnamese matching helpers accent-tolerant through `_normalize_text`.
- Add tests for Vietnamese text with and without accents when adding routing rules.
- Prefer narrow service/repository changes over handler-level logic.

## Current Secretary Surface

- Capture rough notes.
- Answer natural-language agenda and state queries from SQLite.
- Track tasks, reminders, projects, people, meetings, follow-ups, and memory candidates.
- Ask clarification questions for ambiguous task updates or reminder times.
- Handle task completion, deadline updates, bulk cancellation, memory deletion, and correction
  feedback with guardrails.
- Provide Telegram commands: `/today`, `/tomorrow`, `/tasks`, `/reminders`, `/waiting`,
  `/projects`, and `/memory`.

## Deferred Work

- Recurring reminders and automatic briefings.
- Calendar, email, file, browser, and voice integrations.
- Broad autonomous tool execution.
- Peer-to-peer agent swarms.
- Separate graph database.
- Multi-user hosting.
- Rich dashboard UI before secretary workflows prove useful.
