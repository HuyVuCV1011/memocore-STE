from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from memocore.adapters.storage.repositories import (
    CommitmentRepository,
    FollowUpRepository,
    MeetingRepository,
    MemoryItemRepository,
    NoteRepository,
    PersonRepository,
    ProjectRepository,
    ReminderRepository,
    TaskRepository,
)
from memocore.domain.models import EventType, MemoryBucket, MemoryKind, MemoryStatus
from memocore.services.event_service import EventService


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
        person_repo: PersonRepository | None = None,
        commitment_repo: CommitmentRepository | None = None,
        note_repo: NoteRepository | None = None,
        event_service: EventService | None = None,
    ):
        self.task_repo = task_repo
        self.reminder_repo = reminder_repo
        self.followup_repo = followup_repo
        self.project_repo = project_repo
        self.memory_repo = memory_repo
        self.display_timezone = display_timezone
        self.meeting_repo = meeting_repo
        self.person_repo = person_repo
        self.commitment_repo = commitment_repo
        self.note_repo = note_repo
        self.event_service = event_service

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
        meetings = []
        if self.meeting_repo is not None:
            meetings = [
                meeting
                for meeting in await self.meeting_repo.list_all()
                if meeting.starts_at and day_start <= meeting.starts_at <= day_end
            ]
        heading = title or _day_label(target_date, now.astimezone(self.display_timezone).date()).capitalize()
        lines = [f"{heading} - {_weekday_label(target_date)}, {target_date:%d/%m/%Y}"]
        if due:
            lines.append("")
            lines.append("Cần làm")
            lines.extend(_agenda_task_lines(due, display_timezone=self.display_timezone))
        else:
            lines.append("")
            lines.append("Cần làm")
            lines.append(f"Không có task nào đến hạn {_day_label(target_date, now.astimezone(self.display_timezone).date())}.")
        if reminders:
            lines.append("")
            lines.append("Nhắc nhở")
            lines.extend(_reminder_lines(reminders, self.display_timezone))
        if meetings:
            lines.append("")
            lines.append("Lịch/meeting")
            lines.extend(_meeting_lines(meetings, self.display_timezone))
        if waiting:
            lines.append("")
            lines.append("Đang chờ hoặc bị chặn")
            lines.extend(_agenda_task_lines(waiting, display_timezone=self.display_timezone))
        return "\n".join(lines)

    async def work_dashboard(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        tasks = await self.task_repo.list_active()
        top = await self._top_priority_tasks(tasks, now, limit=5)
        overdue = [task for task in tasks if task.due_at and task.due_at < now]
        waiting = [task for task in tasks if str(task.status) in {"waiting", "blocked"}]
        commitments = await self.commitment_repo.list_open() if self.commitment_repo else []
        lines = ["Work dashboard"]
        lines.append(
            f"Tổng quan: {len(tasks)} task mở, {len(overdue)} quá hạn, {len(waiting)} đang chờ/bị chặn, {len(commitments)} commitment mở."
        )
        lines.extend(["", "Top priorities"])
        lines.extend(_scored_task_lines(top, self.display_timezone) if top else ["Không có task mở."])
        lines.extend(["", "Overdue"])
        lines.extend(_task_lines(overdue[:5], self.display_timezone) if overdue else ["Không có task quá hạn."])
        lines.extend(["", "Waiting on people"])
        lines.extend(_task_lines(waiting[:5], self.display_timezone) if waiting else ["Không có task đang chờ hoặc bị chặn."])
        lines.extend(["", "Commitments"])
        lines.extend(_commitment_lines(commitments[:5], self.display_timezone) if commitments else ["Không có commitment đang mở."])
        lines.extend(["", "Quick actions"])
        lines.append("- /tasks để thao tác nhanh với task")
        lines.append("- /reminders để thao tác nhanh với reminder")
        lines.append("- /prep <person/project> trước cuộc họp")
        return "\n".join(lines)

    async def tasks(self) -> str:
        tasks = await self.task_repo.list_active()
        lines = ["Tasks đang mở"]
        if tasks:
            lines.extend(_agenda_task_lines(tasks, display_timezone=self.display_timezone))
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

    async def projects(self, scope: str | None = None) -> str:
        projects = await self.project_repo.list_all()
        if scope:
            normalized_scope = _normalize_text(scope)
            projects = [
                project
                for project in projects
                if normalized_scope in _normalize_text(project.name)
                or normalized_scope in _normalize_text(project.summary)
            ]
        if not projects:
            suffix = f" liên quan đến {scope}" if scope else ""
            return f"Projects\nEm chưa thấy project nào{suffix}."
        active_tasks = await self.task_repo.list_active()
        tasks_by_project = defaultdict(list)
        for task in active_tasks:
            tasks_by_project[task.project_id].append(task)
        core_projects = [project for project in projects if not _is_project_idea_or_low_priority(project.name)]
        idea_projects = [project for project in projects if _is_project_idea_or_low_priority(project.name)]
        title = f"Projects {scope.upper()}" if scope else "Projects"
        lines = [title, f"Em đang theo dõi {len(projects)} project/năng lực. Nhóm ý tưởng hoặc ưu tiên thấp được tách riêng để tránh lẫn với project chính."]
        lines.append("")
        lines.append("Đang theo dõi chính")
        for index, project in enumerate(core_projects, 1):
            project_tasks = tasks_by_project[project.id]
            lines.append(f"{index}. {project.name}")
            lines.append(f"   Task đang mở: {len(project_tasks)}")
            next_task = _next_dated_task(project_tasks)
            if next_task:
                lines.append(
                    f"   Tiếp theo: {_format_due(next_task.due_at, self.display_timezone)} - {next_task.title}"
                )
        if idea_projects:
            lines.append("")
            lines.append("Ý tưởng / cần review")
            for index, project in enumerate(idea_projects, 1):
                project_tasks = tasks_by_project[project.id]
                lines.append(f"{index}. {project.name} - {len(project_tasks)} task mở")
        return "\n".join(lines)

    async def ordered_task_ids_for_view(self, source_view: str) -> list[str]:
        normalized = source_view.removeprefix("query_")
        tasks = await self.task_repo.list_active()
        if normalized == "briefing":
            top = await self._top_priority_tasks(tasks, datetime.now(UTC), limit=3)
            return [task.id for task, _score, _reasons in top]
        if normalized in {"today", "todays"}:
            local_today = datetime.now(UTC).astimezone(self.display_timezone).date()
            day_start = datetime.combine(
                local_today, time.min, tzinfo=self.display_timezone
            ).astimezone(UTC)
            day_end = datetime.combine(
                local_today, time.max, tzinfo=self.display_timezone
            ).astimezone(UTC)
            now = datetime.now(UTC)
            due = [
                task
                for task in tasks
                if task.due_at and (task.due_at <= now or day_start <= task.due_at <= day_end)
            ]
            waiting = [
                task for task in tasks if str(task.status) in {"waiting", "blocked"}
            ]
            return list(dict.fromkeys([task.id for task in [*due, *waiting]]))
        if normalized == "tasks":
            return [task.id for task in tasks[:5]]
        return []

    async def project_tasks(self, project_name: str) -> str:
        matches = await self.project_repo.find_matches(project_name)
        if not matches:
            return f"Project {project_name}\nEm chưa thấy project này trong dữ liệu."
        if len(matches) > 1:
            return _ambiguous_entity_message("project", matches)
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
            return "Ghi nhớ\nChưa có ghi nhớ nào."
        sections = [
            ("Hồ sơ và cách làm việc", "profile"),
            ("Dự án và công việc", "project"),
            ("Tương tác", "interaction"),
        ]
        lines = ["Ghi nhớ về Vũ"]
        for heading, bucket_name in sections:
            items = [item for item in memories if str(item.bucket) == bucket_name]
            if not items:
                continue
            lines.extend(["", heading])
            lines.extend(f"- {item.content}" for item in items)
        return "\n".join(lines)

    async def daily_briefing(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        local_now = now.astimezone(self.display_timezone)
        day_start = datetime.combine(local_now.date(), time.min, tzinfo=self.display_timezone).astimezone(UTC)
        day_end = datetime.combine(local_now.date(), time.max, tzinfo=self.display_timezone).astimezone(UTC)
        tasks = await self.task_repo.list_active()
        top = await self._top_priority_tasks(tasks, now, limit=3)
        overdue = [task for task in tasks if task.due_at and task.due_at < day_start]
        due_today = [task for task in tasks if task.due_at and day_start <= task.due_at <= day_end]
        upcoming_top = [
            task
            for task, _score, _reasons in top
            if task.due_at and task.due_at > day_end
        ]
        undated_priority = [task for task in tasks if task.due_at is None][:5]
        waiting = [task for task in tasks if task.status in {"waiting", "blocked"}]
        reminders = [
            reminder
            for reminder in await self.reminder_repo.list_recent(limit=100)
            if reminder.remind_at and day_start <= reminder.remind_at <= day_end
        ]
        followups = await self.followup_repo.list_open()
        commitments = await self.commitment_repo.list_open() if self.commitment_repo else []
        meetings = []
        if self.meeting_repo is not None:
            meetings = [
                meeting
                for meeting in await self.meeting_repo.list_upcoming(day_start)
                if meeting.starts_at and day_start <= meeting.starts_at <= day_end
            ]

        overdue_followups = [
            item for item in followups if item.due_at is not None and item.due_at < now
        ]
        due_commitments = [
            item
            for item in commitments
            if item.due_at is not None and item.due_at <= day_end
        ]
        lines = [f"Briefing hôm nay - {local_now.date():%d/%m/%Y}", ""]
        lines.append("Nhận định")
        lines.append(
            _briefing_assessment(
                overdue=overdue,
                due_today=due_today,
                meetings=meetings,
                waiting=waiting,
                overdue_followups=overdue_followups,
                due_commitments=due_commitments,
                display_timezone=self.display_timezone,
            )
        )
        lines.extend(["", "Điểm cần chú ý"])
        signals = _briefing_signals(
            overdue=overdue,
            due_today=due_today,
            reminders=reminders,
            meetings=meetings,
            waiting=waiting,
            overdue_followups=overdue_followups,
            due_commitments=due_commitments,
            upcoming_top=upcoming_top,
            display_timezone=self.display_timezone,
        )
        lines.extend(signals or ["- Chưa thấy deadline, meeting hay open loop cấp bách hôm nay."])
        lines.extend(["", "Nên làm tiếp"])
        if top:
            lines.extend(_briefing_action_lines(top, self.display_timezone))
        elif meetings:
            lines.append(f"1. Chuẩn bị trước cho meeting “{meetings[0].title}”.")
        elif followups:
            lines.append(f"1. Chọn một follow-up để khép lại: {followups[0].title}.")
        elif undated_priority:
            lines.append(f"1. Gắn hạn hoặc quyết định bước tiếp theo cho: {undated_priority[0].title}.")
        else:
            lines.append(
                "1. Lịch đang thoáng. Chọn một ưu tiên chủ động hoặc ghi việc mới bằng /task."
            )
        return "\n".join(lines)

    async def people(self) -> str:
        if self.person_repo is None:
            return "People\nPeople repository chưa được cấu hình."
        people = await self.person_repo.list_all()
        if not people:
            return "People\nChưa có person nào."
        lines = ["People"]
        for index, person in enumerate(people, 1):
            aliases = f" | aliases: {', '.join(person.aliases)}" if person.aliases else ""
            relationship = f" | {person.relationship}" if person.relationship else ""
            lines.append(f"{index}. {person.display_name}{relationship}{aliases}")
        return "\n".join(lines)

    async def commitments(self) -> str:
        if self.commitment_repo is None:
            return "Commitments\nCommitment repository chưa được cấu hình."
        commitments = await self.commitment_repo.list_open()
        lines = ["Commitments đang mở"]
        if commitments:
            lines.extend(_commitment_lines(commitments, self.display_timezone))
        else:
            lines.append("Không có commitment đang mở.")
        return "\n".join(lines)

    async def person_context(self, query: str) -> str:
        if self.person_repo is None:
            return "Person context\nPeople repository chưa được cấu hình."
        matches = await self.person_repo.find_matches(query)
        if not matches:
            return f"Person {query}\nEm chưa thấy person này trong dữ liệu."
        if len(matches) > 1:
            return _ambiguous_entity_message("person", matches)
        person = matches[0]
        tasks = await self.task_repo.list_active_by_person(person.id)
        followups = await self.followup_repo.list_open_by_person(person.id)
        memories = await self.memory_repo.list_active_by_person(person.id)
        commitments = (
            await self.commitment_repo.list_open_by_person(person.id)
            if self.commitment_repo is not None
            else []
        )
        meetings = (
            await self.meeting_repo.list_by_person(person.id)
            if self.meeting_repo is not None
            else []
        )
        note_map = await self._note_map(
            [
                *(item.source_note_id for item in tasks),
                *(item.source_note_id for item in followups if item.source_note_id),
                *(item.source_note_id for item in memories),
                *(item.source_note_id for item in commitments if item.source_note_id),
                *(item.source_note_id for item in meetings),
            ]
        )
        lines = [f"Person {person.display_name}"]
        if person.relationship:
            lines.append(f"Quan hệ: {person.relationship}")
        if person.notes:
            lines.append(f"Ghi chú: {person.notes}")
        lines.append("")
        lines.append(f"Tổng quan: {len(tasks)} task, {len(commitments)} commitment, {len(followups)} follow-up, {len(meetings)} meeting, {len(memories)} memory.")
        lines.append("")
        lines.append("Commitments")
        lines.extend(_commitment_lines(commitments, self.display_timezone, note_map) if commitments else ["Không có commitment đang mở."])
        lines.append("")
        lines.append("Task liên quan")
        lines.extend(_task_lines(tasks, self.display_timezone, note_map) if tasks else ["Không có task đang mở."])
        lines.append("")
        lines.append("Follow-up")
        lines.extend(_followup_lines(followups, self.display_timezone, note_map) if followups else ["Không có follow-up đang mở."])
        if meetings:
            lines.append("")
            lines.append("Meeting gần đây/sắp tới")
            lines.extend(_meeting_lines(meetings[:5], self.display_timezone, note_map))
        if memories:
            lines.append("")
            lines.append("Memory liên quan")
            lines.extend(_memory_lines(memories[:8], note_map))
        return "\n".join(lines)

    async def project_context(self, query: str) -> str:
        matches = await self.project_repo.find_matches(query)
        if not matches:
            return f"Project {query}\nEm chưa thấy project này trong dữ liệu."
        if len(matches) > 1:
            return _ambiguous_entity_message("project", matches)
        project = matches[0]
        tasks = await self.task_repo.list_active_by_project(project.id)
        followups = await self.followup_repo.list_open_by_project(project.id)
        memories = await self.memory_repo.list_active_by_project(project.id)
        commitments = (
            await self.commitment_repo.list_open_by_project(project.id)
            if self.commitment_repo is not None
            else []
        )
        meetings = (
            await self.meeting_repo.list_by_project(project.id)
            if self.meeting_repo is not None
            else []
        )
        note_map = await self._note_map(
            [
                *(item.source_note_id for item in tasks),
                *(item.source_note_id for item in followups if item.source_note_id),
                *(item.source_note_id for item in memories),
                *(item.source_note_id for item in commitments if item.source_note_id),
                *(item.source_note_id for item in meetings),
            ]
        )
        lines = [f"Project {project.name}"]
        if project.summary:
            lines.append(project.summary)
        lines.append("")
        lines.append(f"Tổng quan: {len(tasks)} task, {len(commitments)} commitment, {len(followups)} follow-up, {len(meetings)} meeting, {len(memories)} memory.")
        lines.append("")
        lines.append("Task đang mở")
        lines.extend(_task_lines(tasks, self.display_timezone, note_map) if tasks else ["Không có task đang mở."])
        lines.append("")
        lines.append("Commitments")
        lines.extend(_commitment_lines(commitments, self.display_timezone, note_map) if commitments else ["Không có commitment đang mở."])
        lines.append("")
        lines.append("Follow-up")
        lines.extend(_followup_lines(followups, self.display_timezone, note_map) if followups else ["Không có follow-up đang mở."])
        if meetings:
            lines.append("")
            lines.append("Meeting liên quan")
            lines.extend(_meeting_lines(meetings[:5], self.display_timezone, note_map))
        if memories:
            lines.append("")
            lines.append("Memory liên quan")
            lines.extend(_memory_lines(memories[:8], note_map))
        return "\n".join(lines)

    async def meeting_prep(self, query: str) -> str:
        people = await self.person_repo.find_matches(query) if self.person_repo else []
        projects = await self.project_repo.find_matches(query)
        combined_count = len(people) + len(projects)
        if combined_count > 1:
            return _ambiguous_prep_message(people, projects)
        person = people[0] if people else None
        project = projects[0] if projects else None
        if person is not None:
            heading = f"Meeting prep với {person.display_name}"
            tasks = await self.task_repo.list_active_by_person(person.id)
            followups = await self.followup_repo.list_open_by_person(person.id)
            memories = await self.memory_repo.list_active_by_person(person.id)
            commitments = await self.commitment_repo.list_open_by_person(person.id) if self.commitment_repo else []
            meetings = await self.meeting_repo.list_by_person(person.id) if self.meeting_repo else []
        elif project is not None:
            heading = f"Meeting prep cho project {project.name}"
            tasks = await self.task_repo.list_active_by_project(project.id)
            followups = await self.followup_repo.list_open_by_project(project.id)
            memories = await self.memory_repo.list_active_by_project(project.id)
            commitments = await self.commitment_repo.list_open_by_project(project.id) if self.commitment_repo else []
            meetings = await self.meeting_repo.list_by_project(project.id) if self.meeting_repo else []
        else:
            return f"Meeting prep {query}\nEm chưa tìm thấy person hoặc project khớp."
        note_map = await self._note_map(
            [
                *(item.source_note_id for item in tasks),
                *(item.source_note_id for item in followups if item.source_note_id),
                *(item.source_note_id for item in memories),
                *(item.source_note_id for item in commitments if item.source_note_id),
                *(item.source_note_id for item in meetings),
            ]
        )
        lines = [heading, ""]
        lines.append("Upcoming/recent meetings")
        lines.extend(_meeting_lines(meetings[:5], self.display_timezone, note_map) if meetings else ["Chưa có meeting liên quan."])
        lines.extend(["", "Open commitments / Commitments còn mở"])
        lines.extend(_commitment_lines(commitments, self.display_timezone, note_map) if commitments else ["Không có commitment đang mở."])
        waiting_tasks = [task for task in tasks if str(task.status) in {"waiting", "blocked"}]
        lines.extend(["", "Waiting/follow-ups"])
        wait_items = _task_lines(waiting_tasks, self.display_timezone, note_map) if waiting_tasks else []
        followup_items = _followup_lines(followups, self.display_timezone, note_map) if followups else []
        lines.extend(wait_items + followup_items if wait_items or followup_items else ["Không có waiting/follow-up đang mở."])
        lines.extend(["", "Relevant memory"])
        lines.extend(_memory_lines(memories[:8], note_map) if memories else ["Chưa có memory liên quan."])
        lines.extend(["", "Suggested next questions"])
        lines.extend(_suggested_questions(commitments, followups, tasks))
        return "\n".join(lines)

    async def context(self, query: str) -> str:
        people = await self.person_repo.find_matches(query) if self.person_repo else []
        projects = await self.project_repo.find_matches(query)
        if len(people) + len(projects) > 1:
            return _ambiguous_prep_message(people, projects)
        if people:
            return await self.person_context(people[0].display_name)
        if projects:
            return await self.project_context(projects[0].name)
        return f"Context {query}\nEm chưa tìm thấy person hoặc project khớp."

    async def _find_project(self, query: str):
        matches = await self.project_repo.find_matches(query)
        return matches[0] if len(matches) == 1 else None

    async def weekly_review(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        local_now = now.astimezone(self.display_timezone)
        since = now - timedelta(days=7)
        done = await self.task_repo.list_done_since(since)
        active = await self.task_repo.list_active()
        followups = await self.followup_repo.list_open()
        commitments = await self.commitment_repo.list_open() if self.commitment_repo else []
        goals = await self._goal_memories()
        overdue = [task for task in active if task.due_at and task.due_at < now]
        top = await self._top_priority_tasks(active, now, limit=5)
        projects = await self.project_repo.list_all()
        task_project_ids = {task.project_id for task in active if task.project_id}
        projects_without_next_action = [
            project for project in projects if str(project.status) == "active" and project.id not in task_project_ids
        ]
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
        lines.extend(["", "People you owe / owe you"])
        lines.extend(_commitment_lines(commitments[:10], self.display_timezone) if commitments else ["Không có commitment đang mở."])
        lines.extend(["", "Top open loops tuần tới"])
        lines.extend(_scored_task_lines(top, self.display_timezone) if top else ["Không có task mở."])
        lines.extend(["", "Goals"])
        lines.extend([f"- {item.content}" for item in goals[:5]] if goals else ["Chưa có goal đang theo dõi."])
        lines.append("Câu hỏi review: tuần này có tiến gần goal nào không? Task nào không còn đáng làm?")
        if projects_without_next_action:
            lines.extend(["", "Projects chưa có next action"])
            lines.extend(f"- {project.name}" for project in projects_without_next_action[:10])
        return "\n".join(lines)

    async def end_of_day_review(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        since = now - timedelta(days=1)
        done = await self.task_repo.list_done_since(since)
        active = await self.task_repo.list_active()
        carry = await self._top_priority_tasks(active, now, limit=5)
        followups = await self.followup_repo.list_open()
        lines = [f"End-of-day review - {now.astimezone(self.display_timezone).date():%d/%m/%Y}"]
        lines.extend(["", "Hôm nay xong gì"])
        lines.extend(_task_lines(done[:10], self.display_timezone) if done else ["Chưa có task nào được đánh dấu xong trong 24h qua."])
        lines.extend(["", "Còn gì kéo sang mai"])
        lines.extend(_scored_task_lines(carry, self.display_timezone) if carry else ["Không có task mở."])
        lines.extend(["", "Open loop mới cần để ý"])
        lines.extend(_followup_lines(followups[:10], self.display_timezone) if followups else ["Không có follow-up mở."])
        return "\n".join(lines)

    async def goals(self) -> str:
        goals = await self._goal_memories()
        lines = ["Goals"]
        if goals:
            lines.extend(f"{index}. {item.content}" for index, item in enumerate(goals, 1))
        else:
            lines.append("Chưa có goal. Lưu bằng /mem Goal: <mục tiêu> #mem hoặc ghi tự nhiên rồi xác nhận vào memory.")
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

    async def _top_priority_tasks(self, tasks, now: datetime, limit: int = 3):
        scored = [(await self._open_loop_score(task, now), task) for task in tasks]
        scored.sort(key=lambda item: (-item[0][0], item[1].due_at or datetime.max.replace(tzinfo=UTC), item[1].created_at))
        return [(task, score, reasons) for (score, reasons), task in scored[:limit]]

    async def _open_loop_score(self, task, now: datetime) -> tuple[int, list[str]]:
        score = 0
        reasons: list[str] = []
        if task.due_at and task.due_at < now:
            score += 50
            reasons.append("overdue")
        elif task.due_at and task.due_at <= now + timedelta(days=1):
            score += 35
            reasons.append("due soon")
        if str(task.status) in {"waiting", "blocked"}:
            score += 20
            reasons.append(str(task.status))
        if task.person_id:
            score += 8
            reasons.append("person-linked")
        if task.project_id:
            score += 5
            reasons.append("project-linked")
        if str(task.priority) == "high":
            score += 15
            reasons.append("high priority")
        elif str(task.priority) == "low":
            score -= 5
        if task.updated_at < now - timedelta(days=7):
            score += 10
            reasons.append("stale 7d+")
        if self.commitment_repo is not None and (task.person_id or task.project_id):
            commitments = []
            if task.person_id:
                commitments.extend(await self.commitment_repo.list_open_by_person(task.person_id))
            if task.project_id:
                commitments.extend(await self.commitment_repo.list_open_by_project(task.project_id))
            if commitments:
                score += 12
                reasons.append("has commitment")
        if self.event_service is not None:
            nudged = await self.event_service.exists_recent(
                EventType.NUDGE_SENT,
                "task",
                task.id,
                now - timedelta(days=14),
            )
            if nudged:
                score += 6
                reasons.append("nudged")
        return score, reasons or ["open"]

    async def _note_map(self, note_ids: list[str]) -> dict[str, object]:
        if self.note_repo is None:
            return {}
        result = {}
        for note_id in sorted(set(note_ids)):
            note = await self.note_repo.get_by_id(note_id)
            if note is not None:
                result[note_id] = note
        return result

    async def _goal_memories(self):
        memories = await self.memory_repo.list_active()
        return [
            item
            for item in memories
            if str(item.status) == MemoryStatus.ACTIVE.value
            and (
                str(item.kind) == MemoryKind.GOAL.value
                or item.kind == MemoryKind.GOAL
                or str(item.kind) == MemoryKind.PROJECT_STATE.value
                or str(item.bucket) == MemoryBucket.PROFILE.value
            )
            and _looks_like_goal(item.content)
        ]


def _briefing_assessment(
    *,
    overdue,
    due_today,
    meetings,
    waiting,
    overdue_followups,
    due_commitments,
    display_timezone: tzinfo,
) -> str:
    pressure = (
        len(overdue) * 3
        + len(overdue_followups) * 2
        + len(due_commitments) * 2
        + len(due_today)
        + len(meetings)
        + len(waiting)
    )
    if pressure == 0:
        return (
            "Hôm nay chưa có áp lực bắt buộc trong hệ thống. Đây là khoảng trống tốt để "
            "chọn một ưu tiên chủ động thay vì chỉ phản ứng theo deadline."
        )
    if overdue or overdue_followups or due_commitments:
        overdue_names = _task_title_list(overdue)
        if overdue_names:
            return (
                f"Rủi ro lớn nhất là {overdue_names} đã quá hạn. Nên xử lý hoặc chốt lại "
                "cam kết trước khi mở thêm việc mới."
            )
        return (
            "Ngày có rủi ro trễ cam kết. Nên xử lý phần đã quá hạn hoặc liên quan người khác "
            "trước khi mở thêm việc mới."
        )
    if len(due_today) >= 2:
        task_names = _task_title_list(due_today)
        return (
            f"Các deadline hôm nay là {task_names}. Em chưa biết mỗi việc tốn bao lâu, "
            "nên anh chọn thứ tự và chừa buffer nha."
        )
    if len(due_today) == 1:
        task = due_today[0]
        return (
            f"Việc cần chốt hôm nay là “{task.title}”, hạn "
            f"{_format_due(task.due_at, display_timezone)}. Khối lượng hiện vẫn ở mức "
            "kiểm soát được nếu anh bảo vệ thời gian cho việc này."
        )
    if pressure >= 5:
        return (
            "Khối lượng hôm nay tương đối dày. Nên khóa một việc quan trọng trước, rồi mới "
            "chuyển sang meeting và các việc nhỏ."
        )
    return (
        "Khối lượng hôm nay ở mức kiểm soát được. Chọn một kết quả chính và bảo vệ thời gian "
        "để hoàn thành nó."
    )


def _briefing_signals(
    *,
    overdue,
    due_today,
    reminders,
    meetings,
    waiting,
    overdue_followups,
    due_commitments,
    upcoming_top,
    display_timezone: tzinfo,
) -> list[str]:
    signals: list[str] = []
    if overdue:
        signals.append(
            f"- Quá hạn: {_task_title_list(overdue)}."
        )
    if overdue_followups:
        signals.append(f"- {len(overdue_followups)} follow-up đã qua hạn, dễ làm đứt mạch phối hợp.")
    if due_commitments:
        signals.append(f"- {len(due_commitments)} cam kết đến hạn hoặc đã trễ, nên phản hồi sớm.")
    if due_today:
        signals.append(
            f"- Hôm nay: {_task_due_list(due_today, display_timezone)}."
        )
    if upcoming_top:
        signals.append(
            f"- Sắp tới: {_task_due_list(upcoming_top, display_timezone)}."
        )
    if meetings:
        first = min(
            meetings,
            key=lambda item: item.starts_at or datetime.max.replace(tzinfo=UTC),
        )
        signals.append(
            f"- {len(meetings)} meeting; lịch gần nhất là “{first.title}” lúc "
            f"{_format_time(first.starts_at, display_timezone)}."
        )
    if reminders:
        signals.append(f"- {len(reminders)} lời nhắc sẽ đến trong ngày.")
    if waiting:
        signals.append(f"- {len(waiting)} task đang chờ hoặc bị chặn; cần quyết định có thúc đẩy không.")
    return signals


def _task_title_list(tasks) -> str:
    titles = [f"“{task.title}”" for task in tasks]
    if len(titles) <= 1:
        return titles[0] if titles else ""
    return ", ".join(titles[:-1]) + f" và {titles[-1]}"


def _task_due_list(tasks, display_timezone: tzinfo) -> str:
    return "; ".join(
        f"“{task.title}” hạn {_format_due(task.due_at, display_timezone)}"
        for task in tasks
    )


def _briefing_action_lines(scored_tasks, display_timezone: tzinfo) -> list[str]:
    lines: list[str] = []
    for index, (task, _score, reasons) in enumerate(scored_tasks, 1):
        reason = _briefing_reason(reasons)
        due = _format_due(task.due_at, display_timezone)
        lines.append(
            f"{index}. {task.title}{_task_recurrence_badge(task)} — {reason}; hạn {due}."
        )
    return lines


def _briefing_reason(reasons: list[str]) -> str:
    labels = {
        "overdue": "đã quá hạn",
        "due soon": "sắp đến hạn",
        "waiting": "đang chờ",
        "blocked": "đang bị chặn",
        "high priority": "được đặt ưu tiên cao",
        "stale 7d+": "đã lâu chưa tiến triển",
        "has commitment": "có cam kết liên quan",
        "person-linked": "liên quan một người cụ thể",
        "project-linked": "liên quan dự án",
        "nudged": "đã được nhắc nhưng vẫn còn mở",
        "open": "đang mở",
    }
    meaningful = [labels[reason] for reason in reasons if reason in labels]
    return ", ".join(meaningful[:2]) or "đang mở"


def _ambiguous_entity_message(entity_type: str, matches) -> str:
    label = "person" if entity_type == "person" else "project"
    names = [
        match.display_name if entity_type == "person" else match.name
        for match in matches
    ]
    lines = "\n".join(f"{index}. {name}" for index, name in enumerate(names, 1))
    return f"Em thấy nhiều {label} cùng khớp. Anh chọn tên đầy đủ giúp em:\n{lines}"


def _ambiguous_prep_message(people, projects) -> str:
    lines: list[str] = []
    for person in people:
        lines.append(f"- Person: {person.display_name}")
    for project in projects:
        lines.append(f"- Project: {project.name}")
    return "Em thấy nhiều kết quả cùng khớp. Anh chọn tên đầy đủ giúp em:\n" + "\n".join(lines)


def _task_lines(tasks, display_timezone: tzinfo = UTC, note_map: dict | None = None) -> list[str]:
    lines: list[str] = []
    for index, task in enumerate(tasks, 1):
        details = [f"Hạn: {_format_due(task.due_at, display_timezone)}"]
        details.append(f"Trạng thái: {_label_status(task.status)}")
        if task.priority:
            details.append(f"Ưu tiên: {_label_priority(task.priority)}")
        details.append(_evidence("task", task.source_note_id, task.confidence, note_map))
        lines.append(f"{index}. {task.title}{_task_recurrence_badge(task)}")
        lines.append(f"   {' | '.join(details)}")
    return lines


def _agenda_task_lines(tasks, display_timezone: tzinfo = UTC) -> list[str]:
    return [
        f"{index}. {task.title}{_task_recurrence_badge(task)} — hạn {_format_due(task.due_at, display_timezone)}"
        for index, task in enumerate(tasks, 1)
    ]


def _scored_task_lines(scored_tasks, display_timezone: tzinfo = UTC) -> list[str]:
    lines: list[str] = []
    for index, (task, score, reasons) in enumerate(scored_tasks, 1):
        lines.append(f"{index}. {task.title}{_task_recurrence_badge(task)}")
        lines.append(
            f"   Score: {score} | Lý do: {', '.join(reasons)} | Hạn: {_format_due(task.due_at, display_timezone)}"
        )
    return lines


def _task_recurrence_badge(task) -> str:
    labels = {"daily": "Hằng ngày", "weekly": "Hằng tuần"}
    label = labels.get(task.recurrence_rule)
    return f" · 🔁 {label}" if label else ""


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


def _followup_lines(followups, display_timezone: tzinfo, note_map: dict | None = None) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(followups, 1):
        details = [f"Hạn: {_format_due(item.due_at, display_timezone)}"]
        if item.source_note_id:
            details.append(_evidence("follow-up", item.source_note_id, None, note_map))
        lines.append(f"{index}. {item.title}")
        lines.append(f"   {' | '.join(details)}")
    return lines


def _commitment_lines(commitments, display_timezone: tzinfo, note_map: dict | None = None) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(commitments, 1):
        details = [
            f"Chiều: {_label_commitment_direction(item.direction)}",
            f"Hạn: {_format_due(item.due_at, display_timezone)}",
        ]
        if item.source_note_id:
            details.append(_evidence("commitment", item.source_note_id, None, note_map))
        lines.append(f"{index}. {item.title}")
        lines.append(f"   {' | '.join(details)}")
    return lines


def _memory_lines(memories, note_map: dict | None = None) -> list[str]:
    lines = []
    for index, item in enumerate(memories, 1):
        lines.append(f"{index}. [{item.bucket}/{item.kind}] {item.content}")
        lines.append(f"   {_evidence('memory', item.source_note_id, item.confidence, note_map)}")
    return lines


def _meeting_lines(meetings, display_timezone: tzinfo, note_map: dict | None = None) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(meetings, 1):
        starts = _format_due(item.starts_at, display_timezone)
        lines.append(f"{index}. {item.title}")
        lines.append(f"   Bắt đầu: {starts} | {_evidence('meeting', item.source_note_id, None, note_map)}")
    return lines


def _evidence(kind: str, source_note_id: str | None, confidence: float | None, note_map: dict | None) -> str:
    parts = [f"Loại: {kind}"]
    if source_note_id:
        parts.append(f"source: {source_note_id[:8]}")
        note = (note_map or {}).get(source_note_id)
        if note is not None:
            parts.append(f"ngày: {note.created_at:%d/%m/%Y}")
    if confidence is not None:
        parts.append(f"tin cậy: {round(confidence * 100)}%")
    return "Evidence: " + ", ".join(parts)


def _suggested_questions(commitments, followups, tasks) -> list[str]:
    questions = []
    if commitments:
        questions.append("Commitment nào cần chốt trước buổi trao đổi này?")
    if followups:
        questions.append("Có follow-up nào nên hỏi trực tiếp thay vì chờ thêm?")
    waiting = [task for task in tasks if str(task.status) in {"waiting", "blocked"}]
    if waiting:
        questions.append("Task nào đang chờ người này/project này unblock?")
    if not questions:
        questions.append("Có quyết định hoặc next action nào cần ghi lại sau buổi này?")
    return [f"- {question}" for question in questions]


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


def _weekday_label(value: date) -> str:
    labels = {
        0: "Thứ 2",
        1: "Thứ 3",
        2: "Thứ 4",
        3: "Thứ 5",
        4: "Thứ 6",
        5: "Thứ 7",
        6: "Chủ nhật",
    }
    return labels[value.weekday()]


def _is_project_idea_or_low_priority(name: str) -> bool:
    normalized = _normalize_text(name)
    return any(
        token in normalized
        for token in (
            "pet",
            "gacha",
            "todo",
            "shopee",
            "pricing",
            "course materials",
            "syllabus",
            "curriculum",
            "capstone",
            "pbi course",
            "workshop",
            "excel",
            "ai tool",
            "agent training",
        )
    )


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


def _label_commitment_direction(value) -> str:
    labels = {
        "user_owes": "anh nợ người khác",
        "owed_to_user": "người khác nợ anh",
        "mutual": "hai bên",
    }
    return labels.get(str(value), str(value))


def _looks_like_goal(value: str) -> bool:
    normalized = _normalize_text(value)
    return any(token in normalized for token in ("goal", "muc tieu", "objective", "okr", "north star"))


def _normalize_text(value: str) -> str:
    import unicodedata

    lowered = value.lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    ascii_text = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join("".join(char if char.isalnum() else " " for char in ascii_text).split())
