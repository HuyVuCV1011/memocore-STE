from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
from datetime import UTC, datetime, timedelta
import socket

import pytest

from memocore.adapters.storage.sqlite import Database
from memocore.services.backup_service import BackupService
from memocore.services.recovery_preflight_service import (
    RecoveryError,
    RecoveryPreflightService,
    RuntimeState,
    RestoreAuthorization,
    _issue_restore_authorization_with_probe,
    _restore_disk_requirements,
    _safety_creation_requirements,
    _swap_recovery_requirements,
    semantic_database_check,
    issue_restore_authorization,
)
from memocore.services.recovery_io import (
    RecoveryLock,
    durable_copy_with_hash,
)


def _authorization():
    return _issue_restore_authorization_with_probe(
        explicit_maintenance=True, runtime_probe=lambda: RuntimeState.OFFLINE
    )


@pytest.mark.parametrize("state", [RuntimeState.ONLINE, RuntimeState.UNKNOWN])
def test_authorization_issuer_rejects_untrusted_runtime_state(state):
    with pytest.raises(RecoveryError) as error:
        _issue_restore_authorization_with_probe(
            explicit_maintenance=True, runtime_probe=lambda: state
        )
    assert error.value.code == "maintenance_not_verified"


def test_authorization_issuer_accepts_trusted_offline_probe():
    authorization = _issue_restore_authorization_with_probe(
        explicit_maintenance=True, runtime_probe=lambda: RuntimeState.OFFLINE
    )
    assert authorization.runtime_state is RuntimeState.OFFLINE


def test_public_authorization_issuer_uses_internal_probe(monkeypatch):
    monkeypatch.setattr(
        "memocore.services.recovery_preflight_service.probe_runtime_state",
        lambda: RuntimeState.UNKNOWN,
    )
    with pytest.raises(RecoveryError):
        issue_restore_authorization(explicit_maintenance=True)
    monkeypatch.setattr(
        "memocore.services.recovery_preflight_service.probe_runtime_state",
        lambda: RuntimeState.OFFLINE,
    )
    assert (
        issue_restore_authorization(explicit_maintenance=True).runtime_state is RuntimeState.OFFLINE
    )


async def _service(tmp_path) -> BackupService:
    database_path = tmp_path / "memocore.db"
    database = Database(database_path)
    await database.initialize()
    await database.close()
    return BackupService(database_path, tmp_path / "backups")


def _refresh_manifest(backup_path: Path, **updates: object) -> None:
    manifest_path = backup_path.with_name(f"{backup_path.stem}.manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "database_file": backup_path.name,
            "size_bytes": backup_path.stat().st_size,
            "sha256": hashlib.sha256(backup_path.read_bytes()).hexdigest(),
            **updates,
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _insert_note(path: Path, note_id: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT INTO notes (
                id, source, raw_text, summary, tags, status, metadata, created_at, updated_at
            ) VALUES (?, 'manual', ?, '', '[]', 'captured', '{}',
                      '2026-07-16T00:00:00+00:00', '2026-07-16T00:00:00+00:00')
            """,
            (note_id, note_id),
        )
        conn.commit()
    finally:
        conn.close()


async def test_restore_rejects_missing_manifest(tmp_path):
    service = await _service(tmp_path)
    candidate = tmp_path / "unmanaged.sqlite3"
    candidate.write_bytes(service.database_path.read_bytes())

    with pytest.raises(RecoveryError, match="valid backup manifest") as error:
        service.restore(candidate, dry_run=True)

    assert error.value.code == "manifest_invalid"


async def test_confirmed_restore_cannot_bypass_service_authorization(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()

    with pytest.raises(RecoveryError) as error:
        service.restore(backup.backup_id, dry_run=False, confirm=True)

    assert error.value.code == "maintenance_not_verified"

    forged = RestoreAuthorization(RuntimeState.OFFLINE, True, object())
    with pytest.raises(RecoveryError) as forged_error:
        service.restore(
            backup.backup_id,
            dry_run=False,
            confirm=True,
            authorization=forged,
        )
    assert forged_error.value.code == "maintenance_not_verified"


async def test_restore_rejects_corrupt_candidate_even_with_matching_manifest(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    backup.database_path.write_bytes(b"not a sqlite database")
    _refresh_manifest(backup.database_path)

    with pytest.raises(RecoveryError) as error:
        service.restore(backup.backup_id, dry_run=True)

    assert error.value.code == "candidate_invalid"
    report = service.latest_restore_report()
    assert report["dry_run"] is True


async def test_restore_rejects_future_app_and_unknown_migration(tmp_path):
    service = await _service(tmp_path)
    future = service.create_backup()
    _refresh_manifest(future.database_path, app_version="99.0.0")

    with pytest.raises(RecoveryError) as error:
        service.restore(future.backup_id, dry_run=True)
    assert error.value.code == "future_app_version"

    unknown = service.create_backup()
    conn = sqlite3.connect(unknown.database_path)
    try:
        conn.execute("INSERT INTO schema_migrations VALUES ('999_future.sql', datetime('now'))")
        conn.commit()
    finally:
        conn.close()
    _refresh_manifest(unknown.database_path)

    with pytest.raises(RecoveryError) as error:
        service.restore(unknown.backup_id, dry_run=True)
    assert error.value.code == "compatibility_failed"


async def test_old_candidate_is_migrated_without_mutating_source(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    conn = sqlite3.connect(backup.database_path)
    try:
        conn.execute("DROP TABLE activity_links")
        conn.execute("DELETE FROM schema_migrations WHERE version = '008_activity_links.sql'")
        conn.commit()
    finally:
        conn.close()
    _refresh_manifest(backup.database_path)
    source_hash = hashlib.sha256(backup.database_path.read_bytes()).hexdigest()

    service.restore(backup.backup_id, dry_run=False, confirm=True, authorization=_authorization())

    assert hashlib.sha256(backup.database_path.read_bytes()).hexdigest() == source_hash
    conn = sqlite3.connect(service.database_path)
    try:
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = '008_activity_links.sql'"
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='activity_links'"
        ).fetchone()
    finally:
        conn.close()


async def test_preflight_rejects_foreign_key_violation(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    conn = sqlite3.connect(backup.database_path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            """
            INSERT INTO tasks (
                id, title, description, status, priority, source_note_id, confidence,
                created_at, updated_at
            ) VALUES ('orphan', 'orphan', '', 'open', 'medium', 'missing', 1,
                      datetime('now'), datetime('now'))
            """
        )
        conn.commit()
    finally:
        conn.close()
    _refresh_manifest(backup.database_path)

    with pytest.raises(RecoveryError, match="candidate_semantic failed"):
        service.restore(backup.backup_id, dry_run=True)


async def test_insufficient_disk_fails_before_safety_backup(tmp_path, monkeypatch):
    service = await _service(tmp_path)
    backup = service.create_backup()
    called = False
    original = service.create_backup

    def track_create(*, verify=True):
        nonlocal called
        called = True
        return original(verify=verify)

    monkeypatch.setattr(service, "create_backup", track_create)
    recovery = RecoveryPreflightService(service, disk_check=lambda _path, _needed: False)

    with pytest.raises(RecoveryError) as error:
        recovery.restore(
            backup.backup_id,
            dry_run=False,
            confirm=True,
            authorization=_authorization(),
        )

    assert error.value.code == "insufficient_disk"
    assert called is False


async def test_disk_boundary_is_fail_closed_and_exact_value_passes(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    requirements = _restore_disk_requirements(
        backup.database_path, service.database_path, service.backup_dir
    )
    exact = {path: needed for path, needed in requirements}
    assert sum(exact.values()) >= backup.database_path.stat().st_size

    RecoveryPreflightService(
        service, disk_check=lambda path, needed: exact[path] >= needed
    ).restore(backup.backup_id, dry_run=True, confirm=False)

    with pytest.raises(RecoveryError) as error:
        RecoveryPreflightService(
            service, disk_check=lambda path, needed: exact[path] - 1 >= needed
        ).restore(backup.backup_id, dry_run=True, confirm=False)
    assert error.value.code == "insufficient_disk"


async def test_disk_reserve_uses_two_phases_and_actual_artifact_sizes(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    live_size = service.database_path.stat().st_size
    candidate = tmp_path / "grown-candidate.sqlite3"
    candidate.write_bytes(backup.database_path.read_bytes() + b"growth")
    safety = service.backup_dir / "actual-safety.sqlite3"
    safety.write_bytes(b"s" * (live_size + 19))

    before_safety = _safety_creation_requirements(service.database_path, service.backup_dir)
    before_swap = _swap_recovery_requirements(
        candidate,
        safety,
        service.database_path,
        service.backup_dir,
        device_id=lambda _path: 7,
    )

    safety_need = live_size + max(16 * 1024 * 1024, (live_size + 3) // 4)
    assert before_safety == [(service.backup_dir, safety_need)]
    assert before_swap == [(service.backup_dir, candidate.stat().st_size + safety.stat().st_size)]


async def test_swap_reserve_separates_different_filesystems(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    candidate = tmp_path / "candidate.sqlite3"
    candidate.write_bytes(backup.database_path.read_bytes() + b"candidate-growth")
    safety = service.backup_dir / "safety.sqlite3"
    safety.write_bytes(b"safety-copy-is-not-live-size")

    requirements = _swap_recovery_requirements(
        candidate,
        safety,
        service.database_path,
        service.backup_dir,
        device_id=lambda path: 1 if path.resolve() == service.backup_dir.resolve() else 2,
    )

    assert requirements == [
        (service.backup_dir, candidate.stat().st_size),
        (service.database_path.parent, safety.stat().st_size),
    ]


async def test_fresh_target_reserves_forensic_but_no_safety_or_fallback(tmp_path):
    source_service = await _service(tmp_path / "source")
    backup = source_service.create_backup()
    fresh_target = tmp_path / "fresh" / "memocore.db"
    candidate = tmp_path / "fresh" / "candidate.sqlite3"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(backup.database_path.read_bytes())

    assert _safety_creation_requirements(fresh_target, source_service.backup_dir) == []
    assert _swap_recovery_requirements(
        candidate,
        None,
        fresh_target,
        source_service.backup_dir,
        device_id=lambda _path: 1,
    ) == [(source_service.backup_dir, candidate.stat().st_size)]


async def test_target_fallback_reserve_one_byte_short_fails_before_swap(tmp_path, monkeypatch):
    service = await _service(tmp_path)
    backup = service.create_backup()
    original = _swap_recovery_requirements

    def separate_volume_requirements(candidate, safety, live, backup_dir):
        return original(
            candidate,
            safety,
            live,
            backup_dir,
            device_id=lambda path: 1 if path.resolve() == backup_dir.resolve() else 2,
        )

    monkeypatch.setattr(
        "memocore.services.recovery_preflight_service._swap_recovery_requirements",
        separate_volume_requirements,
    )
    target_checks = 0

    def one_byte_short_for_fallback(path: Path, needed: int) -> bool:
        nonlocal target_checks
        if path.resolve() != service.database_path.parent.resolve():
            return True
        target_checks += 1
        if target_checks == 1:  # candidate creation phase
            return True
        available = needed - 1
        return available >= needed

    with pytest.raises(RecoveryError) as error:
        RecoveryPreflightService(service, disk_check=one_byte_short_for_fallback).restore(
            backup.backup_id,
            dry_run=False,
            confirm=True,
            authorization=_authorization(),
        )

    assert error.value.code == "commit_disk_insufficient"
    assert target_checks == 2
    report = json.loads((service.backup_dir / "latest-restore.json").read_text())
    assert report["phase"] == "swap_pending"


async def test_dry_run_requires_only_candidate_capacity(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    calls = 0

    def candidate_only_capacity(_path: Path, _needed: int) -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    result = RecoveryPreflightService(service, disk_check=candidate_only_capacity).restore(
        backup.backup_id, dry_run=True, confirm=False
    )

    assert result.table_counts["notes"] == 0
    assert calls == 1


async def test_safety_backup_verification_failure_prevents_swap(tmp_path, monkeypatch):
    service = await _service(tmp_path)
    backup = service.create_backup()
    _insert_note(service.database_path, "current")

    def fail_safety(*, verify=True):
        raise RuntimeError("injected safety failure")

    monkeypatch.setattr(service, "create_backup", fail_safety)

    with pytest.raises(RecoveryError) as error:
        RecoveryPreflightService(service).restore(
            backup.backup_id,
            dry_run=False,
            confirm=True,
            authorization=_authorization(),
        )

    assert error.value.code == "safety_backup_invalid"
    conn = sqlite3.connect(service.database_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM notes WHERE id='current'").fetchone()[0] == 1
    finally:
        conn.close()


async def test_postflight_failure_rolls_back_original_data(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    _insert_note(service.database_path, "current")
    checks = 0

    def fail_postflight(path: Path) -> dict[str, int]:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("injected postflight failure")
        return semantic_database_check(path)

    with pytest.raises(RecoveryError) as error:
        RecoveryPreflightService(service, semantic_check=fail_postflight).restore(
            backup.backup_id,
            dry_run=False,
            confirm=True,
            authorization=_authorization(),
        )

    assert error.value.code == "postflight_failed"
    conn = sqlite3.connect(service.database_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM notes WHERE id='current'").fetchone()[0] == 1
    finally:
        conn.close()
    report = service.latest_restore_report()
    assert report["status"] == "rolled_back"
    assert report["rollback"] == "succeeded"
    assert report["failure_code"] == "postflight_failed"
    assert report["recovery_code"] == "exact_rollback_verified"


async def test_rollback_failure_is_reported_critical(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    checks = 0
    replacements = 0

    def fail_postflight(path: Path) -> dict[str, int]:
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise RuntimeError("injected postflight failure")
        return semantic_database_check(path)

    def fail_rollback(source: Path, target: Path) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("injected rollback replace failure")
        os.replace(source, target)

    with pytest.raises(RecoveryError) as error:
        RecoveryPreflightService(
            service,
            semantic_check=fail_postflight,
            atomic_replace=fail_rollback,
        ).restore(
            backup.backup_id,
            dry_run=False,
            confirm=True,
            authorization=_authorization(),
        )

    assert error.value.code == "rollback_failed"
    assert error.value.critical is True
    report = service.latest_restore_report()
    assert report["failure_code"] == "postflight_failed"
    assert report["recovery_code"] == "rollback_failed"
    assert report["recovery_phase"] == "rollback_failed"
    assert report["rollback"] == "critical"


async def test_restore_lock_refuses_concurrent_operation(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    lock = service.database_path.with_name(f".{service.database_path.name}.restore.lock")
    held = RecoveryLock.acquire(
        lock,
        {"operation_id": "held", "schema_version": 1, "pid": os.getpid()},
    )
    assert held is not None

    try:
        with pytest.raises(RecoveryError) as error:
            service.restore(backup.backup_id, dry_run=True)
        assert lock.exists()
    finally:
        held.release()

    assert error.value.code == "restore_locked"


async def test_successful_restore_removes_stale_live_sidecars(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    wal = Path(f"{service.database_path}-wal")
    shm = Path(f"{service.database_path}-shm")
    wal.write_bytes(b"stale")
    shm.write_bytes(b"stale")

    service.restore(backup.backup_id, dry_run=False, confirm=True, authorization=_authorization())

    assert not wal.exists()
    assert not shm.exists()
    report = service.latest_restore_report()
    assert report["status"] == "passed"
    assert report["phase"] == "complete"
    assert report["operation_id"]
    assert "\\" not in report["backup_file"]


async def test_semantic_probe_rejects_missing_relation_field(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    conn = sqlite3.connect(backup.database_path)
    try:
        conn.execute("ALTER TABLE commitments DROP COLUMN direction")
        conn.commit()
    finally:
        conn.close()
    _refresh_manifest(backup.database_path)

    with pytest.raises(RecoveryError, match="candidate_semantic failed"):
        service.restore(backup.backup_id, dry_run=True)


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("UPDATE tasks SET status='mystery'", "tasks.status"),
        ("UPDATE tasks SET confidence=2", "tasks.confidence"),
        ("UPDATE tasks SET created_at='not-a-date'", "tasks.created_at"),
    ],
)
async def test_semantic_probe_rejects_invalid_domain_values(tmp_path, statement, message):
    service = await _service(tmp_path)
    _insert_note(service.database_path, "source")
    conn = sqlite3.connect(service.database_path)
    try:
        conn.execute(
            """
            INSERT INTO tasks (
                id, title, description, status, priority, source_note_id, confidence,
                created_at, updated_at
            ) VALUES ('task', 'task', '', 'open', 'medium', 'source', .5,
                      '2026-07-16T00:00:00+00:00', '2026-07-16T00:00:00+00:00')
            """
        )
        conn.execute(statement)
        conn.commit()
    finally:
        conn.close()
    backup = service.create_backup(verify=False)
    _refresh_manifest(backup.database_path, verified=True)

    with pytest.raises(RecoveryError, match="candidate_semantic failed") as error:
        service.restore(backup.backup_id, dry_run=True)
    assert message in str(error.value.__cause__)


async def test_first_swap_failure_restores_exact_main_and_sidecars(tmp_path, monkeypatch):
    service = await _service(tmp_path)
    candidate_backup = service.create_backup()
    _insert_note(service.database_path, "current")
    safety = service.create_backup()
    monkeypatch.setattr(service, "create_backup", lambda verify=True: safety)
    sidecars = [Path(f"{service.database_path}-wal"), Path(f"{service.database_path}-shm")]
    for sidecar in sidecars:
        sidecar.write_bytes(b"")
    originals = {path: path.read_bytes() for path in [service.database_path, *sidecars]}
    calls = 0

    def fail_first_swap(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected first swap failure")
        os.replace(source, target)

    with pytest.raises(RecoveryError) as error:
        RecoveryPreflightService(service, atomic_replace=fail_first_swap).restore(
            candidate_backup.backup_id,
            dry_run=False,
            confirm=True,
            authorization=_authorization(),
        )

    assert error.value.code == "swap_failed"
    assert {path: path.read_bytes() for path in originals} == originals
    report = service.latest_restore_report()
    assert report["failure_code"] == "swap_failed"
    assert report["failure_phase"] == "swap"
    assert report["recovery_phase"] == "rollback_verified"
    assert report["forensic_candidate_file"] == "latest-failed-restore-candidate.sqlite3"
    assert len(report["forensic_candidate_sha256"]) == 64
    assert (service.backup_dir / report["forensic_candidate_file"]).exists()


async def test_forensic_preservation_failure_is_reported(tmp_path, monkeypatch):
    service = await _service(tmp_path)
    backup = service.create_backup()
    monkeypatch.setattr(
        "memocore.services.recovery_preflight_service.durable_copy_with_hash",
        lambda *_args: (_ for _ in ()).throw(OSError("forensic disk failure")),
    )
    calls = 0

    def fail_swap_once(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("swap failure")
        os.replace(source, target)

    with pytest.raises(RecoveryError):
        RecoveryPreflightService(service, atomic_replace=fail_swap_once).restore(
            backup.backup_id,
            dry_run=False,
            confirm=True,
            authorization=_authorization(),
        )

    report = service.latest_restore_report()
    assert report["forensic_candidate_status"] == "failed"
    assert report["forensic_candidate_file"] is None


async def test_fresh_target_dry_run_failure_does_not_create_database(tmp_path):
    source = await _service(tmp_path / "source")
    backup = source.create_backup()
    target = tmp_path / "fresh" / "never-created.db"
    target_service = BackupService(target, source.backup_dir)
    _refresh_manifest(backup.database_path, app_version="99.0.0")

    with pytest.raises(RecoveryError):
        target_service.restore(backup.backup_id, dry_run=True)

    assert not target.exists()


async def test_fresh_nested_target_valid_dry_run_succeeds(tmp_path):
    source = await _service(tmp_path / "source")
    backup = source.create_backup()
    target = tmp_path / "fresh" / "nested" / "memocore.db"
    target_service = BackupService(target, source.backup_dir)

    plan = target_service.restore(backup.backup_id, dry_run=True)

    assert plan.verified is True
    assert target.parent.exists()
    assert not target.exists()


async def test_partial_quarantine_failure_uses_verified_safety_fallback(tmp_path, monkeypatch):
    service = await _service(tmp_path)
    backup = service.create_backup()
    safety = service.create_backup()
    monkeypatch.setattr(service, "create_backup", lambda verify=True: safety)
    Path(f"{service.database_path}-wal").write_bytes(b"")
    moves = 0

    def fail_second_quarantine(source: Path, target: Path) -> None:
        nonlocal moves
        moves += 1
        if moves == 2:
            raise OSError("injected partial quarantine failure")
        os.replace(source, target)

    def fail_exact_restore(source: Path, target: Path) -> None:
        raise OSError("injected exact restore failure")

    with pytest.raises(RecoveryError) as error:
        RecoveryPreflightService(
            service,
            quarantine_replace=fail_second_quarantine,
            atomic_replace=fail_exact_restore,
        ).restore(
            backup.backup_id,
            dry_run=False,
            confirm=True,
            authorization=_authorization(),
        )

    assert error.value.code == "rollback_exact_failed"
    assert semantic_database_check(service.database_path)["notes"] == 0
    report = service.latest_restore_report()
    assert report["status"] == "rolled_back"
    assert report["rollback"] == "safety_succeeded"
    assert report["failure_code"] == "swap_prepare_failed"
    assert report["failure_phase"] == "swap_prepare"
    assert report["recovery_code"] == "rollback_exact_failed"
    assert report["recovery_phase"] == "safety_rollback_verified"


async def test_lock_active_and_malformed_fail_closed(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    lock = service.database_path.with_name(f".{service.database_path.name}.restore.lock")
    held = RecoveryLock.acquire(
        lock,
        {
            "schema_version": 1,
            "operation_id": "active",
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "created_at": datetime.now(UTC).isoformat(),
        },
    )
    assert held is not None
    try:
        with pytest.raises(RecoveryError) as active:
            service.restore(backup.backup_id, dry_run=True)
    finally:
        held.release()
    assert active.value.code == "restore_locked"


async def test_stale_unheld_marker_can_be_replaced_by_os_lock(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    lock = service.database_path.with_name(f".{service.database_path.name}.restore.lock")
    lock.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation_id": "stale",
                "pid": 99999999,
                "host": socket.gethostname(),
                "created_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    assert service.restore(backup.backup_id, dry_run=True).verified is True


async def test_lock_replaced_mid_operation_is_not_deleted(tmp_path):
    service = await _service(tmp_path)
    backup = service.create_backup()
    lock = service.database_path.with_name(f".{service.database_path.name}.restore.lock")

    replacement_blocked = False

    def replace_lock(path: Path) -> dict[str, int]:
        nonlocal replacement_blocked
        try:
            lock.write_text(json.dumps({"operation_id": "replacement"}), encoding="utf-8")
        except PermissionError:
            replacement_blocked = True
        return semantic_database_check(path)

    RecoveryPreflightService(service, semantic_check=replace_lock).restore(
        backup.backup_id, dry_run=True, confirm=False
    )

    assert replacement_blocked or lock.exists()


def test_existing_lock_never_calls_process_kill(monkeypatch, tmp_path):
    lock = tmp_path / "restore.lock"
    held = RecoveryLock.acquire(lock, {"operation_id": "held"})
    assert held is not None
    monkeypatch.setattr(
        "memocore.services.recovery_io.os.kill",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not call os.kill")),
    )
    try:
        assert RecoveryLock.acquire(lock, {"operation_id": "new"}) is None
    finally:
        held.release()


def test_release_lock_detects_replacement_between_validation_and_unlink(tmp_path):
    lock = tmp_path / "restore.lock"
    held = RecoveryLock.acquire(lock, {"operation_id": "owner"})
    assert held is not None

    held.release(
        before_unlink=lambda: lock.write_text(
            json.dumps({"operation_id": "replacement", "padding": "changed"}),
            encoding="utf-8",
        ),
    )

    assert lock.exists()


def test_forensic_copy_flushes_and_verifies_checksum(tmp_path, monkeypatch):
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "backups" / "latest-failed-restore-candidate.sqlite3"
    source.write_bytes(b"forensic candidate bytes")
    calls = 0
    original_fsync = os.fsync

    def tracking_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        original_fsync(descriptor)

    monkeypatch.setattr("memocore.services.recovery_io.os.fsync", tracking_fsync)

    digest = durable_copy_with_hash(source, destination)

    assert calls >= 1
    assert destination.read_bytes() == source.read_bytes()
    assert digest == hashlib.sha256(source.read_bytes()).hexdigest()


async def test_phase_reports_distinguish_migration_and_semantic(tmp_path, monkeypatch):
    service = await _service(tmp_path)
    backup = service.create_backup()
    monkeypatch.setattr(
        "memocore.services.recovery_preflight_service._run_required_migrations",
        lambda _path: (_ for _ in ()).throw(RuntimeError("migration injected")),
    )
    with pytest.raises(RecoveryError):
        service.restore(backup.backup_id, dry_run=True)
    assert service.latest_restore_report()["phase"] == "migration"

    monkeypatch.undo()
    with pytest.raises(RecoveryError):
        RecoveryPreflightService(
            service, semantic_check=lambda _path: (_ for _ in ()).throw(RuntimeError("semantic"))
        ).restore(backup.backup_id, dry_run=True, confirm=False)
    assert service.latest_restore_report()["phase"] == "candidate_semantic"


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE notes SET status='unknown' WHERE id='source'",
        "UPDATE people SET aliases='not-json' WHERE id='person'",
        "UPDATE projects SET project_type='unknown' WHERE id='project'",
        "UPDATE memory_items SET bucket='unknown' WHERE id='memory'",
        "UPDATE memory_items SET kind='unknown' WHERE id='memory'",
    ],
)
async def test_semantic_validator_covers_extended_domain_fields(tmp_path, mutation):
    service = await _service(tmp_path)
    _insert_note(service.database_path, "source")
    conn = sqlite3.connect(service.database_path)
    try:
        now = "2026-07-16T00:00:00+00:00"
        conn.execute("INSERT INTO people VALUES ('person','Person','[]','','',?,?)", (now, now))
        conn.execute(
            """
            INSERT INTO projects (
                id,name,aliases,summary,status,tags,last_seen_at,created_at,updated_at,project_type
            ) VALUES ('project','Project','[]','','active','[]',?,?,?,'product')
            """,
            (now, now, now),
        )
        conn.execute(
            """
            INSERT INTO memory_items (
                id,bucket,kind,content,source_note_id,confidence,status,created_at,updated_at
            ) VALUES ('memory','profile','fact','fact','source',.5,'active',?,?)
            """,
            (now, now),
        )
        conn.execute(mutation)
        conn.commit()
    finally:
        conn.close()
    backup = service.create_backup(verify=False)
    _refresh_manifest(backup.database_path, verified=True)

    with pytest.raises(RecoveryError) as error:
        service.restore(backup.backup_id, dry_run=True)

    assert error.value.code == "candidate_semantic_failed"


async def test_resolved_memory_conflict_state_is_restoreable(tmp_path):
    service = await _service(tmp_path)
    _insert_note(service.database_path, "source")
    conn = sqlite3.connect(service.database_path)
    try:
        conn.execute(
            """
            INSERT INTO memory_items (
                id,bucket,kind,content,source_note_id,confidence,status,conflict_state,
                created_at,updated_at
            ) VALUES ('memory','profile','fact','fact','source',.5,'active','resolved',
                      '2026-07-16T00:00:00+00:00','2026-07-16T00:00:00+00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()
    backup = service.create_backup()

    assert service.restore(backup.backup_id, dry_run=True).verified is True


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE notes SET tags='{}' WHERE id='source'",
        "UPDATE notes SET metadata='[]' WHERE id='source'",
        "UPDATE event_logs SET payload='[]'",
    ],
)
async def test_semantic_validator_rejects_wrong_json_collection_shape(tmp_path, mutation):
    service = await _service(tmp_path)
    _insert_note(service.database_path, "source")
    conn = sqlite3.connect(service.database_path)
    try:
        conn.execute(
            """
            INSERT INTO event_logs VALUES (
                'event','backup_created','system','backup','{}',
                '2026-07-16T00:00:00+00:00'
            )
            """
        )
        conn.execute(mutation)
        conn.commit()
    finally:
        conn.close()
    backup = service.create_backup(verify=False)
    _refresh_manifest(backup.database_path, verified=True)

    with pytest.raises(RecoveryError) as error:
        service.restore(backup.backup_id, dry_run=True)

    assert error.value.code == "candidate_semantic_failed"


@pytest.mark.parametrize("plan_json", ["null", "[]", '"scalar"'])
async def test_conversation_plan_json_requires_object(tmp_path, plan_json):
    service = await _service(tmp_path)
    conn = sqlite3.connect(service.database_path)
    try:
        conn.execute(
            """
            INSERT INTO conversation_turns (
                id,source_chat_id,raw_text,intent,result_entity_ids,created_at,plan_json
            ) VALUES ('turn','chat','text','capture','[]',
                      '2026-07-16T00:00:00+00:00',?)
            """,
            (plan_json,),
        )
        conn.commit()
    finally:
        conn.close()
    backup = service.create_backup(verify=False)
    _refresh_manifest(backup.database_path, verified=True)

    with pytest.raises(RecoveryError) as error:
        service.restore(backup.backup_id, dry_run=True)
    assert error.value.code == "candidate_semantic_failed"


async def test_conversation_plan_json_object_restores(tmp_path):
    service = await _service(tmp_path)
    conn = sqlite3.connect(service.database_path)
    try:
        conn.execute(
            """
            INSERT INTO conversation_turns (
                id,source_chat_id,raw_text,intent,result_entity_ids,created_at,plan_json
            ) VALUES ('turn','chat','text','capture','[]',
                      '2026-07-16T00:00:00+00:00','{}')
            """
        )
        conn.commit()
    finally:
        conn.close()
    backup = service.create_backup()

    assert service.restore(backup.backup_id, dry_run=True).verified is True
