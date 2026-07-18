from __future__ import annotations

from enum import StrEnum
import json
import shutil
import subprocess


class RuntimeState(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


def probe_runtime_state() -> RuntimeState:
    pm2 = shutil.which("pm2") or shutil.which("pm2.cmd")
    if pm2 is None:
        return RuntimeState.UNKNOWN
    try:
        completed = subprocess.run(
            [pm2, "jlist"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return RuntimeState.UNKNOWN
    if completed.returncode != 0:
        return RuntimeState.UNKNOWN
    try:
        processes = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return RuntimeState.UNKNOWN
    if not isinstance(processes, list) or any(not isinstance(item, dict) for item in processes):
        return RuntimeState.UNKNOWN
    matching = [item for item in processes if item.get("name") == "memocore-ste"]
    statuses = {
        item.get("pm2_env", {}).get("status")
        for item in matching
        if isinstance(item.get("pm2_env"), dict)
    }
    if "online" in statuses:
        return RuntimeState.ONLINE
    if matching and not statuses.issubset({"stopped", "errored"}):
        return RuntimeState.UNKNOWN
    return RuntimeState.OFFLINE
