# Telegram Command Model

## Goal

Keep Telegram easy to scan without removing specialist workflows. The visible slash menu contains
only the seven primary entry points; inline buttons provide discovery; exact commands remain as
power-user shortcuts.

## Visible Slash Menu

`post_init` synchronizes this exact menu with Telegram through `setMyCommands`:

| Command | Entry point |
| --- | --- |
| `/today` | Ranked priorities, due work, reminders, and meetings. |
| `/work` | Work/open-loop hub. |
| `/memory` | Memory overview, review, stale, and topic slices. |
| `/context` | People, projects, meeting prep, and linked memory. |
| `/review` | Uncertain memory, aliases, pending clarification, and quality signals. |
| `/briefing` | Current daily briefing. |
| `/capture` | Task, memory, and content capture guidance. |

Do not add every supported command to this menu. A long slash menu makes the main workflows harder
to discover.

## Inline Hubs

- `/work`: today, tasks, reminders, waiting, and commitments.
- `/context`: people, projects, meeting prep, and memory.
- `/capture`: task, memory, and LinkedIn/content capture patterns.
- `/memory`: review queue, stale queue, self, goals, people, projects, and topic slices.
- `/review`: uncertain memory, aliases, pending clarification, undated tasks, and quality signals.

Navigation callbacks edit the existing message and acknowledge the callback before longer work.
Callback payloads must remain short, stable, and safe across process restarts.

## Hidden Shortcuts

Supported shortcuts include `/task`, `/t`, `/mem`, `/m`, `/li`, `/linkedin`, `/tasks`,
`/reminders`, `/waiting`, `/projects`, `/people`, `/person <name>`, `/project <name>`,
`/context <name>`, `/prep <name>`, `/weekly`, `/endday`, `/goals`, `/people review`,
`/projects review`, `/memory review`, and `/memory stale`.

`/start` and `/help` explain the main entry points and expose the most useful shortcuts without
registering them all in Telegram's visible menu.

## Capture Overrides

Natural language remains the default capture path. Exact commands and trailing hashtags can force
a type when needed:

- Task: `/task`, `/t`, `#task`, `#t`
- Reminder: `#remind`, `#r`
- Memory: `/mem`, `/m`, `#mem`, `#m`
- LinkedIn/content: `/li`, `/linkedin`, `#li`, `#linkedin`

## Runtime Verification

Run this before restarting the bot:

```powershell
.\.venv\Scripts\memocore doctor
```

The doctor compares Telegram's live command menu with the expected six commands. The Windows
restart script also compiles the package and runs this preflight before reloading `memocore-ste`.
