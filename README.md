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
| Conversation pipeline | Separate Router, Planner, Context Resolver, Executor, and Composer boundaries with transcript regression evaluation. |
| Stability corpus | 41 isolated multi-turn conversations assert intent, entity focus, durable writes, schedule semantics, correction, and rollback integrity. |
| Knowledge quality | Source-linked entity relations, decision supersession, conflict detection, canonical memory selection, and review evidence. |
| Work and context state | Tasks, reminders, projects, people, meetings, follow-ups, commitments, memory candidates, and event logs. |
| Knowledge model | Canonical projects, people, organizations, decisions, and source-linked memory claims. |
| Secretary queries | Compact `/` menu for `/today`, `/work`, `/context`, `/search`, and `/review`; power-user shortcuts remain available. |
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
| `/today` | Show the day's factual agenda, due work, reminders, meetings, and a short highlight list. |
| `/work` | Open a work hub with tasks, reminders, waiting items, commitments, and action buttons. |
| `/context` | Open people, projects, meeting prep, and memory navigation. |
| `/search <query>` | Search cross-domain timeline/source evidence without exposing backend ids. |
| `/review` | Open uncertain memory, aliases, clarification, feedback, and system triage. |

Power-user shortcuts still work but are kept out of the visible command menu:
`/briefing`, `/memory`, `/capture`, `/task`, `/t`, `/mem`, `/m`, `/li`, `/linkedin`, `/tasks`, `/reminders`, `/waiting`,
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
| Interval recurrence | Daily, weekly, and interval rules like "every 2 days" or "every 3 weeks" are supported for scheduled work. |
| Reminder ergonomics | Natural snooze phrases such as "nhắc lại chiều mai" and "nhắc lại uống thuốc 2 tiếng sau" update existing reminders. |
| Nudge bundling | Quiet hours, cooldown, pre-deadline warnings, per-run limits, and multi-item digest bundling are supported. |
| Feedback signals | Accepted, edited, rejected, ignored, and correction signals are structured, artifact-linked, and reviewable. |

V4 adds linked operational context without importing private data automatically:

| V4 capability | Current state |
| --- | --- |
| People and aliases | Extracted from explicit named-person evidence, stored in SQLite, and retrievable through `/people` and `/person <name>`. |
| Operational ingestion | Meetings, follow-ups, and directional commitments are extracted and linked when confidence and entity references are safe. |
| Commitments | Tracks `user_owes`, `owed_to_user`, and `mutual` commitments by person/project without guessing an unclear direction. |
| Meeting preparation | `/prep <person or project>` summarizes linked commitments, tasks, follow-ups, meetings, and memory. |
| Linked retrieval | Tasks, meetings, follow-ups, commitments, and memory can be retrieved by person or project id. |
| Personal context import | Still review-gated; large private context files should produce Markdown/import plans before any database write. |

Current V4 deepening also includes a five-command Telegram menu, inline navigation hubs, shared work
classification for `/today`, `/work`, and `/briefing`, evidence metadata in context/prep views, review-gated entity matching, paginated memory
triage, a resolvable feedback inbox, cross-domain search/timeline answers, reminder snooze,
pre-deadline nudge digests, end-of-day and weekly rituals, lightweight goals, and a runtime
`doctor` preflight.

Tool orchestration, calendar, email, and multi-agent workers remain intentionally held until the
[conversation stability gates](docs/conversation-stability-gates.md) pass in real Telegram usage.

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
TELEGRAM_OWNER_ID=123456789
DATABASE_PATH=data/memocore.db
USER_TIMEZONE=Asia/Ho_Chi_Minh
MORNING_BRIEFING_TIME=08:00
REMINDER_DEFAULT_TIME=09:00
FOLLOWUP_NUDGE_WINDOW_START=
FOLLOWUP_NUDGE_WINDOW_END=
FOCUS_WINDOW_START=
FOCUS_WINDOW_END=
QUIET_HOURS_START=22:00
QUIET_HOURS_END=07:00
MODEL_PROVIDER=ollama
MODEL_NAME=qwen3:14b
```

Preferred windows are explicit configuration only: MemoCore will not silently infer permanent
briefing, reminder, focus, or follow-up windows from your behavior. `FOLLOWUP_NUDGE_WINDOW_*`
limits stale follow-up nudges, while urgent task deadline warnings can still be sent outside that
window unless quiet hours block them.

Install the default local model when using Ollama:

```bash
ollama pull qwen3:14b
```

## Commands

| Command | Purpose |
| --- | --- |
| `.venv/bin/memocore models` | List configured provider profiles on Linux/macOS. |
| `.venv/bin/memocore doctor` | Check config, SQLite, Telegram slash menu, PM2, and runtime data. |
| `.venv/bin/memocore review-window --days 14` | Report the production trust review-window gate from event logs; add `--require-passed` for release gating. |
| `.venv/bin/memocore backup` | Create a verified SQLite backup. |
| `.venv/bin/memocore restore-drill` | Verify the latest backup by restoring it into a temporary database and recording the drill report. |
| `.venv/bin/memocore export --format json --output exports/memocore.json --redacted` | Export recovery data without raw note text or Telegram identifiers. |
| `python scripts/quality/v4_readiness_gate.py --strict --require-clean` | Final local V4 release gate after the review window reaches 14/14 days. |
| `.\.venv\Scripts\memocore models` | List configured provider profiles on Windows. |
| `.\.venv\Scripts\memocore doctor` | Check config, SQLite, Telegram slash menu, PM2, and runtime data on Windows. |
| `.\.venv\Scripts\memocore review-window --days 14` | Report the production trust review-window gate on Windows; add `--require-passed` for release gating. |
| `.\.venv\Scripts\memocore backup` | Create a verified SQLite backup on Windows. |
| `.\.venv\Scripts\memocore restore-drill` | Run a restore drill on Windows. |
| `.\.venv\Scripts\python.exe scripts\quality\v4_readiness_gate.py --strict --require-clean` | Final Windows V4 release-readiness gate. |
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

The restart script blocks dirty working-tree deployments by default and stamps PM2 with the deployed
commit, dirty flag, schema version, and timestamp. Use `-AllowDirty` only for an explicit
development deployment.

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
| CI quality gate | Python 3.12 compile, ruff source lint, targeted mypy, module-size guard, Markdown link check, clean and previous-release migration smoke, release metadata checks, coverage-gated offline tests, pip-audit, and tracked-file secret scan on Windows and Linux |
| Windows service | PM2 process `memocore-ste` |

### Task references and recurrence

After `/briefing`, `/today`, `/tasks`, or a task-choice list, MemoCore stores the ordered task IDs
for that Telegram chat. Natural actions can then refer to `task 2`, `việc 2`, `cái thứ 2`, or
`số 2` for deadline, completion, cancellation, priority, and recurrence changes.

Tasks support `daily` and `weekly` recurrence as task data, independently from recurring reminders.
Completing a recurring occurrence marks that occurrence done and creates exactly one next occurrence
while preserving title, priority, project, and person links.

`TaskReferenceResolver` resolves numbered references, recent visible lists, time scopes, and title
matches through one mutation boundary. List context expires after six hours. Dynamic scopes such as
“all tasks today”, vague batches, and batches larger than five tasks show a preview before writing.
The preview stores a status/version snapshot, supports selecting tasks again, and revalidates every
target before completion.

If a recurring task's next occurrence is already overdue, MemoCore does not silently mark missed
work done. It asks whether to keep each missed occurrence or move the active occurrence to the
first future slot. Batch completion creates one audit event and offers a guarded undo; tasks changed
after the batch are skipped rather than overwritten.

Resolution metrics record source, mode, candidate count, context age, and confirmation state without
storing the raw message or task title.

## Security

Local secrets, databases, logs, backups, exports, browser sessions, and private profile-review
artifacts are ignored. Never commit `.env` or runtime data. Report vulnerabilities through GitHub's
private security advisory flow; see [SECURITY.md](SECURITY.md).

### Vietnamese assistant voice

The voice source of truth is
[`src/memocore/prompts/assistant_voice_vi.md`](src/memocore/prompts/assistant_voice_vi.md).
MemoCore uses `em`–`anh`, favors the Southern Vietnamese particles `dạ` and `nha` where they serve
a conversational purpose, and avoids `nhé`. Keep particles sparse: warmth should not make action
confirmations vague or verbose.

This setup follows the linguistic role of Vietnamese final/modal particles and politeness markers,
rather than treating Southern voice as a word-substitution gimmick. Background references:

- [SVFF: nhé / nha](https://svff.online/grammar-essentials/nh%C3%A9-nha)
- [Patterns of polite expressions in Vietnamese](https://sealang.net/sala/archives/pdf8/sophana2008srichampa.pdf)
- [Intonational phrase marking in Southern Vietnamese](https://www.isca-archive.org/tal_2016/brunelle16_tal.pdf)

### Stateful conversation references

MemoCore persists a short-lived canonical focus per Telegram chat in `chat_contexts` and records
the user text, assistant outcome, semantic plan, and affected artifacts in `conversation_turns`.
Each request receives a bounded `ConversationFrame` containing recent turns, pending clarification,
visible task references, and the previous result ids. Follow-ups such as `dự án đó`, `task này`,
`người đó`, or `hai task vừa tạo` therefore resolve to entity IDs before retrieval or mutation.

Task mutations also pass through `TaskOperationService`, which is the shared boundary for completion,
recurrence scheduling, cancellation, due dates, priority, and recurrence changes. New conversation
regressions should be added as multi-turn transcript tests, not only isolated sentence tests.

`ConversationPlanner` handles entity-scoped multi-turn plans before the legacy keyword router. For
example, after asking about MemoCore, “cập nhật thêm thông tin cho dự án này như sau” persists each
listed statement as a separate, source-linked project memory instead of opening the project list.
The same source note acts as a rollback boundary for commands such as “xóa 3 thông tin vừa cập
nhật”; explicit references like “dự án này” take precedence over incidental name-token matches.

Routing, planning, context resolution, execution dispatch, and response composition now have
separate service boundaries. Fixture-driven transcript evaluations live under `tests/evaluation/`
and preserve production misunderstandings as permanent regressions.

Future and recurring schedule semantics are normalized in `ScheduleSemantics`. Time ranges persist
as task duration, recurring occurrences retain that duration, and a single outing is not expanded
into several unrelated tasks and durable memories.

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
tests/evaluation/                # multi-turn transcript fixtures and evaluation runner
tests/                           # unit, integration, benchmark, and fixture tests
docs/                            # architecture, roadmap, runtime, storage, and harness notes
scripts/windows/                 # PM2 restart and log helpers for the Windows runtime
```

## Documentation

| Document | Purpose |
| --- | --- |
| [Engineering Guide](agent.md) | Code ownership boundaries, runtime rules, and engineering conventions. |
| [Architecture](docs/architecture.md) | Layering, capture flow, conversation flow, model boundary, and storage direction. |
| [Conversation Stability Gates](docs/conversation-stability-gates.md) | Required evidence before calendar, email, tools, or multi-agent work. |
| [V4 Trustworthy Daily Secretary Plan](docs/v4-trustworthy-daily-secretary-plan.md) | Complete V4 execution plan for review, recovery, closeout, search, and quality gates. |
| [Windows Runtime Guide](docs/windows-runtime.md) | Single-instance PM2 runtime policy for the primary Windows machine. |
| [Telegram Command Model](docs/telegram-command-model.md) | Visible command menu, hidden shortcuts, inline hubs, and verification rules. |
| [Implementation Plan](implementation_plan.md) | Delivered foundations, active V4 work, and held future versions. |
| [Roadmap](docs/personal-assistant-version-roadmap.md) | Product version roadmap from V1 through controlled autonomy. |
| [Agent Harness Direction](docs/agent-harness.md) | Future audited tool-use and approval boundary. |
| [0.5.0 Readiness](docs/version-0.5-readiness.md) | Verified capability audit, release scope, risks, and acceptance gates. |
| [Changelog](CHANGELOG.md) | User-visible behavior changes before release tags are cut. |
| [Storage Migrations](docs/storage/README.md) | SQLite runtime and PostgreSQL/pgvector blueprint status. |
| [V2 Manual Tests](docs/v2-manual-test-cases.md) | Telegram messages for conversational secretary verification. |
| [Content Integration Bridge](docs/content_integration_bridge.md) | Read-only SQLite contract for the LinkedIn content engine. |
| [Telegram Memory UX](docs/telegram-memory-ux-spec.md) | Dashboard, slice, and review model for high-volume Telegram memory browsing. |

## Data Safety

MemoCore is a single-owner bot. `TELEGRAM_OWNER_ID` is required, only that user's private chat is
accepted, and all reminders, briefings, reviews, and nudges are sent only to that fixed ID.

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
