from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from uuid import uuid4

from memocore import __version__


BACKUP_SUFFIX = ".sqlite3"
MANIFEST_SUFFIX = ".manifest.json"
_REQUIRED_RESTORE_TABLES = ("notes", "tasks", "reminders", "memory_items", "event_logs")


@dataclass(frozen=True)
class BackupResult:
    backup_id: str
    database_path: Path
    manifest_path: Path
    verified: bool


@dataclass(frozen=True)
class BackupVerification:
    ok: bool
    backup_id: str | None
    database_path: Path
    manifest_path: Path | None
    integrity: str
    table_count: int
    migrations: tuple[str, ...]
    error: str | None = None


@dataclass(frozen=True)
class RestorePlan:
    backup_id: str
    backup_path: Path
    target_path: Path
    dry_run: bool
    verified: bool
    pre_restore_backup: Path | None = None


@dataclass(frozen=True)
class RestoreDrillResult:
    backup_id: str
    backup_path: Path
    report_path: Path
    verified: bool
    table_counts: dict[str, int]
    drilled_at: str


class BackupService:
    """SQLite backup, restore dry-run, and export operations."""

    def __init__(self, database_path: Path | str, backup_dir: Path | str | None = None):
        self.database_path = Path(database_path)
        self.backup_dir = Path(backup_dir) if backup_dir else Path("backups")

    def create_backup(self, *, verify: bool = True) -> BackupResult:
        if not self.database_path.exists():
            raise FileNotFoundError(f"Database not found: {self.database_path}")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backup_id = _backup_id()
        backup_path = self.backup_dir / f"{backup_id}{BACKUP_SUFFIX}"
        manifest_path = self.backup_dir / f"{backup_id}{MANIFEST_SUFFIX}"

        source = sqlite3.connect(self.database_path)
        try:
            source.execute("PRAGMA wal_checkpoint(PASSIVE)")
            target = sqlite3.connect(backup_path)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()

        verification = self.verify_backup(backup_path)
        manifest = {
            "schema_version": 1,
            "backup_id": backup_id,
            "app_version": __version__,
            "created_at": datetime.now(UTC).isoformat(),
            "source_database_name": self.database_path.name,
            "source_sha256": _sha256(self.database_path),
            "database_file": backup_path.name,
            "size_bytes": backup_path.stat().st_size,
            "sha256": _sha256(backup_path),
            "verified": verification.ok if verify else False,
            "integrity": verification.integrity,
            "table_count": verification.table_count,
            "migrations": list(verification.migrations),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if verify and not verification.ok:
            raise RuntimeError(verification.error or "Backup verification failed")
        return BackupResult(backup_id, backup_path, manifest_path, verification.ok)

    def list_backups(self) -> list[dict]:
        if not self.backup_dir.exists():
            return []
        manifests: list[dict] = []
        for path in self.backup_dir.glob(f"*{MANIFEST_SUFFIX}"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            manifest["manifest_path"] = str(path)
            manifests.append(manifest)
        manifests.sort(
            key=lambda manifest: _parse_datetime(manifest.get("created_at")) or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        return manifests

    def prune_backups(
        self,
        *,
        keep_count: int | None = None,
        max_age_days: int | None = None,
        now: datetime | None = None,
    ) -> list[str]:
        if keep_count is not None and keep_count < 1:
            raise ValueError("keep_count must be at least 1")
        if max_age_days is not None and max_age_days < 1:
            raise ValueError("max_age_days must be at least 1")
        current = now or datetime.now(UTC)
        backups = self.list_backups()
        removable: list[dict] = []
        if keep_count is not None:
            removable.extend(backups[keep_count:])
        if max_age_days is not None:
            for backup in backups:
                created_at = _parse_datetime(backup.get("created_at"))
                if created_at and (current - created_at).days >= max_age_days:
                    removable.append(backup)

        removed: list[str] = []
        seen: set[str] = set()
        for backup in removable:
            backup_id = backup.get("backup_id")
            if not backup_id or backup_id in seen:
                continue
            seen.add(backup_id)
            if not backup.get("verified"):
                continue
            verification = self.verify_backup(backup_id)
            if not verification.ok:
                continue
            backup_path = verification.database_path
            manifest_path = verification.manifest_path or _manifest_path_for_backup(backup_path)
            if backup_path.exists() and backup_path.parent.resolve() == self.backup_dir.resolve():
                backup_path.unlink()
            if manifest_path.exists() and manifest_path.parent.resolve() == self.backup_dir.resolve():
                manifest_path.unlink()
            removed.append(backup_id)
        return removed

    def latest_restore_drill(self) -> dict | None:
        report_path = self._restore_drill_report_path()
        if not report_path.exists():
            return None
        try:
            return json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def latest_restore_report(self) -> dict | None:
        report_path = self._restore_report_path()
        if not report_path.exists():
            return None
        try:
            return json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def verify_backup(self, backup: Path | str) -> BackupVerification:
        backup_path = self._resolve_backup_path(backup)
        manifest_path = _manifest_path_for_backup(backup_path)
        backup_id = _backup_id_from_path(backup_path)
        if not backup_path.exists():
            return BackupVerification(
                False,
                backup_id,
                backup_path,
                manifest_path if manifest_path.exists() else None,
                "missing",
                0,
                (),
                "Backup file does not exist",
            )
        try:
            conn = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                table_count = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
                ).fetchone()[0]
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                migrations = tuple(
                    row[0]
                    for row in conn.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall()
                )
            finally:
                conn.close()
            missing_tables = sorted(set(_REQUIRED_RESTORE_TABLES) - tables)
            ok = integrity == "ok" and not missing_tables
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                expected_hash = manifest.get("sha256")
                if expected_hash and expected_hash != _sha256(backup_path):
                    return BackupVerification(
                        False,
                        backup_id,
                        backup_path,
                        manifest_path,
                        integrity,
                        table_count,
                        migrations,
                        "Backup checksum does not match manifest",
                    )
            return BackupVerification(
                ok,
                backup_id,
                backup_path,
                manifest_path if manifest_path.exists() else None,
                integrity,
                table_count,
                migrations,
                None
                if ok
                else (
                    f"Backup missing required tables: {', '.join(missing_tables)}"
                    if missing_tables
                    else "SQLite integrity check failed"
                ),
            )
        except (OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            return BackupVerification(
                False,
                backup_id,
                backup_path,
                manifest_path if manifest_path.exists() else None,
                "error",
                0,
                (),
                str(exc),
            )

    def restore(
        self,
        backup: Path | str,
        *,
        dry_run: bool,
        confirm: bool = False,
    ) -> RestorePlan:
        backup_path = self._resolve_backup_path(backup)
        verification = self.verify_backup(backup_path)
        if not verification.ok:
            raise RuntimeError(verification.error or "Backup verification failed")
        if dry_run:
            with tempfile.TemporaryDirectory(prefix="memocore-restore-") as tmp:
                candidate = Path(tmp) / self.database_path.name
                shutil.copy2(backup_path, candidate)
                dry_verification = self.verify_backup(candidate)
                if not dry_verification.ok:
                    raise RuntimeError(dry_verification.error or "Restore dry-run failed")
            plan = RestorePlan(
                verification.backup_id or _backup_id_from_path(backup_path) or backup_path.stem,
                backup_path,
                self.database_path,
                True,
                True,
            )
            self._write_restore_report(plan)
            return plan
        if not confirm:
            raise ValueError("Restore requires --confirm unless --dry-run is used")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        pre_restore = None
        if self.database_path.exists():
            safety = self.create_backup(verify=False)
            pre_restore = safety.database_path
        restore_candidate = self.database_path.with_name(
            f".{self.database_path.name}.restore-{uuid4().hex[:8]}.tmp"
        )
        try:
            shutil.copy2(backup_path, restore_candidate)
            candidate_verification = self.verify_backup(restore_candidate)
            if not candidate_verification.ok:
                raise RuntimeError(
                    candidate_verification.error or "Restore candidate verification failed"
                )
            restore_candidate.replace(self.database_path)
        finally:
            if restore_candidate.exists():
                restore_candidate.unlink()
        plan = RestorePlan(
            verification.backup_id or _backup_id_from_path(backup_path) or backup_path.stem,
            backup_path,
            self.database_path,
            False,
            True,
            pre_restore,
        )
        self._write_restore_report(plan)
        return plan

    def run_restore_drill(self, backup: Path | str | None = None) -> RestoreDrillResult:
        if backup is None:
            backups = self.list_backups()
            if backups:
                backup = backups[0].get("backup_id")
            else:
                backup = self.create_backup().backup_id
        if backup is None:
            raise RuntimeError("No backup available for restore drill")
        backup_path = self._resolve_backup_path(backup)
        verification = self.verify_backup(backup_path)
        if not verification.ok:
            raise RuntimeError(verification.error or "Backup verification failed")

        with tempfile.TemporaryDirectory(prefix="memocore-restore-drill-") as tmp:
            candidate = Path(tmp) / self.database_path.name
            shutil.copy2(backup_path, candidate)
            candidate_verification = self.verify_backup(candidate)
            if not candidate_verification.ok:
                raise RuntimeError(candidate_verification.error or "Restore drill verification failed")
            table_counts = _required_table_counts(candidate)

        drilled_at = datetime.now(UTC).isoformat()
        report_path = self._restore_drill_report_path()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        backup_id = verification.backup_id or _backup_id_from_path(backup_path) or backup_path.stem
        payload = {
            "schema_version": 1,
            "backup_id": backup_id,
            "backup_file": backup_path.name,
            "drilled_at": drilled_at,
            "verified": True,
            "table_counts": table_counts,
        }
        report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return RestoreDrillResult(
            backup_id=backup_id,
            backup_path=backup_path,
            report_path=report_path,
            verified=True,
            table_counts=table_counts,
            drilled_at=drilled_at,
        )

    def export_json(self, output_path: Path | str, *, redacted: bool = False) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            payload = {
                "schema_version": 1,
                "exported_at": datetime.now(UTC).isoformat(),
                "redacted": redacted,
                "tables": {
                    table: _table_rows(conn, table, redacted=redacted)
                    for table in _export_tables(conn)
                },
            }
        finally:
            conn.close()
        output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return output

    def export_markdown(self, output_path: Path | str, *, redacted: bool = False) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            tables = {table: _table_rows(conn, table, redacted=redacted) for table in _export_tables(conn)}
            lines = [
                "# MemoCore Export",
                "",
                f"- Exported at: {datetime.now(UTC).isoformat()}",
                f"- Redacted: {str(redacted).lower()}",
                "",
            ]
            sections = [
                ("Projects", "projects", _project_line),
                ("People", "people", _person_line),
                ("Tasks", "tasks", _task_line),
                ("Commitments", "commitments", _commitment_line),
                ("Decisions", "decisions", _decision_line),
                ("Memory", "memory_items", _memory_line),
                ("Reminders", "reminders", _reminder_line),
                ("Meetings", "meetings", _meeting_line),
                ("Follow-ups", "followups", _followup_line),
                ("Notes", "notes", _note_line),
            ]
            for heading, table, formatter in sections:
                lines.extend([f"## {heading}", ""])
                rows = tables.get(table, [])
                if not rows:
                    lines.extend(["No rows.", ""])
                    continue
                for row in rows:
                    lines.append(formatter(row))
                lines.append("")
            extra_tables = sorted(set(tables) - {table for _, table, _ in sections})
            if extra_tables:
                lines.extend(["## Additional tables", ""])
                for table in extra_tables:
                    lines.append(f"- {table}: {len(tables[table])} row(s)")
                lines.append("")
        finally:
            conn.close()
        output.write_text("\n".join(lines), encoding="utf-8")
        return output

    def _resolve_backup_path(self, backup: Path | str) -> Path:
        path = Path(backup)
        if path.exists() or path.suffix:
            return path
        return self.backup_dir / f"{path}{BACKUP_SUFFIX}"

    def _restore_drill_report_path(self) -> Path:
        return self.backup_dir / "latest-restore-drill.json"

    def _restore_report_path(self) -> Path:
        return self.backup_dir / "latest-restore.json"

    def _write_restore_report(self, plan: RestorePlan) -> None:
        report_path = self._restore_report_path()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "backup_id": plan.backup_id,
            "backup_file": plan.backup_path.name,
            "target_database_name": plan.target_path.name,
            "restored_at": datetime.now(UTC).isoformat(),
            "dry_run": plan.dry_run,
            "verified": plan.verified,
            "pre_restore_backup_file": (
                plan.pre_restore_backup.name if plan.pre_restore_backup is not None else None
            ),
        }
        report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _backup_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:8]}"


def _backup_id_from_path(path: Path) -> str | None:
    if path.name.endswith(BACKUP_SUFFIX):
        return path.name[: -len(BACKUP_SUFFIX)]
    return path.stem or None


def _manifest_path_for_backup(path: Path) -> Path:
    backup_id = _backup_id_from_path(path) or path.stem
    return path.with_name(f"{backup_id}{MANIFEST_SUFFIX}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _export_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [row[0] for row in rows]


def _required_table_counts(database_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing = sorted(set(_REQUIRED_RESTORE_TABLES) - tables)
        if missing:
            raise RuntimeError(f"Restore drill missing tables: {', '.join(missing)}")
        return {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in _REQUIRED_RESTORE_TABLES
        }
    finally:
        conn.close()


def _table_rows(
    conn: sqlite3.Connection,
    table: str,
    *,
    redacted: bool,
) -> list[dict]:
    rows = [dict(row) for row in conn.execute(f'SELECT * FROM "{table}"').fetchall()]
    if redacted:
        for row in rows:
            for key in ("raw_text", "source_message_id", "source_chat_id", "payload"):
                if key in row:
                    row[key] = "[redacted]"
    return rows


def _project_line(row: dict) -> str:
    bits = [str(row.get("name") or "Unnamed project")]
    if row.get("status"):
        bits.append(f"status: {row['status']}")
    if row.get("last_seen_at"):
        bits.append(f"last seen: {row['last_seen_at']}")
    return f"- {' · '.join(bits)}"


def _person_line(row: dict) -> str:
    bits = [str(row.get("display_name") or "Unnamed person")]
    if row.get("relationship"):
        bits.append(f"relationship: {row['relationship']}")
    if row.get("notes"):
        bits.append(str(row["notes"]))
    return f"- {' · '.join(bits)}"


def _task_line(row: dict) -> str:
    bits = [str(row.get("title") or "Untitled task")]
    if row.get("status"):
        bits.append(f"status: {row['status']}")
    if row.get("priority"):
        bits.append(f"priority: {row['priority']}")
    if row.get("due_at"):
        bits.append(f"due: {row['due_at']}")
    if row.get("recurrence_rule"):
        bits.append(f"recurs: {row['recurrence_rule']}")
    return f"- {' · '.join(bits)}"


def _commitment_line(row: dict) -> str:
    bits = [str(row.get("title") or "Untitled commitment")]
    if row.get("direction"):
        bits.append(f"direction: {row['direction']}")
    if row.get("status"):
        bits.append(f"status: {row['status']}")
    if row.get("due_at"):
        bits.append(f"due: {row['due_at']}")
    return f"- {' · '.join(bits)}"


def _decision_line(row: dict) -> str:
    bits = [str(row.get("title") or "Untitled decision")]
    if row.get("status"):
        bits.append(f"status: {row['status']}")
    if row.get("summary"):
        bits.append(str(row["summary"]))
    return f"- {' · '.join(bits)}"


def _memory_line(row: dict) -> str:
    bits = [str(row.get("content") or "Empty memory")]
    if row.get("bucket"):
        bits.append(f"bucket: {row['bucket']}")
    if row.get("kind"):
        bits.append(f"kind: {row['kind']}")
    if row.get("status"):
        bits.append(f"status: {row['status']}")
    if row.get("valid_from") or row.get("valid_until"):
        bits.append(f"valid: {row.get('valid_from') or '?'} to {row.get('valid_until') or '?'}")
    return f"- {' · '.join(bits)}"


def _reminder_line(row: dict) -> str:
    bits = [str(row.get("title") or "Untitled reminder")]
    if row.get("status"):
        bits.append(f"status: {row['status']}")
    if row.get("remind_at"):
        bits.append(f"at: {row['remind_at']}")
    if row.get("recurrence_rule"):
        bits.append(f"recurs: {row['recurrence_rule']}")
    return f"- {' · '.join(bits)}"


def _meeting_line(row: dict) -> str:
    bits = [str(row.get("title") or "Untitled meeting")]
    if row.get("starts_at"):
        bits.append(f"starts: {row['starts_at']}")
    if row.get("ends_at"):
        bits.append(f"ends: {row['ends_at']}")
    if row.get("notes"):
        bits.append(str(row["notes"]))
    return f"- {' · '.join(bits)}"


def _followup_line(row: dict) -> str:
    bits = [str(row.get("title") or "Untitled follow-up")]
    if row.get("status"):
        bits.append(f"status: {row['status']}")
    if row.get("due_at"):
        bits.append(f"due: {row['due_at']}")
    if row.get("notes"):
        bits.append(str(row["notes"]))
    return f"- {' · '.join(bits)}"


def _note_line(row: dict) -> str:
    bits = [str(row.get("summary") or row.get("raw_text") or "Untitled note")]
    if row.get("created_at"):
        bits.append(f"created: {row['created_at']}")
    if row.get("status"):
        bits.append(f"status: {row['status']}")
    return f"- {' · '.join(bits)}"
