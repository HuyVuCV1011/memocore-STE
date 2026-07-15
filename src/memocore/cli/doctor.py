from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from telegram import Bot
from telegram.error import TelegramError

from memocore.adapters.llm.provider_factory import PROVIDER_DEFAULTS, create_provider
from memocore.config import Settings
from memocore.services.backup_service import BackupService
from memocore.services.review_window_service import review_window_report
from memocore.services.runtime_version_service import runtime_version_descriptor


EXPECTED_COMMANDS = (
    "today",
    "work",
    "context",
    "search",
    "review",
)


@dataclass(frozen=True)
class CheckResult:
    level: str
    name: str
    detail: str


async def run_doctor(settings: Settings, live_provider: bool = False) -> list[CheckResult]:
    results = [
        _check_python_imports(),
        _check_runtime_version(settings),
        _check_config(settings),
        _check_provider_config(settings),
        _check_database(settings.database_path),
        _check_backups(settings),
        _check_review_window(settings),
        _check_invalid_chat_ids(settings.database_path),
        _check_pm2_process(),
    ]
    results.append(await _check_telegram(settings))
    if live_provider:
        results.append(await _check_provider_live(settings))
    return results


def print_doctor_report(results: list[CheckResult]) -> None:
    print("MemoCore doctor")
    print("")
    for result in results:
        print(f"{result.level:<4} {result.name}: {result.detail}")
    print("")
    if any(result.level == "FAIL" for result in results):
        print("Result: unhealthy")
    elif any(result.level == "WARN" for result in results):
        print("Result: healthy with warnings")
    else:
        print("Result: healthy")


def has_failures(results: list[CheckResult]) -> bool:
    return any(result.level == "FAIL" for result in results)


def _check_python_imports() -> CheckResult:
    return CheckResult("OK", "Python import", f"{sys.version_info.major}.{sys.version_info.minor}")


def _check_runtime_version(settings: Settings) -> CheckResult:
    descriptor = runtime_version_descriptor(settings.database_path)
    return CheckResult("OK", "Runtime version", descriptor.format())


def _check_config(settings: Settings) -> CheckResult:
    missing = []
    if not settings.telegram_bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not settings.database_path:
        missing.append("DATABASE_PATH")
    if missing:
        return CheckResult("FAIL", "Config", f"missing {', '.join(missing)}")
    return CheckResult("OK", "Config", f"database={settings.database_path}")


def _check_provider_config(settings: Settings) -> CheckResult:
    provider = settings.model.provider
    model = settings.model.name
    if provider not in PROVIDER_DEFAULTS:
        return CheckResult("FAIL", "Provider config", f"unsupported provider {provider}")
    if provider != "ollama" and not settings.model.api_key:
        return CheckResult("WARN", "Provider config", f"{provider}/{model} key missing")
    try:
        configured = create_provider(settings.model)
    except Exception as exc:
        return CheckResult("FAIL", "Provider config", str(exc))
    return CheckResult(
        "OK",
        "Provider config",
        f"{configured.info.provider_name}/{configured.info.model_name}",
    )


async def _check_provider_live(settings: Settings) -> CheckResult:
    try:
        provider = create_provider(settings.model)
        healthy = await provider.health_check()
    except Exception as exc:
        return CheckResult("FAIL", "Provider live", str(exc))
    if not healthy:
        return CheckResult("FAIL", "Provider live", "health_check returned false")
    return CheckResult("OK", "Provider live", "health_check passed")


def _check_database(path: Path) -> CheckResult:
    if str(path) != ":memory:" and not path.exists():
        return CheckResult("FAIL", "SQLite", f"{path} does not exist")
    try:
        conn = sqlite3.connect(path)
        row = conn.execute("PRAGMA integrity_check").fetchone()
        tables = {
            item[0]
            for item in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        conn.close()
    except sqlite3.Error as exc:
        return CheckResult("FAIL", "SQLite", str(exc))
    required = {"notes", "tasks", "reminders", "memory_items", "event_logs"}
    missing = sorted(required - tables)
    if missing:
        return CheckResult("FAIL", "SQLite", f"missing tables: {', '.join(missing)}")
    if not row or row[0] != "ok":
        return CheckResult("FAIL", "SQLite", f"integrity_check={row[0] if row else 'empty'}")
    return CheckResult("OK", "SQLite", f"{path}")


def _check_backups(settings: Settings) -> CheckResult:
    service = BackupService(settings.database_path, settings.backup_dir)
    backups = service.list_backups()
    if not backups:
        return CheckResult("WARN", "Backup", "no local backup manifest found")
    latest = backups[0]
    backup_id = latest.get("backup_id")
    if not backup_id:
        return CheckResult("WARN", "Backup", "latest manifest has no backup_id")
    verification = service.verify_backup(backup_id)
    if not verification.ok:
        return CheckResult(
            "FAIL",
            "Backup",
            f"{backup_id}: {verification.error or verification.integrity}",
        )
    restore_drill = service.latest_restore_drill()
    if not restore_drill or not restore_drill.get("verified"):
        return CheckResult("WARN", "Backup", f"{backup_id} verified; restore drill missing")
    drilled_at = restore_drill.get("drilled_at", "unknown")
    drilled_backup = restore_drill.get("backup_id", "unknown")
    return CheckResult(
        "OK",
        "Backup",
        f"{backup_id} verified; restore_drill={drilled_backup} at {drilled_at}",
    )


def _check_review_window(settings: Settings) -> CheckResult:
    report = review_window_report(settings.database_path)
    level = "OK" if report.gate_passed else "WARN"
    return CheckResult(level, "Review window", report.summary())


def _check_invalid_chat_ids(path: Path) -> CheckResult:
    try:
        conn = sqlite3.connect(path)
        rows = conn.execute(
            """
            SELECT source_chat_id, COUNT(*)
            FROM notes
            WHERE source = 'telegram' AND source_chat_id IS NOT NULL
            GROUP BY source_chat_id
            """
        ).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        return CheckResult("FAIL", "Runtime data", str(exc))
    invalid = [(value, count) for value, count in rows if _chat_id_to_int(value) is None]
    if invalid:
        detail = ", ".join(f"{value} ({count})" for value, count in invalid[:5])
        return CheckResult("WARN", "Runtime data", f"invalid source_chat_id: {detail}")
    return CheckResult("OK", "Runtime data", f"{len(rows)} Telegram chat id(s)")


async def _check_telegram(settings: Settings) -> CheckResult:
    try:
        bot = Bot(settings.telegram_bot_token)
        me = await bot.get_me()
        commands = await bot.get_my_commands()
    except TelegramError as exc:
        return CheckResult("FAIL", "Telegram", str(exc))
    except Exception as exc:
        return CheckResult("FAIL", "Telegram", str(exc))
    command_names = tuple(command.command for command in commands)
    if command_names != EXPECTED_COMMANDS:
        return CheckResult(
            "WARN",
            "Telegram",
            f"@{me.username} commands={command_names}, expected={EXPECTED_COMMANDS}",
        )
    return CheckResult("OK", "Telegram", f"@{me.username}, slash menu synced")


def _check_pm2_process() -> CheckResult:
    pm2 = shutil.which("pm2") or shutil.which("pm2.cmd")
    if pm2 is None:
        return CheckResult("WARN", "PM2", "pm2 not found")
    try:
        completed = subprocess.run(
            [pm2, "jlist"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult("WARN", "PM2", "pm2 jlist timed out")
    if completed.returncode != 0:
        return CheckResult("WARN", "PM2", completed.stderr.strip() or "pm2 jlist failed")
    try:
        processes = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return CheckResult("WARN", "PM2", "could not parse pm2 jlist")
    for process in processes:
        if process.get("name") == "memocore-ste":
            status = process.get("pm2_env", {}).get("status", "unknown")
            level = "OK" if status == "online" else "WARN"
            deploy = _pm2_deploy_stamp(process.get("pm2_env", {}))
            suffix = f", {deploy}" if deploy else ""
            return CheckResult(level, "PM2", f"memocore-ste {status}{suffix}")
    return CheckResult("WARN", "PM2", "memocore-ste not registered")


def _pm2_deploy_stamp(pm2_env: dict) -> str:
    commit = pm2_env.get("MEMOCORE_DEPLOY_COMMIT")
    dirty = pm2_env.get("MEMOCORE_DEPLOY_DIRTY")
    schema = pm2_env.get("MEMOCORE_DEPLOY_SCHEMA")
    deployed_at = pm2_env.get("MEMOCORE_DEPLOYED_AT")
    if not any((commit, dirty, schema, deployed_at)):
        return ""
    return (
        f"deploy_commit={commit or 'unknown'}, deploy_dirty={dirty or 'unknown'}, "
        f"deploy_schema={schema or 'unknown'}, deployed_at={deployed_at or 'unknown'}"
    )


def _chat_id_to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
