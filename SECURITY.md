# Security Policy

## Supported Version

Security fixes are applied to the current `main` branch. Older snapshots and forks are not
maintained by this repository.

## Reporting A Vulnerability

Please use GitHub's private vulnerability reporting or security advisory flow for this repository.
Do not open a public issue containing credentials, tokens, private user context, database contents,
or a reproducible exploit.

Include:

- the affected component and revision;
- reproduction steps with secrets and personal data removed;
- the expected and observed impact;
- any suggested mitigation.

## Sensitive Local Data

MemoCore is a local-first personal assistant and may process private conversations, profiles,
tasks, and credentials. Never commit:

- `.env` files or provider/Telegram tokens;
- SQLite databases, backups, logs, or JSONL event exports;
- Telegram exports, browser sessions, cookies, or OAuth credentials;
- files under `personal_profile_review/` or other private profile directories.

The repository's `.gitignore` excludes these paths. Before publishing changes, run a secret scan
over tracked and untracked-in-scope files and verify that `.env` is not tracked.

## Operational Safety

- Run only one Telegram polling instance per bot token.
- Keep write actions confirmation-gated when their target is ambiguous or dynamically resolved.
- Preserve audit events for durable mutations and validate snapshots before batch confirmation or
  undo.
- Rotate any credential immediately if it is exposed, even if the commit is later removed.
