# Telegram Command Model

## Goal

Keep Telegram easy to scan without removing specialist workflows. The visible slash menu contains
only the five primary entry points; inline buttons provide discovery; exact commands remain as
power-user shortcuts.

## Visible Slash Menu

`post_init` synchronizes this exact menu with Telegram through `setMyCommands`:

| Command | Entry point |
| --- | --- |
| `/today` | Factual daily agenda with due work, reminders, meetings, and a short highlight list. |
| `/work` | Work/open-loop hub for tasks, reminders, waiting items, and commitments. |
| `/context` | People, projects, meeting prep, and linked memory. |
| `/search` | Timeline/source search. |
| `/review` | Uncertainty, corrections, aliases, pending clarification, and system warnings. |

Do not add every supported command to this menu. A long slash menu makes the main workflows harder
to discover.

## Inline Hubs

- `/work`: today, tasks, reminders, waiting, and commitments.
- `/context`: people, projects, meeting prep, and memory.
- Hidden `/capture`: task, memory, and LinkedIn/content capture patterns.
- Hidden `/memory`: review queue, stale queue, self, goals, people, projects, and topic slices.
- `/review`: decision-first review inbox, with work hygiene and quality signals below the primary count.

Navigation callbacks edit the existing message and acknowledge the callback before longer work.
Callback payloads must remain short, stable, and safe across process restarts.

## Hidden Shortcuts

Supported shortcuts include `/task`, `/t`, `/mem`, `/m`, `/li`, `/linkedin`, `/tasks`,
`/reminders`, `/waiting`, `/projects`, `/people`, `/person <name>`, `/project <name>`,
`/context <name>`, `/prep <name>`, `/briefing`, `/memory`, `/capture`, `/weekly`, `/endday`,
`/goals`, `/people review`, `/projects review`, `/memory review`, and `/memory stale`.

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

The doctor compares Telegram's live command menu with the expected five commands. The Windows
restart script also compiles the package and runs this preflight before reloading `memocore-ste`.
