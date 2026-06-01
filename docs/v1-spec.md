# V1 Specification

## Goal

V1 proves the first usable assistant loop: capture information quickly, turn it into structured objects, persist those objects locally, and create reminders or tasks when appropriate.

## Must Include

- Telegram capture flow using a bot token.
- Python backend with clean domain services.
- Local inference through Ollama.
- Provider-agnostic model adapter.
- Structured persistence for notes, tasks, reminders, projects, memory items, and event logs.
- Lightweight memory layer with three buckets:
  - `profile`
  - `project`
  - `interaction`
- Schema-driven model output validation.
- SQLite local database.
- Basic reminder dispatch for scheduled reminders.
- Tests for schemas, services, repositories, and extraction fixtures.

## Golden Path

1. User sends a rough note to the Telegram bot.
2. Bot passes the message to `capture_service`.
3. Raw note is stored unchanged.
4. Event log records `note_captured`.
5. Extraction service asks the model for structured output.
6. Validated output creates summary, tags, task candidates, reminder candidates, project hints, and memory candidates.
7. Event log records created derived objects.
8. Bot replies with a short summary and any proposed tasks/reminders.

## Acceptance Criteria

- A new Telegram message creates a `note` row.
- Extraction output is validated by Pydantic before persistence.
- At least one fixture test covers a note with a task and reminder.
- At least one fixture test covers a note with profile memory.
- Invalid model JSON does not crash the bot.
- Reminder candidates are stored separately from scheduled reminders.
- All generated objects link back to the source note.
- `ollama` model name and base URL are configurable.
- Telegram handlers contain no persistence or prompt logic.

## Out of Scope

- Full graph memory.
- Multi-agent orchestration.
- Rich web UI.
- Email, browser, file, or voice ingestion.
- Autonomous execution.
- Complex planning views.
- Multi-user hosting.
- Heavy task queue infrastructure.
- Long-term memory ranking beyond simple buckets and source links.
