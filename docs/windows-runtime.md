# Windows Runtime Guide

This Windows PC is the primary runtime for MemoCore. Treat it as the only machine that should hold
the live `.env`, local SQLite database, Telegram bot token, and long-running Telegram polling
process.

## Runtime Rule

Run the Telegram bot as exactly one long-lived process:

```powershell
pm2 start ecosystem.config.cjs --only memocore-ste
```

Do not start a second bot manually while PM2 is online:

```powershell
.\.venv\Scripts\memocore run --provider groq
python -m memocore.cli.main run --provider groq
```

Telegram long polling only permits one active `getUpdates` consumer for a bot token. If two
MemoCore runtimes use the same token, Telegram logs this error:

```text
Conflict: terminated by other getUpdates request; make sure that only one bot instance is running
```

## Daily Commands

```powershell
pm2 list
.\scripts\windows\restart-memocore.ps1
.\scripts\windows\logs-memocore.ps1
.\.venv\Scripts\memocore models
```

## Preflight Check

Before starting or restarting MemoCore, check for duplicate runtimes:

```powershell
pm2 list
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'memocore.cli.main|memocore-STE|memocore.exe' } |
  Select-Object ProcessId,ParentProcessId,Name,CommandLine
```

The PM2-managed process may show a parent and child Python process. That is expected. What should
not exist is an extra manual `memocore run` or `python -m memocore.cli.main run` process outside
PM2.

## Safe Restart

Use the project script:

```powershell
.\scripts\windows\restart-memocore.ps1
```

The script verifies PM2 and the virtualenv entrypoint, starts or restarts `memocore-ste`, and saves
the PM2 process list.

## Mac Remote Use

Using a Mac to remote into this Windows PC is fine. The remote session itself does not cause polling
conflicts. Conflicts happen only when another MemoCore runtime, on Windows, Mac, or another machine,
uses the same Telegram bot token at the same time.

When working from Mac:

- edit code and push or pull through Git;
- do not keep a separate live `.env` and bot process on Mac;
- restart the Windows service through the Windows runtime script or a controlled remote command;
- never copy the Windows `.env`, `data/`, bot token, provider keys, cookies, or sessions into Git.

## Secrets And Local State

Keep these local and untracked:

- `.env`
- `data/`
- SQLite database files and WAL/SHM files
- API keys and Telegram tokens
- cookies, browser sessions, and generated runtime logs

