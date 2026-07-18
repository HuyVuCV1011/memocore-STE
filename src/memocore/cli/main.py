from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from zoneinfo import ZoneInfo

from memocore.app import create_app, shutdown_app
from memocore.adapters.llm.provider_factory import PROVIDER_DEFAULTS
from memocore.cli.doctor import has_failures, print_doctor_report, run_doctor
from memocore.config import Settings, get_settings
from memocore.services.backup_service import BackupService
from memocore.services.recovery_preflight_service import (
    RecoveryError,
    issue_restore_authorization,
)
from memocore.services.review_window_service import review_window_report


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    settings = get_settings()
    if args.command == "models":
        _print_models(settings)
        return
    if args.command == "doctor":
        results = asyncio.run(run_doctor(settings, live_provider=args.live_provider))
        print_doctor_report(results)
        if has_failures(results):
            raise SystemExit(1)
        return
    if args.command == "backup":
        _run_backup(settings, args)
        return
    if args.command == "backups":
        _run_backups(settings, args)
        return
    if args.command == "restore":
        _run_restore(settings, args)
        return
    if args.command == "restore-drill":
        _run_restore_drill(settings, args)
        return
    if args.command == "export":
        _run_export(settings, args)
        return
    if args.command == "review-window":
        _run_review_window(settings, args)
        return
    settings = settings.with_model_override(provider=args.provider, name=args.model)
    asyncio.run(_run(settings))


async def _run(settings: Settings) -> None:
    app = await create_app(settings)
    await app.initialize()
    if app.post_init:
        await app.post_init(app)
    try:
        await app.start()
        if app.updater is None:
            raise RuntimeError("Telegram updater is not configured")
        await app.updater.start_polling()
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if app.updater and app.updater.running:
            await app.updater.stop()
        if app.running:
            await app.stop()
        await app.shutdown()
        await shutdown_app(app)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the MemoCore personal secretary.")
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="Start the Telegram secretary.")
    run_parser.add_argument("--provider", choices=sorted(PROVIDER_DEFAULTS))
    run_parser.add_argument("--model", help="Override the selected provider's default model.")
    subparsers.add_parser("models", help="List available provider profiles.")
    doctor_parser = subparsers.add_parser("doctor", help="Check runtime, DB, Telegram, and provider config.")
    doctor_parser.add_argument(
        "--live-provider",
        action="store_true",
        help="Also call the configured model provider health check.",
    )
    backup_parser = subparsers.add_parser("backup", help="Create a verified SQLite backup.")
    backup_parser.add_argument("--backup-dir", help="Override the backup directory.")
    backup_parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Create the backup without requiring post-backup verification.",
    )
    backups_parser = subparsers.add_parser("backups", help="Inspect local backups.")
    backups_subparsers = backups_parser.add_subparsers(dest="backups_command")
    backups_list = backups_subparsers.add_parser("list", help="List known backups.")
    backups_list.add_argument("--backup-dir", help="Override the backup directory.")
    backups_prune = backups_subparsers.add_parser("prune", help="Delete old verified backups.")
    backups_prune.add_argument("--backup-dir", help="Override the backup directory.")
    backups_prune.add_argument("--keep", type=int, default=14, help="Keep at least this many newest backups.")
    backups_prune.add_argument("--max-age-days", type=int, help="Also prune backups at least this old.")
    restore_parser = subparsers.add_parser("restore", help="Verify or restore a SQLite backup.")
    restore_parser.add_argument("backup", help="Backup id or path.")
    restore_parser.add_argument("--backup-dir", help="Override the backup directory.")
    restore_parser.add_argument(
        "--maintenance",
        action="store_true",
        help="Acknowledge the Telegram runtime is stopped before a confirmed restore.",
    )
    restore_mode = restore_parser.add_mutually_exclusive_group(required=True)
    restore_mode.add_argument("--dry-run", action="store_true", help="Verify restore without replacing the live database.")
    restore_mode.add_argument("--confirm", action="store_true", help="Replace the live database after verification.")
    restore_drill_parser = subparsers.add_parser(
        "restore-drill",
        help="Run a verified restore drill into a temporary database and record the result.",
    )
    restore_drill_parser.add_argument("--backup", help="Backup id or path; defaults to the latest backup.")
    restore_drill_parser.add_argument("--backup-dir", help="Override the backup directory.")
    export_parser = subparsers.add_parser("export", help="Export MemoCore data.")
    export_parser.add_argument("--format", choices=("json", "markdown"), default="json")
    export_parser.add_argument("--output", required=True, help="Output file path.")
    export_parser.add_argument(
        "--redacted",
        action="store_true",
        help="Omit raw notes, chat ids, source message ids, and event payloads.",
    )
    review_window_parser = subparsers.add_parser(
        "review-window",
        help="Report the production trust review-window gate from event logs.",
    )
    review_window_parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Required consecutive observation days for the gate.",
    )
    review_window_parser.add_argument(
        "--require-passed",
        action="store_true",
        help="Exit non-zero unless the review-window gate has passed.",
    )
    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "run"
        args.provider = None
        args.model = None
    if args.command == "backups" and args.backups_command is None:
        args.backups_command = "list"
    return args


def _print_models(settings: Settings) -> None:
    print(f"Current: {settings.model.provider} / {settings.model.name}")
    print("")
    print("Provider profiles:")
    for provider, (_, default_model, _) in PROVIDER_DEFAULTS.items():
        if provider == "ollama":
            availability = "local"
        else:
            availability = "key set" if settings.api_key_for_provider(provider) else "key missing"
        print(f"- {provider}: {default_model} ({availability})")


def _backup_service(settings: Settings, backup_dir: str | None = None) -> BackupService:
    return BackupService(settings.database_path, backup_dir=backup_dir or settings.backup_dir)


def _run_backup(settings: Settings, args: argparse.Namespace) -> None:
    result = _backup_service(settings, args.backup_dir).create_backup(
        verify=not args.no_verify
    )
    status = "verified" if result.verified else "created"
    print(f"Backup {status}: {result.backup_id}")
    print(f"Database: {result.database_path}")
    print(f"Manifest: {result.manifest_path}")


def _run_backups(settings: Settings, args: argparse.Namespace) -> None:
    service = _backup_service(settings, args.backup_dir)
    if args.backups_command == "prune":
        removed = service.prune_backups(keep_count=args.keep, max_age_days=args.max_age_days)
        if removed:
            print("Pruned backups:")
            for backup_id in removed:
                print(f"- {backup_id}")
        else:
            print("No backups pruned.")
        return
    backups = service.list_backups()
    if not backups:
        print("No backups found.")
        return
    for backup in backups:
        verified = "verified" if backup.get("verified") else "unverified"
        print(
            f"{backup.get('backup_id')} | {backup.get('created_at')} | "
            f"{verified} | {backup.get('size_bytes')} bytes"
        )


def _run_restore(settings: Settings, args: argparse.Namespace) -> None:
    authorization = None
    if args.confirm and not args.maintenance:
        raise SystemExit("Confirmed restore requires --maintenance after stopping the runtime.")
    if args.confirm:
        try:
            authorization = issue_restore_authorization(
                explicit_maintenance=args.maintenance
            )
        except RecoveryError as exc:
            raise SystemExit(str(exc)) from exc
    plan = _backup_service(settings, args.backup_dir).restore(
        args.backup,
        dry_run=args.dry_run,
        confirm=args.confirm,
        authorization=authorization,
    )
    if plan.dry_run:
        print(f"Restore dry-run passed: {plan.backup_id}")
        print(f"Target: {plan.target_path}")
        return
    print(f"Restore completed: {plan.backup_id}")
    print(f"Target: {plan.target_path}")
    if plan.pre_restore_backup is not None:
        print(f"Pre-restore safety backup: {plan.pre_restore_backup}")


def _run_restore_drill(settings: Settings, args: argparse.Namespace) -> None:
    result = _backup_service(settings, args.backup_dir).run_restore_drill(args.backup)
    print(f"Restore drill passed: {result.backup_id}")
    print(f"Backup: {result.backup_path}")
    print(f"Report: {result.report_path}")


def _run_export(settings: Settings, args: argparse.Namespace) -> None:
    service = _backup_service(settings)
    if args.format == "json":
        output = service.export_json(args.output, redacted=args.redacted)
    else:
        output = service.export_markdown(args.output, redacted=args.redacted)
    print(f"Export written: {output}")


def _run_review_window(settings: Settings, args: argparse.Namespace) -> None:
    report = review_window_report(
        settings.database_path,
        required_days=args.days,
        telegram_owner_id=settings.telegram_owner_id,
        display_timezone=ZoneInfo(settings.user_timezone),
    )
    print("MemoCore review window")
    print("")
    for line in report.lines():
        print(line)
    print("")
    if report.gate_passed:
        print("Result: passed")
    elif report.status == "failed":
        print("Result: failed")
        raise SystemExit(1)
    else:
        print("Result: collecting")
        if args.require_passed:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
