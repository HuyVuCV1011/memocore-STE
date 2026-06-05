# Agent Project Memory

## Project Overview

MemoCore là một backend personal secretary local-first. Project nhận ghi chú thô qua Telegram, lưu raw note, gọi model để trích xuất cấu trúc, rồi tạo task, reminder, project, memory candidate, follow-up, meeting và event log.

Stack chính:

- Python >=3.12.
- `python-telegram-bot` cho Telegram adapter.
- `pydantic` và `pydantic-settings` cho schema/config.
- `aiosqlite` và SQLite cho runtime đã verify.
- `httpx` cho LLM provider calls.
- Ollama local provider mặc định, thêm các OpenAI-compatible providers: OpenAI, Gemini, DeepSeek, OpenRouter, Groq.

Vai trò sản phẩm: đây là personal secretary backend, không phải chatbot tổng quát. Mục tiêu là giảm việc user phải nhớ, giải thích lại, triage và theo dõi open loops.

Lịch sử Codex local: có thể xác nhận các thread liên quan qua `~/.codex/session_index.jsonl` và logs như `Review MD plan files`, `Follow implementation plan`, `Redesign as personal secretary`, `Start system and select model`, `Add conversational secretary`, `Setup máy Windows code chính`, `Tổng hợp memory memocore`. Logs raw dạng websocket/telemetry và có nguy cơ chứa thông tin nhạy cảm, nên tài liệu này chỉ trích insight lâu dài từ repo hiện tại, không copy raw conversation.

## Current Development Workflow

Setup mới:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
```

Windows PowerShell tương đương:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -e ".[dev]"
Copy-Item .env.example .env
```

Sửa `.env` bằng token/key riêng của máy mới. Không copy `.env`, token Telegram, API key, local DB hay session từ máy cũ vào repo.

Chạy app:

```bash
.venv/bin/memocore
.venv/bin/memocore run --provider ollama
.venv/bin/memocore models
```

Windows:

```powershell
.\.venv\Scripts\memocore
.\.venv\Scripts\memocore run --provider ollama
.\.venv\Scripts\memocore models
```

Model local mặc định là `qwen3:14b`; cần Ollama đang chạy và model đã pull:

```bash
ollama pull qwen3:14b
```

Test:

```bash
.venv/bin/pytest -q
```

Windows:

```powershell
.\.venv\Scripts\pytest -q
```

Live extraction benchmark là opt-in và có thể gọi model/provider thật:

```bash
MEMOCORE_RUN_LIVE_BENCHMARK=1 .venv/bin/pytest tests/benchmark/test_extraction_benchmark.py -v
```

Repo hiện tại không có `package.json`, không có frontend build script, không có lint script riêng trong `pyproject.toml`.

### Windows PC Primary Runtime Setup

Mục tiêu vận hành mới: PC Windows là máy chạy local chính cho MemoCore. Mac chỉ là máy di động để sửa code, push/pull code, hoặc gửi lệnh remote cho PC restart service khi cần.

Agent trên PC nên làm theo thứ tự:

1. Clone/pull repo `memocore` về PC.
2. Cài Python 3.12, Git và Ollama.
3. Tạo virtualenv `.venv`.
4. Cài package bằng `.\.venv\Scripts\pip install -e ".[dev]"`.
5. Tạo `.env` local từ `.env.example`.
6. Điền secret từ nguồn an toàn trên PC, không lấy từ file docs này.
7. Pull model Ollama local, mặc định `qwen3:14b`.
8. Chạy `.\.venv\Scripts\pytest -q`.
9. Chạy `.\.venv\Scripts\memocore models` để kiểm tra provider/key/model profile.
10. Chạy service bằng `.\.venv\Scripts\memocore run --provider ollama` hoặc provider được user chọn.

Secret bắt buộc/tùy chọn cần có trên PC:

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

Không được ghi giá trị thật của các biến trên vào `docs/agent-project-memory.md`, `README.md`, commit, issue, chat transcript, hay bất kỳ file Git-tracked nào. Agent chỉ được đọc `.env` local trên PC khi user đã đặt file đó ở máy PC. Nếu cần tạo `.env`, hãy tạo từ `.env.example` và để user/secret manager điền giá trị.

Cách lưu secret khuyến nghị trên Windows:

- Dễ nhất: `.env` local trong thư mục repo trên PC, đã được `.gitignore`.
- An toàn hơn: dùng Windows Credential Manager, 1Password/Bitwarden CLI, hoặc file encrypted bằng `age`/`sops`, rồi generate `.env` local khi setup.
- Khi dùng hosted provider, ưu tiên provider-specific keys như `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`; `MODEL_API_KEY` chỉ dùng khi muốn override provider đang active.

### Mac-to-PC Remote Control Direction

Nếu Mac chỉ dùng để sửa code và điều khiển PC, không lưu PC secrets trên Mac trong repo. Agent trên Mac nên:

1. Sửa code trong repo.
2. Chạy unit tests local nếu môi trường Mac có đủ dependency.
3. Push/pull hoặc sync code sang PC bằng Git.
4. Gửi lệnh remote cho PC restart service khi user đã cấu hình SSH/Tailscale/remote shell an toàn.

Gợi ý mô hình remote an toàn:

- PC chạy SSH server chỉ trong LAN/Tailscale, không mở public nếu không cần.
- Dùng SSH key riêng cho Mac -> PC, passphrase và limited user.
- Trên PC tạo script restart rõ ràng, ví dụ `scripts/windows/restart-memocore.ps1`, để agent chỉ gọi script đó thay vì chạy command tùy tiện.
- Nếu service chạy bằng Windows Terminal/Task Scheduler/NSSM/PowerShell background job, document đúng command restart trong file riêng hoặc section này khi đã chốt cách chạy thật.

Chưa có script restart PC trong repo hiện tại. Khi user muốn, agent nên thêm script Windows riêng, ví dụ:

```powershell
# scripts/windows/restart-memocore.ps1
# Stop running MemoCore process/service, pull latest code if requested, then start again.
```

Trước khi agent Mac gửi lệnh restart PC, cần hỏi user xác nhận nếu command có thể stop service đang chạy thật.

## Important Architecture Notes

Doc nên đọc trước khi code: `agent.md`, `docs/architecture.md`, `docs/v1-spec.md`, `docs/storage/README.md`, sau đó mới vào `src/memocore/`.

Thư mục quan trọng:

- `src/memocore/app.py`: wire dependencies, tạo Telegram app, database, provider, services và reminder dispatch loop.
- `src/memocore/config.py`: `.env` config, provider selection, fallback config, legacy Ollama env compatibility.
- `src/memocore/adapters/telegram/`: Telegram handlers. Handler chỉ translate update thành request và reply; không đặt persistence/prompt logic ở đây.
- `src/memocore/adapters/llm/`: provider abstraction, Ollama provider, OpenAI-compatible provider, fallback provider.
- `src/memocore/adapters/storage/`: SQLite database, migrations, repositories.
- `src/memocore/domain/`: typed domain models và Pydantic schemas.
- `src/memocore/services/`: behavior cốt lõi: capture, extraction, conversation routing, intent classification, memory lifecycle, reminders, secretary views, event logging.
- `src/memocore/prompts/`: prompt templates cho extraction và intent classification.

Core flow:

1. Telegram message vào `message_handler`.
2. Pending clarification được xử lý trước.
3. `ConversationService` route intent bằng deterministic rules, sau đó AI classifier nếu cần.
4. Capture intent đi vào `CaptureService`.
5. Raw note được lưu trước model call.
6. `ExtractionService` build prompt, gọi provider, validate `NoteExtraction`.
7. Derived objects được persist trong transaction.
8. Event log ghi các action user-visible/lifecycle.

Important boundary:

- Raw notes tách riêng AI-derived data.
- Repositories chỉ persist state, không gọi Telegram/model.
- Services own assistant behavior.
- Adapters chỉ translate external protocols.
- User-visible actions và memory lifecycle changes phải auditable qua event logs.
- Conversation context nên bounded/operational; durable facts đi vào managed storage thay vì raw chat history.

## Coding Conventions

Theo pattern hiện có trước khi thêm abstraction mới. Nếu thêm tính năng, ưu tiên service/repository contract thay vì chèn logic vào Telegram handler.

Quy ước nên giữ:

- Async end-to-end cho I/O và tests dùng `pytest-asyncio`.
- Domain data dùng typed models/schema, không parse JSON/string tùy tiện khi đã có Pydantic.
- Model output phải validate bằng schema trước khi persist.
- Derived writes liên quan note nên đi trong `database.transaction()`.
- Tạo event log cho transitions quan trọng: note captured/processed, task/reminder/memory lifecycle, model output invalid, reminder sent/failed.
- Idempotency theo `source`, `source_chat_id`, `source_message_id`; duplicate Telegram message trả về capture cũ thay vì tạo objects mới.
- Vietnamese matching helpers thường normalize accent bằng `_normalize_text`; nếu thêm rule tiếng Việt, thêm fixture/test cho có dấu và không dấu nếu có thể.
- Prompt ownership nằm trong extraction/intent services; không để prompt logic ở provider.

Những việc nên tránh:

- Không chuyển project thành generic chatbot hoặc autonomous agent tự do.
- Không wire PostgreSQL/pgvector thành production runtime khi chưa có adapter, import command và contract tests thật.
- Không đưa graph database, rich dashboard UI, multi-user hosting hay broad tool execution vào scope nhỏ.
- Không commit `.env`, `data/`, local DB, token, key, cookie, session, generated runtime files.

## UI/UX Notes

Project hiện tại không có frontend. UX chính là Telegram text replies.

Telegram interface nên ngắn gọn, hữu ích và secretary-like:

- `/today`, `/tasks`, `/reminders`, `/waiting`, `/projects`, `/memory` phải tiếp tục là các command rõ ràng.
- Capture reply nên là confirmation có summary và counts/task/reminder/memory liên quan, không chỉ báo model/extraction technical.
- Khi reminder thiếu thời gian, ưu tiên hỏi clarification thay vì schedule sai.
- Casual/no-op reply nên nhẹ và không tạo memory/task ngoài ý muốn.

## Backend/Data Notes

SQLite là runtime đã verify. `Database.initialize()` tạo parent folder, bật foreign keys, dùng WAL, tạo schema, upgrade columns cũ và apply SQL migrations trong package data.

Bảng chính:

- `notes`: raw evidence, source ids, summary, tags, status, metadata.
- `tasks`: task candidates/open/waiting/blocked/done/cancelled, source note link.
- `reminders`: reminder lifecycle, `attempt_count`, `claimed_at`, delivery channel.
- `projects`, `people`, `meetings`, `followups`.
- `memory_items`: buckets `profile`, `project`, `interaction`; lifecycle candidate/active/rejected/superseded/deleted.
- `event_logs`: audit trail.

PostgreSQL/pgvector chỉ là blueprint trong `docs/storage/postgres/001_secretary_foundation.sql`. Không claim production support cho Postgres cho đến khi có runtime adapter và tests thật.

Provider/config notes:

- `.env.example` là template an toàn; `.env` thật không được copy vào docs/commit.
- `MODEL_PROVIDER=ollama`, `MODEL_NAME=qwen3:14b` là default.
- Hosted providers cần provider-specific keys như `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`; `MODEL_API_KEY` vẫn là generic override.
- `MODEL_FALLBACK_PROVIDER` chỉ nên dùng khi primary provider có thể fail hoặc output invalid.
- Gemini dùng OpenAI-compatible endpoint.
- `USER_TIMEZONE` quan trọng cho relative time như "tomorrow 9am"; máy mới nên set đúng timezone user.

## Known Pitfalls

- Logs/history Codex local có thể chứa telemetry và thông tin nhạy cảm; chỉ dùng để xác nhận thread/insight tổng quát, không paste raw.
- `.env`, `data/memocore.db`, `data/*.db-wal`, `data/*.db-shm`, `data/user_feedback.jsonl` là local runtime state. Không đưa vào Git và cần cân nhắc riêng khi migrate sang Windows.
- Không nhúng API key/token thô vào file memory này. File memory dùng để hướng dẫn agent, không phải secret vault.
- Ollama provider cần Ollama server chạy ở `http://127.0.0.1:11434` nếu không override.
- Nếu hosted provider thiếu key, `create_provider` sẽ raise `ValueError`.
- Small/local models có thể trả schema thay vì extraction data; `ExtractionService` đã detect case này và retry với instruction "Return extraction data, not its schema."
- Extraction invalid JSON không được crash bot; note status phải thành failed và có event `MODEL_OUTPUT_INVALID`.
- Relative dates phải tính deterministically trong Python trước khi prompt; không phụ thuộc model tự do suy diễn ngày.
- Reminder dispatch dùng lease qua `claimed_at`; multi-worker mạnh hơn sẽ cần Postgres row locking sau này.
- Conversation routing dễ nhầm query thành capture/memory. Deterministic safe routes và confidence threshold trong `ConversationService` là guard quan trọng.
- Memory correction/delete matching dựa vào heuristic token overlap; cần test kỹ với tiếng Việt có dấu/không dấu để tránh xóa/supersede nhầm.
- Trên Windows, command path `.venv/bin/...` đổi thành `.\.venv\Scripts\...`; file path trong docs/commands nên để agent tương lai nhận ra cả hai.
- Repo không có `AGENTS.md` và không có `.agents/skills` hiện tại. Nếu một AGENTS/skill folder được thêm sau này, đọc trước và update tài liệu này.

## Verification Checklist

Trước khi báo hoàn thành task:

- Chạy targeted tests gần với thay đổi, ví dụ:

```bash
.venv/bin/pytest tests/unit/test_config.py -q
.venv/bin/pytest tests/unit/test_extraction_service.py -q
.venv/bin/pytest tests/integration/test_capture_flow.py -q
```

- Nếu thay đổi shared service/repository/schema, chạy full suite:

```bash
.venv/bin/pytest -q
```

- Nếu thay đổi provider/model/prompt, thêm hoặc cập nhật fixture tests; live benchmark chỉ chạy khi user đồng ý vì có thể gọi model/API.
- Nếu thay đổi Telegram handlers/conversation routing, chạy tests liên quan `test_handlers.py`, `test_conversation_service.py`, `test_intent_pipeline.py`, `test_clarification_flow.py`.
- Nếu thay đổi storage schema/repository, chạy `tests/integration/test_repositories.py` và verify migration package data trong `pyproject.toml`.
- Nếu thay đổi reminder behavior, chạy `tests/unit/test_reminder_service.py` và cân nhắc lease/failure cases.
- Nếu thay đổi memory lifecycle, chạy `tests/unit/test_memory_service.py` và các integration capture/correction nếu có.
- Không cần browser check vì chưa có frontend.

## Agent Instructions

Khi agent mới vào project:

1. Đọc `README.md`, `agent.md`, `docs/architecture.md`, `docs/v1-spec.md`, `docs/storage/README.md`.
2. Đọc `pyproject.toml` và `.env.example` để hiểu Python version, entrypoint, dependencies và config names.
3. Đọc code theo luồng `src/memocore/app.py` -> `adapters/telegram/handlers.py` -> `services/conversation_service.py` -> `services/capture_service.py` -> `services/task_extraction_service.py` -> `adapters/storage/repositories.py`.
4. Tránh đọc/copy `.env`, `data/`, auth/session/cache files. Nếu cần biết schema DB, đọc `sqlite.py` và migrations thay vì dump DB thật.
5. Nếu cần thay đổi behavior secretary, thêm tests trước/kèm theo, đặc biệt cho Vietnamese text và edge cases query-vs-capture.
6. Hỏi user trước khi chạy live provider/API, live Telegram bot, live benchmark, migration dữ liệu thật, hoặc xóa/sửa local DB.
7. Không overwrite `AGENTS.md` nếu sau này có file này; chỉ đọc và tôn trọng nó.
8. Không commit/push nếu user không yêu cầu.
9. Khi migrate sang Windows, tạo `.env` mới từ `.env.example`; chỉ copy source/docs/tests. Local model Ollama, Telegram token và provider keys phải setup riêng trên Windows.
10. Nếu user muốn PC là runtime chính, coi PC là nơi duy nhất giữ `.env` thật và local DB thật. Mac không nên giữ bản copy secret/runtime DB trừ khi user yêu cầu rõ.
11. Nếu cần điều khiển PC từ Mac, ưu tiên gọi script restart đã định nghĩa sẵn trên PC qua SSH/Tailscale. Không tự tạo command remote phá hủy dữ liệu hoặc reset DB nếu user chưa xác nhận.
