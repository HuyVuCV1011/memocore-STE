# Memocore

Memocore is a local-first personal secretary backend. It captures rough Telegram notes, extracts structured work, remembers context, sends reminders, and surfaces open loops.

## Current State

The repository includes V1, V1.1, V1.2, and the local V1.5 secretary foundation:

- Immutable raw-note capture with idempotency.
- Tasks, reminders, projects, memory candidates, meetings, follow-ups, and audit events.
- Ollama plus OpenAI-compatible hosted providers.
- Validation-aware provider fallback.
- Deterministic relative-date prompt context.
- Transactional derived writes and leased reminder dispatch.
- Telegram commands: `/today`, `/waiting`, `/projects`, `/memory`.
- SQLite runtime with a PostgreSQL and pgvector migration blueprint.

## Setup

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

## Run

```bash
.venv/bin/memocore
```

Telegram commands:

| Command | Purpose |
|---|---|
| `/today` | Show due and overdue work |
| `/waiting` | Show blocked, waiting, and follow-up items |
| `/projects` | Show captured projects |
| `/memory` | Show recent active memory candidates |

## Providers

Set `MODEL_PROVIDER` to `ollama`, `openai`, `gemini`, `deepseek`, `openrouter`, or `groq`. Hosted providers require `MODEL_API_KEY`. Gemini is routed through its OpenAI-compatible endpoint.

Optional fallback:

```env
MODEL_FALLBACK_PROVIDER=openai
MODEL_FALLBACK_NAME=gpt-4.1-nano
MODEL_FALLBACK_API_KEY=your-key
MODEL_FALLBACK_STRUCTURED_OUTPUT_MODE=auto
```

Legacy `OLLAMA_BASE_URL` and `OLLAMA_MODEL` variables remain accepted during migration.

## Test

```bash
.venv/bin/pytest -q
```

Run the opt-in live extraction benchmark:

```bash
MEMOCORE_RUN_LIVE_BENCHMARK=1 .venv/bin/pytest tests/benchmark/test_extraction_benchmark.py -v
```

## Storage

SQLite is the verified local runtime. PostgreSQL with pgvector is the planned long-distance system of record for concurrent workers, remote backups, full-text search, and semantic retrieval. See [storage migrations](docs/storage/README.md).

## Documentation

- [Implementation plan](implementation_plan.md)
- [Engineering guide](agent.md)
- [Architecture](docs/architecture.md)
- [Roadmap](docs/personal-assistant-version-roadmap.md)
- [V1 specification](docs/v1-spec.md)
- [Archived V1 evaluation](docs/v1-evaluation.md)

## Security

Never commit `.env`, local databases, API keys, Telegram tokens, or generated runtime files. The included `.gitignore` excludes these by default.
