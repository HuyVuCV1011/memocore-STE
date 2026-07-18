from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import shutil

DeviceId = Callable[[Path], int]


def restore_disk_requirements(
    backup_path: Path, live_path: Path, backup_dir: Path
) -> list[tuple[Path, int]]:
    del backup_dir
    source_size = backup_path.stat().st_size
    candidate_need = source_size + max(16 * 1024 * 1024, (source_size + 3) // 4)
    return [(_existing_parent(live_path.parent), candidate_need)]


def safety_creation_requirements(live_path: Path, backup_dir: Path) -> list[tuple[Path, int]]:
    if not live_path.exists():
        return []
    live_size = live_path.stat().st_size
    safety_need = live_size + max(16 * 1024 * 1024, (live_size + 3) // 4)
    return [(_existing_parent(backup_dir), safety_need)]


def swap_recovery_requirements(
    candidate_path: Path,
    safety_path: Path | None,
    live_path: Path,
    backup_dir: Path,
    *,
    device_id: DeviceId | None = None,
) -> list[tuple[Path, int]]:
    identify = device_id or filesystem_device
    backup_volume = _existing_parent(backup_dir)
    target_volume = _existing_parent(live_path.parent)
    candidate_size = candidate_path.stat().st_size
    requirements: dict[int, tuple[Path, int]] = {
        identify(backup_volume): (backup_volume, candidate_size)
    }
    if safety_path is not None:
        device = identify(target_volume)
        path, reserved = requirements.get(device, (target_volume, 0))
        requirements[device] = (path, reserved + safety_path.stat().st_size)
    return list(requirements.values())


def has_free_space(path: Path, required_bytes: int) -> bool:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free >= required_bytes


def filesystem_device(path: Path) -> int:
    return _existing_parent(path).stat().st_dev


def _existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current
