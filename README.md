<div align="center">

# MemoCore

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-local--first-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)
![Ollama](https://img.shields.io/badge/Ollama-local--AI-111111?style=for-the-badge)
![Pytest](https://img.shields.io/badge/pytest-tested-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white)

**A local-first personal secretary backend for rough notes, reminders, memory, and open loops.**

<a href="#concept">Concept</a> ·
<a href="#features">Features</a> ·
<a href="#quick-start">Quick Start</a> ·
<a href="#windows-runtime">Windows Runtime</a> ·
<a href="#documentation">Documentation</a>

</div>

> MemoCore turns rough Telegram messages into structured work, evidence-backed memory, reminders,
> and auditable secretary actions while keeping local data under the user's control.

## Concept

MemoCore is not a generic chatbot. It is a personal secretary backend designed to reduce the need
to remember, re-explain, triage, and manually chase open loops.

Telegram is the first interface. Core behavior lives in services, repositories, typed schemas, and
provider abstractions so future email, calendar, voice, file, or web adapters can reuse the same
backend.

## Features

| Capability | Current state |
| --- | --- |
| Raw-note capture | Immutable raw notes with Telegram source idempotency. |
| Conversation routing | Deterministic and model-assisted routing for capture, query, correction, clarification, and casual messages. |
| Work extraction | Tasks, reminders, projects, people, meetings, follow-ups, memory candidates, and event logs. |
| Secretary queries | `/today`, `/tomorrow`, `/tasks`, `/reminders`, `/waiting`, `/projects`, and `/memory`. |
| Memory lifecycle | Candidate, active, rejected, superseded, and delete/forget flows. |
| Reliability | Schema validation, provider fallback, transactional derived writes, and audit events. |
| Time handling | Python-resolved relative dates for prompts and agenda views. |
| Reminder delivery | Leased reminder dispatch to avoid immediate duplicate sends. |
| Storage | SQLite verified runtime; PostgreSQL plus `pgvector` remains a blueprint. |

## Current Status

| Version | Status | Focus |
| --- | --- | --- |
| V1 | Delivered | Capture, extraction, SQLite storage, reminders, provider reliability, and memory hygiene. |
| V2 | Delivered | Conversational secretary routing, natural-language SQLite queries, clarification, and safer corrections. |
| V3 | Next | Daily briefings, recurring reminders, stale-loop nudges, quiet hours, and feedback signals. |

## Quick Start

### Linux or macOS Development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
```

### Windows Development

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -e ".[dev]"
Copy-Item .env.example .env
```

Edit `.env` with local secrets. Do not commit `.env`.

```env
TELEGRAM_BOT_TOKEN=your-token
DATABASE_PATH=data/memocore.db
USER_TIMEZONE=Asia/Ho_Chi_Minh
MODEL_PROVIDER=ollama
MODEL_NAME=qwen3:14b
```

Install the default local model when using Ollama:

```bash
ollama pull qwen3:14b
```

## Commands

| Command | Purpose |
| --- | --- |
| `.venv/bin/memocore models` | List configured provider profiles on Linux/macOS. |
| `.\.venv\Scripts\memocore models` | List configured provider profiles on Windows. |
| `.venv/bin/memocore run --provider ollama` | Run the bot with Ollama. |
| `.venv/bin/memocore run --provider groq` | Run the bot with Groq. |
| `.venv/bin/pytest -q` | Run the test suite on Linux/macOS. |
| `.\.venv\Scripts\pytest -q` | Run the test suite on Windows. |

Live extraction benchmarks are opt-in because they may call a real model/provider:

```bash
MEMOCORE_RUN_LIVE_BENCHMARK=1 .venv/bin/pytest tests/benchmark/test_extraction_benchmark.py -v
```

## Windows Runtime

The Windows PC is the primary runtime. The live Telegram bot should run through PM2 as exactly one
process named `memocore-ste`.

```powershell
pm2 list
.\scripts\windows\restart-memocore.ps1
.\scripts\windows\logs-memocore.ps1
```

Do not start a manual second bot while PM2 is online:

```powershell
.\.venv\Scripts\memocore run --provider groq
python -m memocore.cli.main run --provider groq
```

Running two bot instances with the same Telegram token causes:

```text
Conflict: terminated by other getUpdates request; make sure that only one bot instance is running
```

See [Windows Runtime Guide](docs/windows-runtime.md) for the full operating rule.

## Providers

Set `MODEL_PROVIDER` to one of:

| Provider | Default model | Key source |
| --- | --- | --- |
| `ollama` | `qwen3:14b` | Local Ollama server |
| `openai` | `gpt-4.1-nano` | `OPENAI_API_KEY` or `MODEL_API_KEY` |
| `gemini` | `gemini-2.5-flash` | `GEMINI_API_KEY` or `MODEL_API_KEY` |
| `deepseek` | `deepseek-chat` | `DEEPSEEK_API_KEY` or `MODEL_API_KEY` |
| `openrouter` | `meta-llama/llama-3.3-70b-instruct:free` | `OPENROUTER_API_KEY` or `MODEL_API_KEY` |
| `groq` | `llama-3.3-70b-versatile` | `GROQ_API_KEY` or `MODEL_API_KEY` |

Optional fallback:

```env
MODEL_FALLBACK_PROVIDER=openrouter
MODEL_FALLBACK_NAME=meta-llama/llama-3.3-70b-instruct:free
MODEL_FALLBACK_API_KEY=your-key
MODEL_FALLBACK_STRUCTURED_OUTPUT_MODE=auto
```

Legacy `OLLAMA_BASE_URL` and `OLLAMA_MODEL` are still accepted during migration.

## Tech Stack

| Layer | Technology |
| --- | --- |
| Runtime | Python 3.12+ |
| Messaging | Telegram bot |
| Storage | SQLite with packaged migrations |
| Models | Ollama and OpenAI-compatible HTTP providers |
| Schemas | Pydantic and Pydantic Settings |
| Async I/O | `python-telegram-bot`, `aiosqlite`, `httpx` |
| Tests | `pytest`, `pytest-asyncio` |
| Windows service | PM2 process `memocore-ste` |

## Project Structure

```text
src/memocore/
  app.py                         # dependency wiring and Telegram application setup
  config.py                      # .env-backed settings and provider overrides
  adapters/
    llm/                         # provider abstraction, Ollama, OpenAI-compatible providers
    storage/                     # SQLite runtime, migrations, repositories
    telegram/                    # Telegram handlers and bot creation
  domain/                        # typed domain models and schemas
  prompts/                       # extraction and intent-classification prompts
  services/                      # capture, conversation, reminders, memory, events, views
tests/                           # unit, integration, benchmark, and fixture tests
docs/                            # architecture, roadmap, runtime, storage, and harness notes
scripts/windows/                 # PM2 restart and log helpers for the Windows runtime
```

## Documentation

| Document | Purpose |
| --- | --- |
| [Engineering Guide](agent.md) | Code ownership boundaries, runtime rules, and engineering conventions. |
| [Architecture](docs/architecture.md) | Layering, capture flow, conversation flow, model boundary, and storage direction. |
| [Windows Runtime Guide](docs/windows-runtime.md) | Single-instance PM2 runtime policy for the primary Windows machine. |
| [Implementation Plan](implementation_plan.md) | Delivered V1/V2 scope and next-version plan. |
| [Roadmap](docs/personal-assistant-version-roadmap.md) | Product version roadmap from V1 through controlled autonomy. |
| [Agent Harness Direction](docs/agent-harness.md) | Future audited tool-use and approval boundary. |
| [Storage Migrations](docs/storage/README.md) | SQLite runtime and PostgreSQL/pgvector blueprint status. |
| [V2 Manual Tests](docs/v2-manual-test-cases.md) | Telegram messages for conversational secretary verification. |

## Data Safety

Never commit:

- `.env`
- `data/`
- local SQLite databases
- Telegram bot tokens
- provider API keys
- cookies, browser sessions, or generated runtime logs

The repository `.gitignore` excludes these local runtime files by default.
