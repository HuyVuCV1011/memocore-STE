from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
from typing import TYPE_CHECKING
from uuid import uuid4

from memocore import __version__
from memocore.adapters.storage.sqlite import Database
from memocore.services.recovery_capacity import (
    has_free_space as _has_free_space,
    restore_disk_requirements as _restore_disk_requirements,
    safety_creation_requirements as _safety_creation_requirements,
    swap_recovery_requirements as _swap_recovery_requirements,
)
from memocore.services.recovery_io import RecoveryLock, atomic_write_json, durable_copy_with_hash
from memocore.services.recovery_runtime import RuntimeState, probe_runtime_state
from memocore.services import recovery_semantic_validator as semantic_validator

if TYPE_CHECKING:
    from memocore.services.backup_service import BackupService


DiskCheck = Callable[[Path, int], bool]
SemanticCheck = Callable[[Path], dict[str, int]]
AtomicReplace = Callable[[Path, Path], None]
_AUTHORIZATION_PROOF = object()


@dataclass(frozen=True)
class RestoreAuthorization:
    runtime_state: RuntimeState
    explicit_maintenance: bool
    _proof: object


RuntimeProbe = Callable[[], RuntimeState]


def issue_restore_authorization(*, explicit_maintenance: bool) -> RestoreAuthorization:
    return _issue_restore_authorization_with_probe(
        explicit_maintenance=explicit_maintenance,
        runtime_probe=probe_runtime_state,
    )


def _issue_restore_authorization_with_probe(
    *, explicit_maintenance: bool, runtime_probe: RuntimeProbe
) -> RestoreAuthorization:
    runtime_state = runtime_probe()
    if not explicit_maintenance or runtime_state is not RuntimeState.OFFLINE:
        raise RecoveryError(
            "maintenance_not_verified",
            "Confirmed restore requires explicit maintenance authorization and verified offline runtime",
        )
    return RestoreAuthorization(runtime_state, explicit_maintenance, _AUTHORIZATION_PROOF)


class RecoveryError(RuntimeError):
    def __init__(self, code: str, message: str, *, critical: bool = False):
        super().__init__(message)
        self.code = code
        self.critical = critical


@dataclass(frozen=True)
class RecoveryOutcome:
    backup_id: str
    backup_path: Path
    target_path: Path
    safety_backup: Path | None
    table_counts: dict[str, int]


@dataclass(frozen=True)
class LiveSnapshot:
    files: dict[Path, Path]
    hashes: dict[Path, str]


class QuarantineError(RuntimeError):
    def __init__(self, snapshot: LiveSnapshot, cause: Exception):
        super().__init__("Live database quarantine failed")
        self.snapshot = snapshot
        self.__cause__ = cause


class RecoveryPreflightService:
    """Fail-closed SQLite restore orchestration outside the CLI boundary."""

    def __init__(
        self,
        backup_service: BackupService,
        *,
        disk_check: DiskCheck | None = None,
        semantic_check: SemanticCheck | None = None,
        atomic_replace: AtomicReplace | None = None,
        quarantine_replace: AtomicReplace | None = None,
    ):
        self.backup_service = backup_service
        self.database_path = backup_service.database_path
        self.backup_dir = backup_service.backup_dir
        self.disk_check = disk_check or _has_free_space
        self.semantic_check = semantic_check or semantic_database_check
        self.atomic_replace = atomic_replace or _atomic_replace
        self.quarantine_replace = quarantine_replace or _atomic_replace
        self._operation_id = ""
        self._backup_file = ""
        self._safety_file: str | None = None
        self._forensic_file: str | None = None
        self._forensic_sha256: str | None = None
        self._forensic_status = "not_requested"
        self._dry_run = False

    def restore(
        self,
        backup: Path | str,
        *,
        dry_run: bool,
        confirm: bool,
        authorization: RestoreAuthorization | None = None,
    ) -> RecoveryOutcome:
        if not dry_run and not confirm:
            raise ValueError("Restore requires --confirm unless --dry-run is used")
        if confirm and (
            authorization is None
            or authorization._proof is not _AUTHORIZATION_PROOF
            or not authorization.explicit_maintenance
            or authorization.runtime_state is not RuntimeState.OFFLINE
        ):
            raise RecoveryError(
                "maintenance_not_verified",
                "Confirmed restore requires service-verified offline maintenance authorization",
            )
        self._operation_id = uuid4().hex
        self._dry_run = dry_run
        backup_path = self.backup_service._resolve_backup_path(backup)
        self._backup_file = backup_path.name
        try:
            self._validate_manifest(backup_path)
        except RecoveryError as exc:
            self._fail(exc.code, backup_path.stem, rollback="not_needed")
            raise
        verification = self.backup_service.verify_backup(backup_path)
        backup_id = verification.backup_id or backup_path.stem
        if not verification.ok:
            self._fail("candidate_invalid", backup_id, rollback="not_needed")
            raise RecoveryError(
                "candidate_invalid", verification.error or "Backup verification failed"
            )

        disk_requirements = _restore_disk_requirements(
            backup_path, self.database_path, self.backup_dir
        )
        self._require_disk(disk_requirements, backup_id, None)

        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.database_path.with_name(f".{self.database_path.name}.restore.lock")
        recovery_lock = self._acquire_lock(lock_path, backup_id)
        safety_path: Path | None = None
        candidate = self.database_path.with_name(
            f".{self.database_path.name}.restore-{uuid4().hex[:8]}.tmp"
        )
        try:
            try:
                shutil.copy2(backup_path, candidate)
            except Exception as exc:
                self._stage_failure("candidate_copy", backup_id, exc)
            try:
                self._assert_compatible(candidate)
            except Exception as exc:
                self._stage_failure("compatibility", backup_id, exc)
            try:
                _run_required_migrations(candidate)
                _finalize_candidate(candidate)
            except Exception as exc:
                self._stage_failure("migration", backup_id, exc)
            try:
                table_counts = self.semantic_check(candidate)
            except Exception as exc:
                self._stage_failure("candidate_semantic", backup_id, exc)
            if dry_run:
                self._success(backup_id, dry_run=True, rollback="not_needed")
                return RecoveryOutcome(
                    backup_id, backup_path, self.database_path, None, table_counts
                )

            safety_requirements = _safety_creation_requirements(self.database_path, self.backup_dir)
            self._require_disk(safety_requirements, backup_id, "safety")

            if self.database_path.exists():
                try:
                    safety = self.backup_service.create_backup(verify=True)
                except Exception as exc:
                    self._fail(
                        "safety_backup_invalid", backup_id, rollback="not_needed", phase="safety"
                    )
                    raise RecoveryError(
                        "safety_backup_invalid", "Pre-restore safety backup could not be verified"
                    ) from exc
                safety_verification = self.backup_service.verify_backup(safety.database_path)
                if not safety.verified or not safety_verification.ok:
                    self._fail(
                        "safety_backup_invalid", backup_id, rollback="not_needed", phase="safety"
                    )
                    raise RecoveryError(
                        "safety_backup_invalid", "Pre-restore safety backup could not be verified"
                    )
                try:
                    semantic_database_check(safety.database_path)
                except Exception as exc:
                    self._fail(
                        "safety_backup_invalid", backup_id, rollback="not_needed", phase="safety"
                    )
                    raise RecoveryError(
                        "safety_backup_invalid", "Pre-restore safety backup was not usable"
                    ) from exc
                safety_path = safety.database_path
                self._safety_file = safety_path.name

            swap_requirements = _swap_recovery_requirements(
                candidate, safety_path, self.database_path, self.backup_dir
            )
            self._require_disk(swap_requirements, backup_id, "swap_pending")

            self._write_report(
                {
                    "status": "incomplete",
                    "phase": "swap_pending",
                    "backup_id": backup_id,
                    "dry_run": False,
                    "rollback": "not_started",
                }
            )
            try:
                snapshot = self._quarantine_live_files()
            except QuarantineError as quarantine_error:
                self._rollback(
                    quarantine_error.snapshot,
                    safety_path,
                    backup_id,
                    quarantine_error,
                    failure_code="swap_prepare_failed",
                    swapped=False,
                )
                raise RecoveryError(
                    "swap_prepare_failed", "Live snapshot preparation failed and was restored"
                ) from quarantine_error
            try:
                self.atomic_replace(candidate, self.database_path)
                table_counts = self.semantic_check(self.database_path)
            except Exception as swap_or_postflight_error:
                failure_code = "swap_failed" if candidate.exists() else "postflight_failed"
                self._preserve_failed_candidate(
                    candidate if candidate.exists() else self.database_path
                )
                self._rollback(
                    snapshot,
                    safety_path,
                    backup_id,
                    swap_or_postflight_error,
                    failure_code=failure_code,
                    swapped=not candidate.exists(),
                )
                raise RecoveryError(
                    failure_code,
                    "Restore swap/postflight failed; the exact live snapshot was restored",
                ) from swap_or_postflight_error
            self._cleanup_snapshot(snapshot)
            self._success(backup_id, dry_run=False, rollback="not_needed")
            return RecoveryOutcome(
                backup_id, backup_path, self.database_path, safety_path, table_counts
            )
        except RecoveryError:
            raise
        except Exception as exc:
            self._fail("preflight_failed", backup_id, rollback="not_needed")
            raise RecoveryError("preflight_failed", f"Restore preflight failed: {exc}") from exc
        finally:
            candidate.unlink(missing_ok=True)
            recovery_lock.release()

    def drill(self, backup: Path | str) -> tuple[str, Path, dict[str, int]]:
        outcome = self.restore(backup, dry_run=True, confirm=False)
        return outcome.backup_id, outcome.backup_path, outcome.table_counts

    def _require_disk(
        self, requirements: list[tuple[Path, int]], backup_id: str, phase: str | None
    ) -> None:
        if all(self.disk_check(path, size) for path, size in requirements):
            return
        code = "commit_disk_insufficient" if phase else "insufficient_disk"
        self._fail(code, backup_id, rollback="not_needed", phase=phase)
        raise RecoveryError(code, f"Insufficient disk space during {phase or 'candidate'} phase")

    def _assert_compatible(self, candidate: Path) -> None:
        packaged = _packaged_migrations()
        conn = sqlite3.connect(f"file:{candidate}?mode=ro", uri=True)
        try:
            applied = {
                row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
        finally:
            conn.close()
        future = sorted(applied - packaged)
        if future:
            raise RecoveryError(
                "incompatible_schema", "Backup was created by a newer incompatible schema"
            )

    def _stage_failure(self, phase: str, backup_id: str, cause: Exception) -> None:
        code = f"{phase}_failed"
        self._fail(code, backup_id, rollback="not_needed", phase=phase)
        raise RecoveryError(code, f"Restore {phase} failed: {cause}") from cause

    def _validate_manifest(self, backup_path: Path) -> None:
        manifest_path = backup_path.with_name(f"{backup_path.stem}.manifest.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RecoveryError(
                "manifest_invalid", "Restore requires a valid backup manifest"
            ) from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != 1
            or manifest.get("database_file") != backup_path.name
            or manifest.get("verified") is not True
            or manifest.get("size_bytes") != backup_path.stat().st_size
            or manifest.get("sha256") != _sha256(backup_path)
        ):
            raise RecoveryError("manifest_invalid", "Backup manifest does not match the candidate")
        app_version = manifest.get("app_version")
        if not isinstance(app_version, str) or _version_tuple(app_version) is None:
            raise RecoveryError("manifest_invalid", "Backup manifest app version is invalid")
        candidate_version = _version_tuple(app_version)
        current_version = _version_tuple(__version__)
        if candidate_version is None or current_version is None:
            raise RecoveryError("manifest_invalid", "Backup manifest app version is invalid")
        if candidate_version > current_version:
            raise RecoveryError("future_app_version", "Backup requires a newer MemoCore version")

    def _rollback(
        self,
        snapshot: LiveSnapshot,
        safety_path: Path | None,
        backup_id: str,
        cause: Exception,
        *,
        failure_code: str,
        swapped: bool,
    ) -> None:
        try:
            if swapped:
                _remove_sqlite_sidecars(self.database_path)
                self.database_path.unlink(missing_ok=True)
            for original, quarantined in snapshot.files.items():
                if quarantined.exists():
                    original.unlink(missing_ok=True)
                    self.atomic_replace(quarantined, original)
            if any(
                not original.exists() or _sha256(original) != expected
                for original, expected in snapshot.hashes.items()
            ):
                raise RuntimeError("Exact rollback hash verification failed")
        except Exception as exact_error:
            try:
                self._rollback_from_safety(safety_path)
            except Exception as fallback_error:
                self._fail(
                    failure_code,
                    backup_id,
                    rollback="critical",
                    recovery_code="rollback_failed",
                    recovery_phase="rollback_failed",
                )
                raise RecoveryError(
                    "rollback_failed",
                    "CRITICAL: exact and safety-backup rollback both failed",
                    critical=True,
                ) from fallback_error
            self._fail(
                failure_code,
                backup_id,
                rollback="safety_succeeded",
                recovery_code="rollback_exact_failed",
                recovery_phase="safety_rollback_verified",
            )
            raise RecoveryError(
                "rollback_exact_failed",
                "Exact rollback failed; verified logical safety backup restored",
                critical=True,
            ) from exact_error
        self._fail(
            failure_code,
            backup_id,
            rollback="succeeded",
            recovery_code="exact_rollback_verified",
            recovery_phase="rollback_verified",
        )

    def _rollback_from_safety(self, safety_path: Path | None) -> None:
        if safety_path is None:
            raise RuntimeError("No verified safety backup exists")
        verification = self.backup_service.verify_backup(safety_path)
        if not verification.ok:
            raise RuntimeError("Safety backup verification failed before rollback")
        candidate = self.database_path.with_name(
            f".{self.database_path.name}.fallback-{self._operation_id}.tmp"
        )
        try:
            shutil.copy2(safety_path, candidate)
            _finalize_candidate(candidate)
            self.semantic_check(candidate)
            _remove_sqlite_sidecars(self.database_path)
            self.database_path.unlink(missing_ok=True)
            os.replace(candidate, self.database_path)
            self.semantic_check(self.database_path)
        finally:
            candidate.unlink(missing_ok=True)

    def _quarantine_live_files(self) -> LiveSnapshot:
        snapshot: dict[Path, Path] = {}
        hashes: dict[Path, str] = {}
        try:
            for original in _sqlite_files(self.database_path):
                if not original.exists():
                    continue
                quarantined = original.with_name(
                    f".{original.name}.recovery-{self._operation_id}.original"
                )
                hashes[original] = _sha256(original)
                self.quarantine_replace(original, quarantined)
                snapshot[original] = quarantined
        except OSError as exc:
            raise QuarantineError(LiveSnapshot(snapshot, hashes), exc) from exc
        return LiveSnapshot(snapshot, hashes)

    def _cleanup_snapshot(self, snapshot: LiveSnapshot) -> None:
        for quarantined in snapshot.files.values():
            quarantined.unlink(missing_ok=True)

    def _preserve_failed_candidate(self, source: Path) -> None:
        if not source.exists():
            return
        forensic = self.backup_dir / "latest-failed-restore-candidate.sqlite3"
        try:
            forensic_hash = durable_copy_with_hash(source, forensic)
            self._forensic_file = forensic.name
            self._forensic_sha256 = forensic_hash
            self._forensic_status = "verified"
        except OSError:
            self._forensic_status = "failed"

    def _acquire_lock(self, lock_path: Path, backup_id: str) -> RecoveryLock:
        metadata = {
            "schema_version": 1,
            "operation_id": self._operation_id,
            "pid": os.getpid(),
            "host": __import__("socket").gethostname(),
            "created_at": datetime.now(UTC).isoformat(),
        }
        recovery_lock = RecoveryLock.acquire(lock_path, metadata)
        if recovery_lock is None:
            self._fail("restore_locked", backup_id, rollback="not_needed")
            raise RecoveryError(
                "restore_locked",
                f"Restore lock exists at {lock_path.name}; verify the owning process before removal",
            )
        return recovery_lock

    def _success(self, backup_id: str, *, dry_run: bool, rollback: str) -> None:
        self._write_report(
            {
                "status": "passed",
                "phase": "dry_run_complete" if dry_run else "complete",
                "backup_id": backup_id,
                "dry_run": dry_run,
                "rollback": rollback,
            }
        )

    def _fail(
        self,
        code: str,
        backup_id: str,
        *,
        rollback: str,
        phase: str | None = None,
        recovery_code: str | None = None,
        recovery_phase: str | None = None,
    ) -> None:
        status = (
            "rolled_back"
            if rollback in {"succeeded", "safety_succeeded"}
            else ("rollback_failed" if rollback == "critical" else "failed")
        )
        self._write_report(
            {
                "status": status,
                "phase": phase or ("rollback_verified" if status == "rolled_back" else code),
                "failure_phase": phase or code.removesuffix("_failed"),
                "recovery_code": recovery_code,
                "recovery_phase": recovery_phase or "not_completed",
                "failure_code": code,
                "backup_id": backup_id,
                "dry_run": self._dry_run,
                "rollback": rollback,
            }
        )
        if rollback not in {"succeeded", "safety_succeeded", "critical"}:
            self._record_failure_event(code, rollback)

    def _write_report(self, payload: dict[str, object]) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "schema_version": 2,
            "operation_id": self._operation_id,
            "recorded_at": datetime.now(UTC).isoformat(),
            "backup_file": self._backup_file,
            "target_database_name": self.database_path.name,
            "safety_backup_file": self._safety_file,
            "forensic_candidate_file": self._forensic_file,
            "forensic_candidate_sha256": self._forensic_sha256,
            "forensic_candidate_status": self._forensic_status,
            **payload,
        }
        report_path = self.backup_dir / "latest-restore.json"
        atomic_write_json(report_path, report)

    def _record_failure_event(self, failure_code: str, rollback: str) -> None:
        if not self.database_path.exists():
            return
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(f"file:{self.database_path}?mode=rw", uri=True)
            conn.execute(
                """
                INSERT INTO event_logs (id, event_type, entity_type, entity_id, payload, created_at)
                VALUES (?, 'backup_failed', 'system', 'recovery', ?, ?)
                """,
                (
                    str(uuid4()),
                    json.dumps(
                        {
                            "schema_version": 1,
                            "operation": "restore",
                            "failure_code": failure_code,
                            "rollback": rollback,
                        }
                    ),
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()
        except sqlite3.Error:
            return
        finally:
            if conn is not None:
                conn.close()


def semantic_database_check(database_path: Path) -> dict[str, int]:
    return semantic_validator.semantic_database_check(
        database_path, packaged_migrations=_packaged_migrations()
    )


def _run_required_migrations(database_path: Path) -> None:
    error: list[BaseException] = []

    def run() -> None:
        async def initialize() -> None:
            database = Database(database_path)
            try:
                await database.initialize()
            finally:
                await database.close()

        try:
            asyncio.run(initialize())
        except BaseException as exc:  # pragma: no cover - forwarded to the caller
            error.append(exc)

    worker = threading.Thread(target=run, name="memocore-restore-migrations")
    worker.start()
    worker.join()
    if error:
        raise RuntimeError("Candidate migration failed") from error[0]


def _atomic_replace(source: Path, target: Path) -> None:
    os.replace(source, target)


def _finalize_candidate(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.commit()
    finally:
        conn.close()
    _remove_sqlite_sidecars(path)


def _remove_sqlite_sidecars(path: Path) -> None:
    Path(f"{path}-wal").unlink(missing_ok=True)
    Path(f"{path}-shm").unlink(missing_ok=True)
    Path(f"{path}-journal").unlink(missing_ok=True)


def _sqlite_files(path: Path) -> tuple[Path, ...]:
    return (path, Path(f"{path}-wal"), Path(f"{path}-shm"), Path(f"{path}-journal"))


def _version_tuple(value: str) -> tuple[int, ...] | None:
    parts = value.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _packaged_migrations() -> set[str]:
    return {
        path.name
        for path in files("memocore.adapters.storage").joinpath("migrations/sqlite").iterdir()
        if path.name.endswith(".sql")
    }


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
