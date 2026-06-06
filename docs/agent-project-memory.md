# Agent Project Memory

This file captures durable project context for future coding agents. It is not a secret vault.
Never place real Telegram tokens, API keys, cookies, browser sessions, or local database contents
in this document.

## Project Summary

MemoCore is a local-first personal secretary backend. It receives rough Telegram messages, stores
raw notes, extracts structured work, manages memory candidates, tracks reminders and open loops,
and records auditable events.

The product goal is practical secretary behavior: reduce forgotten commitments, repeated context
explanations, manual triage, and manual follow-up chasing.

## Stack

| Area | Technology |
| --- | --- |
| Runtime | Python 3.12+ |
| Telegram adapter | `python-telegram-bot` |
| Schemas and config | `pydantic`, `pydantic-settings` |
| Storage | SQLite through `aiosqlite` |
| HTTP/model calls | `httpx` |
| Local model provider | Ollama |
| Hosted providers | OpenAI-compatible endpoints: OpenAI, Gemini, DeepSeek, OpenRouter, Groq |
| Tests | `pytest`, `pytest-asyncio` |

## Verified Runtime

SQLite is the verified local runtime. PostgreSQL plus `pgvector` exists only as a blueprint in
`docs/storage/postgres/001_secretary_foundation.sql`.

Default local model: `qwen3:14b`.

The Windows PC is the primary live runtime. Mac may be used for editing, Git sync, or remote
control, but it should not keep a second live bot process with the same Telegram token.

## Windows Runtime Rule

Run the live bot through one PM2 process named `memocore-ste`.

Use:

```powershell
pm2 list
.\scripts\windows\restart-memocore.ps1
.\scripts\windows\logs-memocore.ps1
```

Do not run these while PM2 is online:

```powershell
.\.venv\Scripts\memocore run --provider groq
python -m memocore.cli.main run --provider groq
```

Telegram long polling permits only one active `getUpdates` consumer per bot token. Duplicate bot
instances cause:

```text
Conflict: terminated by other getUpdates request; make sure that only one bot instance is running
```

If this appears, check local processes first:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'memocore.cli.main|memocore-STE|memocore.exe' } |
  Select-Object ProcessId,ParentProcessId,Name,CommandLine
```

The PM2-managed process may show a parent and child Python process. That is normal. Stop only extra
manual runtimes outside PM2.

See `docs/windows-runtime.md`.

## Setup Commands

Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\pytest -q
.\.venv\Scripts\memocore models
```

Linux or macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
.venv/bin/pytest -q
.venv/bin/memocore models
```

Install the local Ollama model when using `MODEL_PROVIDER=ollama`:

```bash
ollama pull qwen3:14b
```

## Required Local Configuration

Create `.env` from `.env.example`. Fill real values locally only.

```env
TELEGRAM_BOT_TOKEN=
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

OPENAI_API_KEY=
GEMINI_API_KEY=
DEEPSEEK_API_KEY=
OPENROUTER_API_KEY=
GROQ_API_KEY=

MODEL_FALLBACK_PROVIDER=
MODEL_FALLBACK_NAME=
MODEL_FALLBACK_BASE_URL=
MODEL_FALLBACK_API_KEY=
MODEL_FALLBACK_STRUCTURED_OUTPUT_MODE=auto
```

Do not commit `.env`, `data/`, local databases, provider keys, Telegram tokens, cookies, sessions,
or runtime logs.

## Architecture Notes

Read these before changing behavior:

1. `README.md`
2. `agent.md`
3. `docs/architecture.md`
4. `docs/v1-spec.md`
5. `docs/storage/README.md`
6. `docs/windows-runtime.md`

Code path to inspect:

```text
src/memocore/app.py
src/memocore/adapters/telegram/handlers.py
src/memocore/services/conversation_service.py
src/memocore/services/capture_service.py
src/memocore/services/task_extraction_service.py
src/memocore/adapters/storage/repositories.py
```

Core flow:

1. Telegram update reaches `message_handler`.
2. Pending clarification is handled first.
3. `ConversationService` routes deterministic intents, then model-assisted classification if needed.
4. Capture intents go to `CaptureService`.
5. Raw note is persisted before any model call.
6. `ExtractionService` builds prompts, calls the provider, and validates `NoteExtraction`.
7. Derived objects are written transactionally.
8. Event logs record visible actions and lifecycle transitions.

## Engineering Rules

- Keep Telegram handlers thin.
- Keep provider-specific behavior inside provider adapters.
- Keep persistence inside repositories.
- Keep assistant behavior inside services.
- Validate model output with schemas before persistence.
- Add event logs for important note, task, reminder, memory, and correction transitions.
- Add tests for Vietnamese routing and correction edge cases.
- Never convert the project into a generic chatbot or broad autonomous agent.

## Known Pitfalls

- Duplicate Telegram polling can happen if PM2 and a manual process both run the bot.
- Windows needs `tzdata` for reliable `ZoneInfo("Asia/Ho_Chi_Minh")`.
- Hosted providers require keys; missing keys should be visible through `memocore models`.
- Small local models can return schemas instead of extraction data.
- Invalid model JSON should fail the note gracefully and create `MODEL_OUTPUT_INVALID`.
- Relative dates should be resolved in Python before prompting.
- Query-like messages must not become durable memory or accidental tasks.
- Memory correction and deletion use heuristic matching; test accent and no-accent Vietnamese.

## Verification Checklist

Run the full suite after shared service, repository, provider, prompt, or routing changes:

```powershell
.\.venv\Scripts\pytest -q
```

Targeted examples:

```powershell
.\.venv\Scripts\pytest tests\integration\test_conversation_service.py -q
.\.venv\Scripts\pytest tests\unit\test_handlers.py -q
.\.venv\Scripts\pytest tests\unit\test_extraction_service.py -q
.\.venv\Scripts\pytest tests\integration\test_capture_flow.py -q
```

Check provider profiles:

```powershell
.\.venv\Scripts\memocore models
```

Use live benchmarks only when the user agrees:

```bash
MEMOCORE_RUN_LIVE_BENCHMARK=1 .venv/bin/pytest tests/benchmark/test_extraction_benchmark.py -v
```
