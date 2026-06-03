<div align="center">

# MemoCore

![Python](https://img.shields.io/badge/Python-3.x-3776AB?style=for-the-badge&logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-local--first-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)
![Ollama](https://img.shields.io/badge/Ollama-local--AI-111111?style=for-the-badge)

**A local-first personal secretary backend for rough notes, reminders, memory, and open loops.**

<a href="#-concept">Concept</a> ·
<a href="#-features">Features</a> ·
<a href="#-quick-start">Quick Start</a> ·
<a href="#-providers">Providers</a> ·
<a href="#-documentation">Documentation</a>

</div>

> MemoCore captures rough Telegram notes, extracts structured work, remembers context, sends reminders, and surfaces open loops.

---

## 💡 Concept

MemoCore is a local-first personal secretary backend. It is designed to turn unstructured Telegram notes into durable tasks, reminders, projects, memory candidates, meetings, follow-ups, and audit events while keeping the verified runtime simple.

---

## ✨ Features

| Capability | Current state |
| --- | --- |
| Raw-note capture | Immutable raw-note capture with idempotency. |
| Work extraction | Tasks, reminders, projects, memory candidates, meetings, follow-ups, and audit events. |
| Model support | Ollama plus OpenAI-compatible hosted providers. |
| Reliability | Validation-aware provider fallback and transactional derived writes. |
| Time handling | Deterministic relative-date prompt context. |
| Reminder delivery | Leased reminder dispatch. |
| Telegram commands | `/today`, `/waiting`, `/projects`, and `/memory`. |
| Storage | SQLite runtime with a PostgreSQL and `pgvector` migration blueprint. |

---

## 🗺️ Current State

The repository includes V1, V1.1, V1.2, and the local V1.5 secretary foundation.

| Version family | Foundation |
| --- | --- |
| V1 through V1.2 | Core capture, extraction, memory, task, and reminder behaviors. |
| Local V1.5 | Secretary foundation with provider selection, fallback, Telegram commands, SQLite runtime, and migration planning. |

---

## 🚀 Quick Start

### Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=your-token
DATABASE_PATH=data/memocore.db
MODEL_PROVIDER=ollama
MODEL_NAME=qwen3:4b
# Optional: MODEL_BASE_URL=http://127.0.0.1:11434
```

Install the default local model:

```bash
ollama pull qwen3:4b
```

### Run

```bash
.venv/bin/memocore
```

List configured model profiles:

```bash
.venv/bin/memocore models
```

Select a provider when starting MemoCore:

```bash
.venv/bin/memocore run --provider ollama
.venv/bin/memocore run --provider gemini
.venv/bin/memocore run --provider groq
.venv/bin/memocore run --provider openrouter
```

Each hosted provider uses its default model unless `--model` is supplied:

```bash
.venv/bin/memocore run --provider gemini --model gemini-2.5-flash
```

### Test

```bash
.venv/bin/pytest -q
```

Run the opt-in live extraction benchmark:

```bash
MEMOCORE_RUN_LIVE_BENCHMARK=1 .venv/bin/pytest tests/benchmark/test_extraction_benchmark.py -v
```

---

## 💬 Telegram Commands

| Command | Purpose |
| --- | --- |
| `/today` | Show due and overdue work. |
| `/waiting` | Show blocked, waiting, and follow-up items. |
| `/projects` | Show captured projects. |
| `/memory` | Show recent active memory candidates. |

---

## 🤖 Providers

Set `MODEL_PROVIDER` to `ollama`, `openai`, `gemini`, `deepseek`, `openrouter`, or `groq`. Hosted providers require `MODEL_API_KEY`. Gemini is routed through its OpenAI-compatible endpoint.

For CLI switching, store provider-specific keys such as `GEMINI_API_KEY`, `GROQ_API_KEY`, and `OPENROUTER_API_KEY` in `.env`. `MODEL_API_KEY` remains supported as a generic active-provider override.

Optional fallback:

```env
MODEL_FALLBACK_PROVIDER=openai
MODEL_FALLBACK_NAME=gpt-4.1-nano
MODEL_FALLBACK_API_KEY=your-key
MODEL_FALLBACK_STRUCTURED_OUTPUT_MODE=auto
```

Legacy `OLLAMA_BASE_URL` and `OLLAMA_MODEL` variables remain accepted during migration.

---

## 🏗️ Tech Stack

| Layer | Technology |
| --- | --- |
| Runtime | Python |
| Local storage | SQLite |
| Future storage blueprint | PostgreSQL with `pgvector` |
| Messaging interface | Telegram bot |
| Local model provider | Ollama |
| Hosted model providers | OpenAI-compatible providers, Gemini, DeepSeek, OpenRouter, Groq |

<details>
<summary>📁 Storage Note</summary>

SQLite is the verified local runtime. PostgreSQL with `pgvector` remains a blueprint for measured concurrency, backup, full-text search, or semantic retrieval needs after secretary workflows are proven. See [storage migrations](docs/storage/README.md).

</details>

---

## 📚 Documentation

| Document | Purpose |
| --- | --- |
| [Implementation plan](implementation_plan.md) | Implementation direction |
| [Engineering guide](agent.md) | Engineering guide |
| [Architecture](docs/architecture.md) | System architecture |
| [Agent harness direction](docs/agent-harness.md) | Agent harness direction |
| [Roadmap](docs/personal-assistant-version-roadmap.md) | Personal assistant roadmap |
| [V1 specification](docs/v1-spec.md) | V1 specification |
| [Archived V1 evaluation](docs/v1-evaluation.md) | Archived V1 evaluation |

---

## 🔐 Security

Never commit `.env`, local databases, API keys, Telegram tokens, or generated runtime files. The included `.gitignore` excludes these by default.
