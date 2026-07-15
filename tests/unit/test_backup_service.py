from __future__ import annotations

import json
import sqlite3

from memocore.adapters.storage.sqlite import Database
from memocore.services.backup_service import BackupService


async def test_backup_create_verify_and_restore_dry_run(tmp_path):
    db_path = tmp_path / "memocore.db"
    database = Database(db_path)
    await database.initialize()
    conn = await database.connection()
    await conn.execute(
        """
        INSERT INTO notes (
            id, source, source_message_id, source_chat_id, raw_text, summary,
            tags, status, metadata, created_at, updated_at
        )
        VALUES (
            'note-1', 'telegram', 'message-1', 'chat-1', 'secret note',
            '', '[]', 'captured', '{}', '2026-07-15T00:00:00+00:00',
            '2026-07-15T00:00:00+00:00'
        )
        """
    )
    await conn.commit()
    await database.close()

    service = BackupService(db_path, backup_dir=tmp_path / "backups")

    backup = service.create_backup()
    verification = service.verify_backup(backup.database_path)
    dry_run = service.restore(backup.backup_id, dry_run=True)

    assert backup.verified is True
    assert verification.ok is True
    assert "001_clarification_requests.sql" in verification.migrations
    assert dry_run.verified is True
    assert dry_run.dry_run is True
    restore_report = service.latest_restore_report()
    assert restore_report is not None
    assert restore_report["backup_id"] == backup.backup_id
    assert restore_report["dry_run"] is True
    assert backup.manifest_path.exists()
    manifest = json.loads(backup.manifest_path.read_text(encoding="utf-8"))
    assert manifest["app_version"] == "0.4.1"
    assert manifest["source_sha256"]


async def test_restore_drill_records_verified_report(tmp_path):
    db_path = tmp_path / "memocore.db"
    database = Database(db_path)
    await database.initialize()
    await database.close()
    service = BackupService(db_path, backup_dir=tmp_path / "backups")
    backup = service.create_backup()

    drill = service.run_restore_drill(backup.backup_id)
    latest = service.latest_restore_drill()

    assert drill.verified is True
    assert drill.report_path.exists()
    assert drill.table_counts["notes"] == 0
    assert latest is not None
    assert latest["backup_id"] == backup.backup_id
    assert latest["verified"] is True


async def test_restore_confirm_creates_pre_restore_backup(tmp_path):
    db_path = tmp_path / "memocore.db"
    database = Database(db_path)
    await database.initialize()
    await database.close()
    service = BackupService(db_path, backup_dir=tmp_path / "backups")
    backup = service.create_backup()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE notes")
        conn.commit()
    finally:
        conn.close()

    plan = service.restore(backup.backup_id, dry_run=False, confirm=True)
    verification = service.verify_backup(db_path)

    assert plan.verified is True
    assert plan.pre_restore_backup is not None
    assert plan.pre_restore_backup.exists()
    assert verification.ok is True


async def test_prune_backups_deletes_only_verified_old_backups(tmp_path):
    db_path = tmp_path / "memocore.db"
    database = Database(db_path)
    await database.initialize()
    await database.close()
    service = BackupService(db_path, backup_dir=tmp_path / "backups")
    first = service.create_backup()
    second = service.create_backup()
    first_manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
    first_manifest["created_at"] = "2026-07-01T00:00:00+00:00"
    first_manifest["verified"] = False
    first.manifest_path.write_text(json.dumps(first_manifest), encoding="utf-8")
    second_manifest = json.loads(second.manifest_path.read_text(encoding="utf-8"))
    second_manifest["created_at"] = "2026-07-02T00:00:00+00:00"
    second.manifest_path.write_text(json.dumps(second_manifest), encoding="utf-8")

    removed = service.prune_backups(keep_count=1)

    assert removed == []
    assert first.database_path.exists()
    assert second.database_path.exists()

    first_manifest["verified"] = True
    first.manifest_path.write_text(json.dumps(first_manifest), encoding="utf-8")
    removed = service.prune_backups(keep_count=1)

    assert removed == [first.backup_id]
    assert not first.database_path.exists()
    assert second.database_path.exists()


async def test_export_json_redacts_private_source_fields(tmp_path):
    db_path = tmp_path / "memocore.db"
    database = Database(db_path)
    await database.initialize()
    conn = await database.connection()
    await conn.execute(
        """
        INSERT INTO notes (
            id, source, source_message_id, source_chat_id, raw_text, summary,
            tags, status, metadata, created_at, updated_at
        )
        VALUES (
            'note-1', 'telegram', 'message-1', 'chat-1', 'secret note',
            '', '[]', 'captured', '{}', '2026-07-15T00:00:00+00:00',
            '2026-07-15T00:00:00+00:00'
        )
        """
    )
    await conn.commit()
    await database.close()

    output = tmp_path / "exports" / "memocore.json"
    BackupService(db_path).export_json(output, redacted=True)

    payload = json.loads(output.read_text(encoding="utf-8"))
    note = payload["tables"]["notes"][0]
    assert note["raw_text"] == "[redacted]"
    assert note["source_message_id"] == "[redacted]"
    assert note["source_chat_id"] == "[redacted]"


async def test_export_markdown_groups_recovery_sections(tmp_path):
    db_path = tmp_path / "memocore.db"
    database = Database(db_path)
    await database.initialize()
    conn = await database.connection()
    await conn.executescript(
        """
        INSERT INTO notes (
            id, source, source_message_id, source_chat_id, raw_text, summary,
            tags, status, metadata, created_at, updated_at
        )
        VALUES (
            'note-1', 'telegram', 'message-1', 'chat-1', 'secret note',
            'Captured source', '[]', 'captured', '{}', '2026-07-15T00:00:00+00:00',
            '2026-07-15T00:00:00+00:00'
        );
        INSERT INTO projects (
            id, name, summary, status, tags, last_seen_at, created_at, updated_at
        )
        VALUES (
            'project-1', 'MemoCore', '', 'active', '[]', '2026-07-15T00:00:00+00:00',
            '2026-07-15T00:00:00+00:00', '2026-07-15T00:00:00+00:00'
        );
        INSERT INTO tasks (
            id, title, description, status, priority, due_at, project_id, source_note_id,
            confidence, created_at, updated_at
        )
        VALUES (
            'task-1', 'Ship export', '', 'open', 'high', '2026-07-16T00:00:00+00:00',
            'project-1', 'note-1', 0.9, '2026-07-15T00:00:00+00:00',
            '2026-07-15T00:00:00+00:00'
        );
        INSERT INTO memory_items (
            id, bucket, kind, content, source_note_id, project_id, confidence, status,
            created_at, updated_at
        )
        VALUES (
            'memory-1', 'project', 'project_state', 'Export must be recoverable.',
            'note-1', 'project-1', 0.9, 'active', '2026-07-15T00:00:00+00:00',
            '2026-07-15T00:00:00+00:00'
        );
        """
    )
    await conn.commit()
    await database.close()

    output = tmp_path / "exports" / "memocore.md"
    BackupService(db_path).export_markdown(output, redacted=True)
    text = output.read_text(encoding="utf-8")

    assert "## Projects" in text
    assert "- MemoCore · status: active" in text
    assert "## Tasks" in text
    assert "- Ship export · status: open · priority: high" in text
    assert "## Memory" in text
    assert "Export must be recoverable." in text
    assert "secret note" not in text
    assert "message-1" not in text
    assert "chat-1" not in text


def test_backup_cli_parse_commands():
    from memocore.cli.main import _parse_args

    backup = _parse_args(["backup", "--backup-dir", "safe"])
    prune = _parse_args(["backups", "prune", "--keep", "7"])
    restore = _parse_args(["restore", "backup-id", "--dry-run"])
    drill = _parse_args(["restore-drill", "--backup", "backup-id"])
    export = _parse_args(["export", "--format", "markdown", "--output", "out.md"])

    assert backup.command == "backup"
    assert backup.backup_dir == "safe"
    assert prune.command == "backups"
    assert prune.backups_command == "prune"
    assert prune.keep == 7
    assert restore.command == "restore"
    assert restore.dry_run is True
    assert drill.command == "restore-drill"
    assert drill.backup == "backup-id"
    assert export.command == "export"
    assert export.format == "markdown"
