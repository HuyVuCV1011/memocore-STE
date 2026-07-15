from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo


@dataclass(frozen=True)
class RankedTask:
    task: object
    tier: str
    reason: str


@dataclass(frozen=True)
class WorkState:
    now: datetime
    local_today: date
    day_start: datetime
    day_end: datetime
    open_tasks: list[object]
    overdue: list[object]
    due_today: list[object]
    routine_today: list[object]
    waiting: list[object]
    blocked: list[object]
    unscheduled: list[object]
    upcoming: list[object]
    next_actions: list[RankedTask]

    @property
    def actionable_today(self) -> list[object]:
        return _unique([*self.overdue, *self.due_today])

    @property
    def open_loop_count(self) -> int:
        return len(self.open_tasks)


class WorkStateService:
    """Build one shared work model for Telegram-facing work views."""

    def __init__(self, display_timezone: tzinfo = UTC):
        self.display_timezone = display_timezone

    def classify(self, tasks: list[object], now: datetime | None = None) -> WorkState:
        now = now or datetime.now(UTC)
        local_today = now.astimezone(self.display_timezone).date()
        day_start = datetime.combine(
            local_today, time.min, tzinfo=self.display_timezone
        ).astimezone(UTC)
        day_end = datetime.combine(
            local_today, time.max, tzinfo=self.display_timezone
        ).astimezone(UTC)
        open_tasks = [task for task in tasks if _status(task) in {"candidate", "open", "waiting", "blocked"}]
        waiting = [task for task in open_tasks if _status(task) == "waiting"]
        blocked = [task for task in open_tasks if _status(task) == "blocked"]
        actionable = [task for task in open_tasks if _status(task) not in {"waiting", "blocked"}]
        overdue = sorted(
            [task for task in actionable if _due_at(task) is not None and _due_at(task) < now],
            key=_task_sort_key,
        )
        due_today = sorted(
            [
                task
                for task in actionable
                if _due_at(task) is not None and now <= _due_at(task) <= day_end
            ],
            key=_task_sort_key,
        )
        routine_today = [
            task for task in [*overdue, *due_today] if _is_routine(task)
        ]
        unscheduled = sorted(
            [task for task in actionable if _due_at(task) is None],
            key=_task_sort_key,
        )
        upcoming = sorted(
            [task for task in actionable if _due_at(task) is not None and _due_at(task) > day_end],
            key=_task_sort_key,
        )
        return WorkState(
            now=now,
            local_today=local_today,
            day_start=day_start,
            day_end=day_end,
            open_tasks=open_tasks,
            overdue=overdue,
            due_today=due_today,
            routine_today=routine_today,
            waiting=sorted(waiting, key=_task_sort_key),
            blocked=sorted(blocked, key=_task_sort_key),
            unscheduled=unscheduled,
            upcoming=upcoming,
            next_actions=self._next_actions(overdue, due_today, unscheduled, upcoming, now),
        )

    def _next_actions(
        self,
        overdue: list[object],
        due_today: list[object],
        unscheduled: list[object],
        upcoming: list[object],
        now: datetime,
    ) -> list[RankedTask]:
        ranked: list[RankedTask] = []
        for task in overdue:
            reason = "quá hạn"
            if _is_routine(task):
                reason = "việc định kỳ đã lỡ hạn"
            ranked.append(RankedTask(task=task, tier="P0", reason=reason))
        for task in due_today:
            reason = "đến hạn hôm nay"
            tier = "P3" if _is_routine(task) else "P1"
            if _priority(task) == "high" and not _is_routine(task):
                reason = "ưu tiên cao và đến hạn hôm nay"
            elif _is_routine(task):
                reason = "việc định kỳ hôm nay"
            ranked.append(RankedTask(task=task, tier=tier, reason=reason))
        for task in unscheduled:
            if _priority(task) == "high":
                ranked.append(RankedTask(task=task, tier="P2", reason="ưu tiên cao nhưng chưa có hạn"))
            elif _is_stale(task, now):
                ranked.append(RankedTask(task=task, tier="P2", reason="đã lâu chưa tiến triển"))
        if not ranked:
            for task in upcoming[:3]:
                ranked.append(RankedTask(task=task, tier="P2", reason="mốc sắp tới"))
        ranked.sort(key=lambda item: (_tier_rank(item.tier), _task_sort_key(item.task)))
        return _unique_ranked(ranked)[:5]


def _status(task: object) -> str:
    return str(getattr(task, "status", "open"))


def _priority(task: object) -> str:
    return str(getattr(task, "priority", "medium"))


def _due_at(task: object):
    return getattr(task, "due_at", None)


def _is_routine(task: object) -> bool:
    return bool(getattr(task, "recurrence_rule", None))


def _is_stale(task: object, now: datetime) -> bool:
    updated_at = getattr(task, "updated_at", now)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at < now - timedelta(days=7)


def _task_sort_key(task: object):
    due_at = _due_at(task) or datetime.max.replace(tzinfo=UTC)
    created_at = getattr(task, "created_at", datetime.max.replace(tzinfo=UTC))
    priority_rank = {"high": 0, "medium": 1, "low": 2}.get(_priority(task), 1)
    return (due_at, priority_rank, created_at)


def _tier_rank(tier: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(tier, 9)


def _unique(tasks: list[object]) -> list[object]:
    seen: set[str] = set()
    result: list[object] = []
    for task in tasks:
        task_id = getattr(task, "id", None)
        if task_id in seen:
            continue
        if task_id is not None:
            seen.add(task_id)
        result.append(task)
    return result


def _unique_ranked(items: list[RankedTask]) -> list[RankedTask]:
    seen: set[str] = set()
    result: list[RankedTask] = []
    for item in items:
        task_id = getattr(item.task, "id", None)
        if task_id in seen:
            continue
        if task_id is not None:
            seen.add(task_id)
        result.append(item)
    return result
