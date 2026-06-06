from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from memocore.adapters.storage.repositories import (
    FollowUpRepository,
    MeetingRepository,
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
        meeting_repo: MeetingRepository | None = None,
    ):
        self.task_repo = task_repo
        self.reminder_repo = reminder_repo
        self.followup_repo = followup_repo
        self.project_repo = project_repo
        self.memory_repo = memory_repo
        self.display_timezone = display_timezone
        self.meeting_repo = meeting_repo

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

    async def daily_briefing(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        local_now = now.astimezone(self.display_timezone)
        day_start = datetime.combine(local_now.date(), time.min, tzinfo=self.display_timezone).astimezone(UTC)
        day_end = datetime.combine(local_now.date(), time.max, tzinfo=self.display_timezone).astimezone(UTC)
        tasks = await self.task_repo.list_active()
        overdue = [task for task in tasks if task.due_at and task.due_at < day_start]
        due_today = [task for task in tasks if task.due_at and day_start <= task.due_at <= day_end]
        undated_priority = [task for task in tasks if task.due_at is None][:5]
        waiting = [task for task in tasks if task.status in {"waiting", "blocked"}]
        reminders = [
            reminder
            for reminder in await self.reminder_repo.list_recent(limit=100)
            if reminder.remind_at and day_start <= reminder.remind_at <= day_end
        ]
        followups = await self.followup_repo.list_open()
        meetings = []
        if self.meeting_repo is not None:
            meetings = [
                meeting
                for meeting in await self.meeting_repo.list_upcoming(day_start)
                if meeting.starts_at and day_start <= meeting.starts_at <= day_end
            ]

        lines = [f"Briefing hôm nay - {local_now.date():%d/%m/%Y}"]
        lines.append("")
        lines.append(f"Tổng quan: {len(due_today)} task hôm nay, {len(overdue)} quá hạn, {len(reminders)} reminder, {len(followups)} follow-up mở.")
        lines.append("")
        lines.append("Quá hạn")
        lines.extend(_task_lines(overdue, self.display_timezone) if overdue else ["Không có task quá hạn."])
        lines.append("")
        lines.append("Hôm nay")
        lines.extend(_task_lines(due_today, self.display_timezone) if due_today else ["Không có task đến hạn hôm nay."])
        if reminders:
            lines.append("")
            lines.append("Reminder hôm nay")
            lines.extend(_reminder_lines(reminders, self.display_timezone))
        if meetings:
            lines.append("")
            lines.append("Lịch/meeting hôm nay")
            lines.extend(_meeting_lines(meetings, self.display_timezone))
        if followups:
            lines.append("")
            lines.append("Follow-up đang mở")
            lines.extend(_followup_lines(followups, self.display_timezone))
        if waiting:
            lines.append("")
            lines.append("Đang chờ hoặc bị chặn")
            lines.extend(_task_lines(waiting, self.display_timezone))
        if undated_priority:
            lines.append("")
            lines.append("Không có hạn nhưng nên rà soát")
            lines.extend(_task_lines(undated_priority, self.display_timezone))
        return "\n".join(lines)

    async def weekly_review(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        local_now = now.astimezone(self.display_timezone)
        since = now - timedelta(days=7)
        done = await self.task_repo.list_done_since(since)
        active = await self.task_repo.list_active()
        followups = await self.followup_repo.list_open()
        overdue = [task for task in active if task.due_at and task.due_at < now]
        lines = [f"Weekly review - tuần kết thúc {local_now.date():%d/%m/%Y}"]
        lines.append("")
        lines.append(f"Đã xong tuần này: {len(done)} task.")
        lines.extend(_task_lines(done[:10], self.display_timezone) if done else ["Chưa có task nào được đánh dấu xong tuần này."])
        lines.append("")
        lines.append(f"Còn mở: {len(active)} task, trong đó {len(overdue)} quá hạn.")
        lines.extend(_task_lines(overdue[:10], self.display_timezone) if overdue else ["Không có task quá hạn."])
        lines.append("")
        lines.append(f"Follow-up còn mở: {len(followups)}.")
        if followups:
            lines.extend(_followup_lines(followups[:10], self.display_timezone))
        return "\n".join(lines)

    async def deadline_nudges(
        self,
        now: datetime | None = None,
        stale_followup_days: int = 3,
    ) -> list[tuple[str, str, str]]:
        now = now or datetime.now(UTC)
        local_now = now.astimezone(self.display_timezone)
        tasks = await self.task_repo.list_active()
        followups = await self.followup_repo.list_open()
        nudges: list[tuple[str, str, str]] = []
        for task in tasks:
            if task.due_at and task.due_at < now:
                nudges.append(
                    (
                        "task",
                        task.id,
                        f"Task quá hạn: {task.title}\nHạn: {_format_due(task.due_at, self.display_timezone)}",
                    )
                )
        stale_before = now - timedelta(days=stale_followup_days)
        for followup in followups:
            is_due = followup.due_at is not None and followup.due_at < now
            is_stale = followup.due_at is None and followup.created_at < stale_before
            if is_due or is_stale:
                label = "quá hạn" if is_due else f"chưa cập nhật {stale_followup_days}+ ngày"
                due_text = _format_due(followup.due_at, self.display_timezone) if followup.due_at else local_now.date().strftime("%d/%m/%Y")
                nudges.append(("followup", followup.id, f"Follow-up {label}: {followup.title}\nMốc: {due_text}"))
        return nudges


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


def _followup_lines(followups, display_timezone: tzinfo) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(followups, 1):
        details = [f"Hạn: {_format_due(item.due_at, display_timezone)}"]
        lines.append(f"{index}. {item.title}")
        lines.append(f"   {' | '.join(details)}")
    return lines


def _meeting_lines(meetings, display_timezone: tzinfo) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(meetings, 1):
        starts = _format_due(item.starts_at, display_timezone)
        lines.append(f"{index}. {item.title}")
        lines.append(f"   Bắt đầu: {starts}")
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
