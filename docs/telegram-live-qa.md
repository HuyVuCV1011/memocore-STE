# Telegram Live QA

Use this harness when MemoCore needs proof from the real Telegram surface, not only offline unit or
transcript tests. It sends messages from a Telegram user account to the bot, captures the actual bot
replies, records inline button labels, and writes Markdown/JSON reports for review.

## Safety Model

MemoCore is a single-owner Telegram bot. A secondary Telegram account cannot use the production bot
unless that account is the configured `TELEGRAM_OWNER_ID`.

Prefer one of these setups:

1. Run a sandbox bot/runtime with `TELEGRAM_OWNER_ID` set to the secondary account and a sandbox
   `DATABASE_PATH`.
2. Use the production bot only for read-only cases and only when the secondary account is the
   configured owner.

Do not run a second MemoCore process with the same production bot token while PM2 is online. Telegram
long polling allows only one active consumer per bot token.

## One-Time Telegram Client Setup

Create Telegram API credentials for the secondary account at <https://my.telegram.org/apps>.

Store them outside Git, for example in a local PowerShell session:

```powershell
$env:TELEGRAM_LIVE_QA_API_ID="123456"
$env:TELEGRAM_LIVE_QA_API_HASH="your-api-hash"
$env:TELEGRAM_LIVE_QA_PHONE="+84000000000"
$env:TELEGRAM_LIVE_QA_BOT_USERNAME="@memocore_ste_bot"
```

Install the optional runner dependency:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[live-qa]"
```

The first real run will ask for the Telegram login code. The local Telethon session is written under
`.telegram-live-qa/`, which is ignored by Git.

## Validate A Case Without Telegram

```powershell
.\.venv\Scripts\python.exe -m scripts.live_telegram.run_live_qa qa\live_telegram\cases\read_only_smoke.json --dry-run
```

## Run A Live Case

```powershell
.\.venv\Scripts\python.exe -m scripts.live_telegram.run_live_qa qa\live_telegram\cases\read_only_smoke.json
```

Reports are written to `telegram_live_qa_reports/`, also ignored by Git. Use the Markdown report to
judge wording, density, duplication across `/today`, `/work`, and `/briefing`, and whether internal
metadata leaked.

## Case Format

```json
{
  "name": "read_only_smoke",
  "bot_username": "@memocore_ste_bot",
  "timeout_seconds": 30,
  "quiet_seconds": 2,
  "messages": ["/today", "/work", "/briefing"],
  "expect": {
    "min_bot_messages": 3,
    "max_bot_messages": 9,
    "must_include": ["Hôm nay"],
    "must_not_include": ["task_id", "memory_id", "confidence", "Traceback"]
  }
}
```

Start with read-only commands. Mutation cases such as completing tasks, undo, closeout, or recurrence
backfill should run only against a sandbox database or after a verified backup.
