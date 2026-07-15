from __future__ import annotations

from datetime import UTC, datetime, tzinfo

from memocore.domain.models import MemoryStatus
from memocore.services.work_state_service import WorkStateService


class ProjectHealthService:
    def __init__(self, display_timezone: tzinfo = UTC):
        self.display_timezone = display_timezone
        self.work_state_service = WorkStateService(display_timezone)

    def health_lines(
        self,
        *,
        project,
        tasks,
        commitments,
        followups,
        memories,
        now: datetime,
    ) -> list[str]:
        state = self.work_state_service.classify(list(tasks), now)
        overdue_tasks = state.overdue
        waiting_tasks = [*state.waiting, *state.blocked]
        overdue_followups = [
            item for item in followups if item.due_at is not None and item.due_at < now
        ]
        overdue_commitments = [
            item for item in commitments if item.due_at is not None and item.due_at < now
        ]
        risky_memories = [
            item
            for item in memories
            if str(getattr(item, "conflict_state", "none")) != "none"
            or str(item.status) == MemoryStatus.CANDIDATE.value
        ]
        stale_reference = max(
            [
                _as_utc(project.updated_at),
                _as_utc(project.last_seen_at),
                *(_as_utc(item.updated_at) for item in tasks),
                *(_as_utc(item.updated_at) for item in commitments),
                *(_as_utc(item.updated_at) for item in followups),
                *(_as_utc(item.updated_at) for item in memories),
            ],
            default=_as_utc(project.updated_at),
        )
        stale_days = (now - stale_reference).days
        if state.next_actions:
            next_task = state.next_actions[0]
            task = next_task.task
            next_action = (
                f"{getattr(task, 'title', 'Task')} · "
                f"{_format_due(getattr(task, 'due_at', None), self.display_timezone)} · {next_task.reason}"
            )
        else:
            next_action = "Chưa có next action."

        risks: list[str] = []
        if not tasks:
            risks.append("thiếu next action")
        if overdue_tasks:
            risks.append(f"{len(overdue_tasks)} task quá hạn")
        if waiting_tasks:
            risks.append(f"{len(waiting_tasks)} việc đang chờ/bị chặn")
        if overdue_commitments:
            risks.append(f"{len(overdue_commitments)} commitment quá hạn")
        if overdue_followups:
            risks.append(f"{len(overdue_followups)} follow-up quá hạn")
        if risky_memories:
            risks.append(f"{len(risky_memories)} memory cần rà")
        if stale_days >= 14:
            risks.append(f"{stale_days} ngày chưa có cập nhật")

        status = "cần xem lại" if risks else "ổn"
        return [
            f"- Trạng thái: {status}",
            f"- Next action: {next_action}",
            (
                "- Rủi ro: " + "; ".join(risks)
                if risks
                else "- Rủi ro: chưa thấy blocker rõ."
            ),
            (
                f"- Open loops: {len(overdue_tasks)} quá hạn · {len(waiting_tasks)} chờ/bị chặn · "
                f"{len(commitments)} commitment · {len(followups)} follow-up."
            ),
        ]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_due(value: datetime | None, display_timezone: tzinfo) -> str:
    if value is None:
        return "chưa có hạn"
    return value.astimezone(display_timezone).strftime("%H:%M %d/%m")
