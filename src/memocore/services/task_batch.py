from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, tzinfo
import json

from memocore.domain.models import Task


@dataclass(frozen=True)
class TaskBatchSnapshot:
    task_id: str
    status: str
    updated_at: datetime

    @classmethod
    def from_task(cls, task: Task) -> "TaskBatchSnapshot":
        return cls(task.id, str(task.status), task.updated_at)

    def matches(self, task: Task | None) -> bool:
        return (
            task is not None
            and str(task.status) == self.status
            and task.updated_at == self.updated_at
        )


def encode_batch_field(
    snapshots: list[TaskBatchSnapshot],
    *,
    selected_ids: list[str] | None = None,
) -> str:
    payload = {
        "snapshots": [
            {
                **asdict(snapshot),
                "updated_at": snapshot.updated_at.isoformat(),
            }
            for snapshot in snapshots
        ],
        "selected_ids": selected_ids,
    }
    return "batch_done|" + json.dumps(payload, separators=(",", ":"))


def decode_batch_field(
    field_name: str,
) -> tuple[list[TaskBatchSnapshot], list[str] | None]:
    if not field_name.startswith("batch_done|"):
        return [], None
    try:
        payload = json.loads(field_name.split("|", 1)[1])
        snapshots = [
            TaskBatchSnapshot(
                task_id=item["task_id"],
                status=item["status"],
                updated_at=datetime.fromisoformat(item["updated_at"]),
            )
            for item in payload.get("snapshots", [])
        ]
        selected_ids = payload.get("selected_ids")
    except (KeyError, TypeError, ValueError):
        return [], None
    return snapshots, selected_ids


def batch_preview_text(tasks: list[Task], display_timezone: tzinfo) -> str:
    lines = [f"Sẽ hoàn thành {len(tasks)} task:"]
    for index, task in enumerate(tasks, 1):
        due = (
            task.due_at.astimezone(display_timezone).strftime("%H:%M %d/%m/%Y")
            if task.due_at
            else "chưa có hạn"
        )
        recurring = " · định kỳ" if task.recurrence_rule else ""
        lines.append(f"{index}. {task.title} · {due}{recurring}")
    recurring_count = sum(1 for task in tasks if task.recurrence_rule)
    if recurring_count:
        lines.append(
            f"{recurring_count} task định kỳ sẽ tạo kỳ kế tiếp khi hoàn thành."
        )
    return "\n".join(lines)
