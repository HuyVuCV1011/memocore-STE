from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from memocore.adapters.storage.repositories import (
    FollowUpRepository,
    MemoryItemRepository,
    ProjectRepository,
    ReminderRepository,
    TaskRepository,
)


class SecretaryService:
    def __init__(
        self,
        task_repo: TaskRepository,
        reminder_repo: ReminderRepository,
        followup_repo: FollowUpRepository,
        project_repo: ProjectRepository,
        memory_repo: MemoryItemRepository,
        display_timezone: tzinfo = UTC,
    ):
        self.task_repo = task_repo
        self.reminder_repo = reminder_repo
        self.followup_repo = followup_repo
        self.project_repo = project_repo
        self.memory_repo = memory_repo
        self.display_timezone = display_timezone

    async def today(self) -> str:
        now = datetime.now(UTC)
        local_now = now.astimezone(self.display_timezone)
        return await self.agenda_for_date(local_now.date(), "Hôm nay")

    async def tomorrow(self) -> str:
        now = datetime.now(UTC)
        local_now = now.astimezone(self.display_timezone)
        return await self.agenda_for_date(local_now.date() + timedelta(days=1), "Ngày mai")

    async def agenda_for_date(self, target_date: date, title: str | None = None) -> str:
        now = datetime.now(UTC)
        day_start = datetime.combine(
            target_date, time.min, tzinfo=self.display_timezone
        ).astimezone(UTC)
        day_end = datetime.combine(
            target_date, time.max, tzinfo=self.display_timezone
        ).astimezone(UTC)
        tasks = await self.task_repo.list_active()
        due = [
            task
            for task in tasks
            if task.due_at and day_start <= task.due_at <= day_end
        ]
        if target_date <= now.astimezone(self.display_timezone).date():
            due = [
                task
                for task in tasks
                if task.due_at and (task.due_at <= now or day_start <= task.due_at <= day_end)
            ]
        waiting = [task for task in tasks if task.status in {"waiting", "blocked"}]
        reminders = [
            reminder
            for reminder in await self.reminder_repo.list_recent(limit=100)
            if reminder.remind_at and day_start <= reminder.remind_at <= day_end
        ]
        heading = title or _day_label(target_date, now.astimezone(self.display_timezone).date()).capitalize()
        lines = [f"{heading} - {target_date:%d/%m/%Y}"]
        if due:
            lines.append("")
            lines.append("Cần làm")
            lines.extend(_task_lines(due, display_timezone=self.display_timezone))
        else:
            lines.append("")
            lines.append("Cần làm")
            lines.append(f"Không có task nào đến hạn {_day_label(target_date, now.astimezone(self.display_timezone).date())}.")
        if reminders:
            lines.append("")
            lines.append("Nhắc nhở")
            lines.extend(_reminder_lines(reminders, self.display_timezone))
        if waiting:
            lines.append("")
            lines.append(f"Đang chờ hoặc bị chặn: {len(waiting)}")
        return "\n".join(lines)

    async def tasks(self) -> str:
        tasks = await self.task_repo.list_active()
        lines = ["Tasks đang mở"]
        if tasks:
            lines.extend(_task_lines(tasks, display_timezone=self.display_timezone))
        else:
            lines.append("Không có task đang mở.")
        return "\n".join(lines)

    async def reminders(self) -> str:
        reminders = await self.reminder_repo.list_recent()
        if not reminders:
            return "Nhắc nhở\nChưa có reminder nào."
        return "Nhắc nhở\n" + "\n".join(_reminder_lines(reminders, self.display_timezone))

    async def waiting(self) -> str:
        tasks = await self.task_repo.list_active()
        waiting = [task for task in tasks if task.status in {"waiting", "blocked"}]
        followups = await self.followup_repo.list_open()
        lines = ["Đang chờ và follow-up"]
        if waiting:
            lines.extend(_task_lines(waiting, display_timezone=self.display_timezone))
        else:
            lines.append("Không có task đang chờ.")
        lines.extend(f"{index}. Follow-up: {item.title}" for index, item in enumerate(followups, 1))
        return "\n".join(lines)

    async def projects(self) -> str:
        projects = await self.project_repo.list_all()
        if not projects:
            return "Projects\nChưa có project nào."
        active_tasks = await self.task_repo.list_active()
        tasks_by_project = defaultdict(list)
        for task in active_tasks:
            tasks_by_project[task.project_id].append(task)
        lines = ["Projects"]
        for index, project in enumerate(projects, 1):
            project_tasks = tasks_by_project[project.id]
            lines.append("")
            lines.append(f"{index}. {project.name}")
            lines.append(f"   Task đang mở: {len(project_tasks)}")
            next_task = _next_dated_task(project_tasks)
            if next_task:
                lines.append(
                    f"   Tiếp theo: {_format_due(next_task.due_at, self.display_timezone)} - {next_task.title}"
                )
        return "\n".join(lines)

    async def project_tasks(self, project_name: str) -> str:
        projects = await self.project_repo.list_all()
        matches = [
            project
            for project in projects
            if _normalize_text(project_name) in _normalize_text(project.name)
            or _normalize_text(project.name) in _normalize_text(project_name)
        ]
        if not matches:
            return f"Project {project_name}\nMình chưa thấy project này trong dữ liệu."
        if len(matches) > 1:
            names = "\n".join(f"{index}. {project.name}" for index, project in enumerate(matches, 1))
            return f"Mình thấy vài project khớp. Bạn muốn xem project nào?\n{names}"
        project = matches[0]
        tasks = [
            task
            for task in await self.task_repo.list_active()
            if task.project_id == project.id
            or _normalize_text(project.name) in _normalize_text(task.title)
            or _normalize_text(project.name) in _normalize_text(task.description)
        ]
        lines = [f"Project {project.name}", "Task đang mở"]
        if tasks:
            lines.extend(_task_lines(tasks, display_timezone=self.display_timezone))
        else:
            lines.append("Không có task đang mở cho project này.")
        return "\n".join(lines)

    async def memories(self, bucket: str | None = None) -> str:
        memories = await self.memory_repo.list_active()
        if bucket:
            memories = [item for item in memories if item.bucket == bucket]
        if not memories:
            return "Memory\nChưa có memory nào."
        return "Memory\n" + "\n".join(
            f"- [{item.bucket}] {item.content}" for item in memories[:20]
        )


def _task_lines(tasks, display_timezone: tzinfo = UTC) -> list[str]:
    lines: list[str] = []
    for index, task in enumerate(tasks, 1):
        details = [f"Hạn: {_format_due(task.due_at, display_timezone)}"]
        details.append(f"Trạng thái: {_label_status(task.status)}")
        if task.priority:
            details.append(f"Ưu tiên: {_label_priority(task.priority)}")
        lines.append(f"{index}. {task.title}")
        lines.append(f"   {' | '.join(details)}")
    return lines


def _format_time(value: datetime | None, display_timezone: tzinfo) -> str:
    if value is None:
        return "chưa rõ giờ"
    return value.astimezone(display_timezone).strftime("%H:%M")


def _format_due(value: datetime | None, display_timezone: tzinfo) -> str:
    if value is None:
        return "chưa có hạn"
    local_value = value.astimezone(display_timezone)
    today = datetime.now(UTC).astimezone(display_timezone).date()
    day_label = _day_label(local_value.date(), today)
    return f"{_format_time(value, display_timezone)} {day_label}"


def _reminder_lines(reminders, display_timezone: tzinfo) -> list[str]:
    lines: list[str] = []
    sorted_reminders = sorted(
        reminders,
        key=lambda item: item.remind_at or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    for index, item in enumerate(sorted_reminders, 1):
        lines.append(f"{index}. {item.title}")
        lines.append(
            f"   Lúc: {_format_due(item.remind_at, display_timezone)} | Trạng thái: {_label_status(item.status)}"
        )
    return lines


def _next_dated_task(tasks):
    dated = [task for task in tasks if task.due_at]
    if not dated:
        return None
    return min(dated, key=lambda task: task.due_at)


def _day_label(value: date, today: date) -> str:
    if value == today:
        return "hôm nay"
    delta = (value - today).days
    if delta == 1:
        return "ngày mai"
    if delta == -1:
        return "hôm qua"
    return value.strftime("%d/%m/%Y")


def _label_status(value) -> str:
    labels = {
        "candidate": "mới ghi nhận",
        "open": "đang mở",
        "waiting": "đang chờ",
        "blocked": "bị chặn",
        "done": "đã xong",
        "cancelled": "đã hủy",
        "scheduled": "đã lên lịch",
        "sent": "đã gửi",
        "failed": "lỗi gửi",
    }
    return labels.get(str(value), str(value))


def _label_priority(value: str) -> str:
    labels = {"low": "thấp", "medium": "vừa", "high": "cao"}
    return labels.get(value, value)


def _normalize_text(value: str) -> str:
    import unicodedata

    lowered = value.lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    ascii_text = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join("".join(char if char.isalnum() else " " for char in ascii_text).split())
