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
| Work and context state | Tasks, reminders, projects, people, meetings, follow-ups, commitments, memory candidates, and event logs. |
| Secretary queries | Compact `/` menu for `/today`, `/work`, `/memory`, `/context`, `/briefing`, and `/capture`; power-user shortcuts remain available. |
| Memory lifecycle | Candidate, active, rejected, superseded, and delete/forget flows. |
| V4 context retrieval | Person/project context, linked commitments, meeting preparation summaries, and SQLite retrieval by linked entities. |
| Reliability | Schema validation, provider fallback, transactional derived writes, and audit events. |
| Time handling | Python-resolved relative dates for prompts and agenda views. |
| Reminder delivery | Leased reminder dispatch to avoid immediate duplicate sends. |
| Storage | SQLite verified runtime; PostgreSQL plus `pgvector` remains a blueprint. |

## Telegram Command Model

Telegram shows a small command menu. The visible `/` menu is intentionally limited to the main
entry points:

| Command | Behavior |
| --- | --- |
| `/today` | Show the day's top priorities, due work, reminders, and meetings. |
| `/work` | Open a work dashboard with tasks, reminders, waiting items, and commitments. |
| `/memory` | Open the memory dashboard with review/stale/topic slices. |
| `/context` | Open people, projects, meeting prep, and memory navigation. |
| `/briefing` | Generate the current daily briefing. |
| `/capture` | Show quick capture patterns for tasks, memory, and content notes. |

Power-user shortcuts still work but are kept out of the visible command menu:
`/task`, `/t`, `/mem`, `/m`, `/li`, `/linkedin`, `/tasks`, `/reminders`, `/waiting`,
`/projects`, `/people`, `/person <name>`, `/project <name>`, `/context <name>`,
`/prep <name>`, `/weekly`, `/endday`, `/goals`, `/people review`, `/projects review`,
`/memory review`, and `/memory stale`.

Trailing action hashtags also force deterministic capture when placed at the end of a message:
`#li`, `#linkedin`, `#task`, `#t`, `#remind`, `#r`, `#mem`, and `#m`.

## Current Status

| Version | Status | Focus |
| --- | --- | --- |
| V1 | Delivered | Capture, extraction, SQLite storage, reminders, provider reliability, and memory hygiene. |
| V2 | Delivered | Conversational secretary routing, natural-language SQLite queries, clarification, and safer corrections. |
| V3 | Delivered | Daily briefings, recurring daily/weekly reminders, stale-loop nudges, quiet hours, and weekly reviews. |
| V4 | Active | Telegram UX, open-loop intelligence, evidence-backed context, entity/memory hygiene, daily rituals, and goals. |
| V5 | Held | Bounded orchestration remains intentionally postponed while V4 is deepened and validated. |

V3 intentionally defers a few deeper behaviors to V4/V5 unless they become painful sooner:

| Deferred item | Current decision |
| --- | --- |
| Interval recurrence | Daily and weekly recurrence are supported. Rules like "every 2 days" or "every 3 weeks" are deferred. |
| Nudge bundling | Quiet hours and cooldown exist. Bundling many low-priority nudges into one digest is deferred. |
| Feedback signals | Correction feedback exists. Explicit accepted, edited, ignored, and rejected suggestion signals are deferred. |

V4 adds linked operational context without importing private data automatically:

| V4 capability | Current state |
| --- | --- |
| People and aliases | Extracted from explicit named-person evidence, stored in SQLite, and retrievable through `/people` and `/person <name>`. |
| Operational ingestion | Meetings, follow-ups, and directional commitments are extracted and linked when confidence and entity references are safe. |
| Commitments | Tracks `user_owes`, `owed_to_user`, and `mutual` commitments by person/project without guessing an unclear direction. |
| Meeting preparation | `/prep <person or project>` summarizes linked commitments, tasks, follow-ups, meetings, and memory. |
| Linked retrieval | Tasks, meetings, follow-ups, commitments, and memory can be retrieved by person or project id. |
| Personal context import | Still review-gated; large private context files should produce Markdown/import plans before any database write. |

Current V4 deepening also includes a six-command Telegram menu, inline navigation hubs, ranked work
priorities, evidence metadata in context/prep views, review-gated entity matching, paginated memory
triage, end-of-day and weekly rituals, lightweight goals, and a runtime `doctor` preflight.

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
| `.venv/bin/memocore doctor` | Check config, SQLite, Telegram slash menu, PM2, and runtime data. |
| `.\.venv\Scripts\memocore models` | List configured provider profiles on Windows. |
| `.\.venv\Scripts\memocore doctor` | Check config, SQLite, Telegram slash menu, PM2, and runtime data on Windows. |
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
| [Telegram Command Model](docs/telegram-command-model.md) | Visible command menu, hidden shortcuts, inline hubs, and verification rules. |
| [Implementation Plan](implementation_plan.md) | Delivered foundations, active V4 work, and held future versions. |
| [Roadmap](docs/personal-assistant-version-roadmap.md) | Product version roadmap from V1 through controlled autonomy. |
| [Agent Harness Direction](docs/agent-harness.md) | Future audited tool-use and approval boundary. |
| [0.5.0 Readiness](docs/version-0.5-readiness.md) | Verified capability audit, release scope, risks, and acceptance gates. |
| [Storage Migrations](docs/storage/README.md) | SQLite runtime and PostgreSQL/pgvector blueprint status. |
| [V2 Manual Tests](docs/v2-manual-test-cases.md) | Telegram messages for conversational secretary verification. |
| [Content Integration Bridge](docs/content_integration_bridge.md) | Read-only SQLite contract for the LinkedIn content engine. |
| [Telegram Memory UX](docs/telegram-memory-ux-spec.md) | Dashboard, slice, and review model for high-volume Telegram memory browsing. |

## Data Safety

Commit source, tests, docs, migrations, and safe examples such as `.env.example`.

Never commit:

- `.env`
- `.env.*` except `.env.example`
- `data/`
- local SQLite databases
- `logs/`
- `*.jsonl` runtime feedback/event exports
- Telegram bot tokens
- provider API keys
- private keys, certificates, OAuth tokens, service-account files, or client secret files
- cookies, browser sessions, or generated runtime logs

The repository `.gitignore` excludes these local runtime files by default.
