from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta, tzinfo
import re

from memocore.adapters.storage.repositories import (
    ActivityLinkRepository,
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
from memocore.domain.models import EventType, MemoryBucket, MemoryKind, MemoryStatus, Project, ProjectStatus, ProjectType
from memocore.domain.schemas import AssistantAction, AssistantResponse, AssistantSection
from memocore.services.briefing_judgment_service import briefing_assessment, briefing_signals
from memocore.services.event_service import EventService
from memocore.services.presentation_labels import person_note_lines, relationship_label
from memocore.services.project_health_service import ProjectHealthService
from memocore.services.work_state_service import WorkStateService


PEOPLE_PAGE_SIZE = 6


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
        activity_link_repo: ActivityLinkRepository | None = None,
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
        self.activity_link_repo = activity_link_repo
        self.work_state_service = WorkStateService(display_timezone)
        self.project_health_service = ProjectHealthService(display_timezone)

    async def today(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        local_now = now.astimezone(self.display_timezone)
        return await self.agenda_for_date(local_now.date(), "Hôm nay", now=now)

    async def tomorrow(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        local_now = now.astimezone(self.display_timezone)
        return await self.agenda_for_date(
            local_now.date() + timedelta(days=1),
            "Ngày mai",
            now=now,
        )

    async def agenda_for_date(
        self,
        target_date: date,
        title: str | None = None,
        *,
        now: datetime | None = None,
    ) -> str:
        now = now or datetime.now(UTC)
        day_start = datetime.combine(
            target_date, time.min, tzinfo=self.display_timezone
        ).astimezone(UTC)
        day_end = datetime.combine(
            target_date, time.max, tzinfo=self.display_timezone
        ).astimezone(UTC)
        tasks = await self.task_repo.list_active()
        state = self.work_state_service.classify(tasks, now)
        due = [
            task
            for task in tasks
            if task.due_at and day_start <= task.due_at <= day_end
        ]
        if target_date <= now.astimezone(self.display_timezone).date():
            due = state.actionable_today
        waiting = [
            task
            for task in [*state.waiting, *state.blocked]
            if task.due_at and task.due_at <= day_end
        ]
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
        if meetings and due:
            linked_meeting_ids = (
                await self.activity_link_repo.linked_meeting_ids(
                    [task.id for task in due]
                )
                if self.activity_link_repo is not None
                else set()
            )
            meetings = [
                meeting
                for meeting in meetings
                if meeting.id not in linked_meeting_ids
                and not any(_meeting_duplicates_task(meeting, task) for task in due)
            ]
        conflicts = _schedule_conflicts(due, meetings, self.display_timezone)
        heading = title or _day_label(target_date, now.astimezone(self.display_timezone).date()).capitalize()
        lines = [f"{heading} - {_weekday_label(target_date)}, {target_date:%d/%m/%Y}"]
        if target_date == now.astimezone(self.display_timezone).date() and state.next_actions:
            lines.append("")
            lines.append("Ưu tiên nổi bật")
            lines.extend(
                f"{index}. {item.task.title}{_task_recurrence_badge(item.task)} - {item.reason}"
                for index, item in enumerate(state.next_actions[:3], 1)
            )
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
        if conflicts:
            lines.extend(["", "⚠️ Xung đột lịch"])
            lines.extend(f"- {conflict}" for conflict in conflicts)
        if waiting:
            lines.append("")
            lines.append("Đang chờ có hạn")
            lines.extend(_agenda_task_lines(waiting[:3], display_timezone=self.display_timezone))
        if not due and not reminders and not meetings:
            upcoming = sorted(
                [task for task in tasks if task.due_at and task.due_at > day_end],
                key=lambda item: item.due_at,
            )
            if upcoming:
                lines.extend(
                    [
                        "",
                        "Tiếp theo",
                        f"- {upcoming[0].title} · {_format_due(upcoming[0].due_at, self.display_timezone)}",
                    ]
                )
        return "\n".join(lines)

    async def work_dashboard(self, now: datetime | None = None) -> str:
        now = now or datetime.now(UTC)
        tasks = await self.task_repo.list_active()
        state = self.work_state_service.classify(tasks, now)
        commitments = await self.commitment_repo.list_open() if self.commitment_repo else []
        lines = ["Công việc"]
        lines.append(
            f"{state.open_loop_count} task mở · {len(state.overdue)} quá hạn · "
            f"{len(state.waiting) + len(state.blocked)} đang chờ/bị chặn · {len(commitments)} commitment mở."
        )
        next_actions = state.next_actions[:5]
        next_action_ids = _ranked_task_ids(next_actions)
        lines.extend(["", "Nên xử lý tiếp"])
        lines.extend(
            _ranked_task_lines(next_actions, self.display_timezone, state.local_today)
            if next_actions
            else ["Không có việc nào cần ưu tiên ngay."]
        )
        overdue_other = _tasks_excluding(state.overdue, next_action_ids)
        lines.extend(["", "Quá hạn"])
        lines.extend(
            _task_lines(overdue_other[:5], self.display_timezone)
            if overdue_other
            else ["Không có task quá hạn khác."]
        )
        lines.extend(["", "Đang chờ/bị chặn"])
        waiting = [*state.waiting, *state.blocked]
        lines.extend(_task_lines(waiting[:5], self.display_timezone) if waiting else ["Không có task đang chờ hoặc bị chặn."])
        lines.extend(["", "Cam kết"])
        lines.extend(_commitment_lines(commitments[:5], self.display_timezone) if commitments else ["Không có commitment đang mở."])
        lines.extend(["", "Hành động nhanh"])
        lines.append("- Mở Task để đánh dấu xong, đổi hạn hoặc đổi ưu tiên.")
        lines.append("- Mở Đang chờ khi anh muốn follow-up người khác.")
        lines.append("- Dùng /prep <người hoặc dự án> trước cuộc họp.")
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
        active_tasks = await self.task_repo.list_active()
        tasks_by_project = defaultdict(list)
        for task in active_tasks:
            tasks_by_project[task.project_id].append(task)

        if scope:
            normalized_scope = _normalize_text(scope)
            matching_projects = [
                project
                for project in projects
                if normalized_scope in _normalize_text(project.name)
                or (project.summary and normalized_scope in _normalize_text(project.summary))
            ]
            if not matching_projects:
                return f"Projects\nEm chưa thấy project nào liên quan đến {scope}."
            lines = [f"Projects {scope.upper()}"]
            for index, project in enumerate(matching_projects, 1):
                p_tasks = tasks_by_project[project.id]
                task_suffix = f" - {len(p_tasks)} task mở" if len(p_tasks) > 0 else ""
                lines.append(f"{index}. {project.name}{task_suffix}")
            return "\n".join(lines)

        # Build dynamic tree
        non_archived = [p for p in projects if p.status != ProjectStatus.ARCHIVED]
        ideas_or_review = [p for p in non_archived if p.status in (ProjectStatus.INCUBATING, ProjectStatus.REVIEW)]
        active_projects = [p for p in non_archived if p.status not in (ProjectStatus.INCUBATING, ProjectStatus.REVIEW)]

        parent_map = defaultdict(list)
        projects_by_id = {p.id: p for p in active_projects}
        for p in active_projects:
            if p.parent_project_id and p.parent_project_id in projects_by_id:
                parent_map[p.parent_project_id].append(p)

        roots = [p for p in active_projects if not p.parent_project_id or p.parent_project_id not in projects_by_id]
        portfolio_roots = [p for p in roots if p.project_type == ProjectType.PORTFOLIO]
        independent_roots = [p for p in roots if p.project_type != ProjectType.PORTFOLIO]

        def render_project_node(proj: Project, level: int, parent_names: list[str]) -> list[str]:
            clean_name = proj.name
            for p_name in parent_names:
                p_name_lower = p_name.lower()
                if clean_name.lower().startswith(p_name_lower):
                    clean_name = clean_name[len(p_name):].strip()
                    clean_name = clean_name.lstrip("/- :").strip()
            for p_name in parent_names:
                if p_name.lower() == "ste":
                    for extra in ["ste edu", "ste data", "ste ai"]:
                        if clean_name.lower().startswith(extra):
                            clean_name = clean_name[len(extra):].strip()
                            clean_name = clean_name.lstrip("/- :").strip()

            p_tasks = tasks_by_project[proj.id]
            task_suffix = f" ({len(p_tasks)})" if len(p_tasks) > 0 else ""
            indent = "  " * level
            node_lines = [f"{indent}- {clean_name}{task_suffix}"]

            children = parent_map.get(proj.id, [])
            children.sort(key=lambda x: x.name)
            for child in children:
                node_lines.extend(render_project_node(child, level + 1, parent_names + [proj.name]))
            return node_lines

        lines = ["Projects"]

        # 1. Render Portfolios
        portfolio_roots.sort(key=lambda x: x.name)
        for root in portfolio_roots:
            lines.append("")
            lines.append(root.name)
            children = parent_map.get(root.id, [])
            children.sort(key=lambda x: x.name)
            for child in children:
                lines.extend(render_project_node(child, 0, [root.name]))

        # 2. Render Independents
        if independent_roots:
            lines.append("")
            lines.append("Independent")
            independent_roots.sort(key=lambda x: x.name)
            for root in independent_roots:
                lines.extend(render_project_node(root, 0, []))

        # 3. Render Ideas / Needs review
        if ideas_or_review:
            lines.append("")
            lines.append("Ideas / Needs review")
            ideas_or_review.sort(key=lambda x: (x.status.value if x.status else "", x.name))
            for p in ideas_or_review:
                p_tasks = tasks_by_project[p.id]
                task_suffix = f" ({len(p_tasks)})" if len(p_tasks) > 0 else ""
                clean_name = p.name
                if clean_name.lower().startswith("ste"):
                    clean_name = clean_name[3:].strip()
                    clean_name = clean_name.lstrip("/- :").strip()
                status_label = "review" if p.status == ProjectStatus.REVIEW else "incubating"
                lines.append(f"- {clean_name} ({status_label}){task_suffix}")

        return "\n".join(lines)

    async def ordered_task_ids_for_view(
        self,
        source_view: str,
        now: datetime | None = None,
    ) -> list[str]:
        now = now or datetime.now(UTC)
        normalized = source_view.removeprefix("query_")
        tasks = await self.task_repo.list_active()
        if normalized == "briefing":
            state = self.work_state_service.classify(tasks, now)
            return [item.task.id for item in state.next_actions[:3]]
        if normalized in {"today", "todays"}:
            state = self.work_state_service.classify(tasks, now)
            due = state.actionable_today
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
        project_ids = await self._get_descendant_ids(project.id)
        all_projects = {p.id: p for p in await self.project_repo.list_all()}
        project_names = [all_projects[pid].name for pid in project_ids if pid in all_projects]
        tasks = [
            task
            for task in await self.task_repo.list_active()
            if task.project_id in project_ids
            or any(_normalize_text(name) in _normalize_text(task.title) for name in project_names)
            or any(_normalize_text(name) in _normalize_text(task.description) for name in project_names)
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
        state = self.work_state_service.classify(tasks, now)
        overdue = state.overdue
        due_today = state.due_today
        action_items = state.next_actions[:3]
        action_item_ids = _ranked_task_ids(action_items)
        signal_overdue = _tasks_excluding(overdue, action_item_ids)
        signal_due_today = _tasks_excluding(due_today, action_item_ids)
        upcoming_top = [
            task
            for task in state.upcoming
            if task.due_at
            and task.due_at <= now + timedelta(days=1)
            and task.id not in action_item_ids
        ][:3]
        undated_priority = state.unscheduled[:5]
        waiting = [*state.waiting, *state.blocked]
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
            briefing_assessment(
                overdue=overdue,
                due_today=due_today,
                meetings=meetings,
                waiting=waiting,
                overdue_followups=overdue_followups,
                due_commitments=due_commitments,
                action_items=action_items,
                routine_count=len(routines := _tasks_excluding(state.routine_today, action_item_ids)),
                undated_count=len(undated_priority),
                display_timezone=self.display_timezone,
                reference_date=local_now.date(),
            )
        )
        lines.extend(["", "Điểm cần chú ý"])
        signals = briefing_signals(
            overdue=signal_overdue,
            due_today=signal_due_today,
            reminders=reminders,
            meetings=meetings,
            waiting=waiting,
            overdue_followups=overdue_followups,
            due_commitments=due_commitments,
            upcoming_top=upcoming_top,
            display_timezone=self.display_timezone,
            reference_date=local_now.date(),
        )
        lines.extend(signals or ["- Chưa thấy deadline, meeting hay open loop cấp bách hôm nay."])
        if waiting:
            lines.extend(["", "Việc đang chờ"])
            lines.extend(
                f"- {task.title}{_task_recurrence_badge(task)} - {_waiting_action_label(task)}"
                for task in waiting[:3]
            )
        if routines:
            lines.extend(["", "Routine hôm nay"])
            lines.extend(
                f"- {task.title}{_task_recurrence_badge(task)} - giữ nhịp nếu còn năng lượng."
                for task in routines[:3]
            )
        lines.extend(["", "Nên làm tiếp"])
        if action_items:
            lines.extend(_ranked_task_lines(action_items, self.display_timezone, local_now.date()))
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
            return "Nhân sự\nKho nhân sự chưa được cấu hình."
        people = await self.person_repo.list_all()
        if not people:
            return "Nhân sự\nChưa có ai trong dữ liệu."
        visible = people[:PEOPLE_PAGE_SIZE]
        lines = ["Nhân sự"]
        for index, person in enumerate(visible, 1):
            relation = relationship_label(person.relationship)
            lines.append(f"{index}. {person.display_name}")
            if relation:
                lines.append(f"   {relation}")
        if len(people) > len(visible):
            lines.append(
                f"\nĐang hiện {len(visible)}/{len(people)} người. Mở /context và dùng nút Sau để xem tiếp."
            )
        return "\n".join(lines)

    async def people_view(self, page: int = 0) -> AssistantResponse:
        if self.person_repo is None:
            return AssistantResponse(title="Nhân sự", summary="Kho nhân sự chưa được cấu hình.")
        people = await self.person_repo.list_all()
        total_pages = max(1, (len(people) + PEOPLE_PAGE_SIZE - 1) // PEOPLE_PAGE_SIZE)
        page = min(max(page, 0), total_pages - 1)
        start = page * PEOPLE_PAGE_SIZE
        visible = people[start : start + PEOPLE_PAGE_SIZE]
        lines = [
            f"{start + index}. {person.display_name} · {relationship_label(person.relationship)}"
            for index, person in enumerate(visible, 1)
        ] or ["Chưa có ai trong dữ liệu."]
        actions: list[AssistantAction] = []
        for index, person in enumerate(visible):
            actions.append(
                AssistantAction(
                    label=person.display_name[:32],
                    action_id=f"nav:context:person:{person.id}",
                    row=index,
                )
            )
        nav_row = len(visible)
        if page > 0:
            actions.append(
                AssistantAction(
                    label="‹ Trước",
                    action_id=f"nav:context:people:{page - 1}",
                    row=nav_row,
                )
            )
        if page + 1 < total_pages:
            actions.append(
                AssistantAction(
                    label="Sau ›",
                    action_id=f"nav:context:people:{page + 1}",
                    row=nav_row,
                )
            )
        actions.append(
            AssistantAction(label="Quay lại", action_id="nav:context", row=nav_row + 1)
        )
        return AssistantResponse(
            title="Nhân sự",
            summary=f"{len(people)} người · trang {page + 1}/{total_pages}",
            sections=[AssistantSection(lines=lines)],
            actions=actions,
        )

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
            return "Hồ sơ nhân sự\nKho nhân sự chưa được cấu hình."
        matches = await self.person_repo.find_matches(query)
        if not matches:
            return f"Hồ sơ · {query}\nEm chưa thấy người này trong dữ liệu."
        if len(matches) > 1:
            return _ambiguous_entity_message("person", matches)
        return await self._render_person_context(matches[0])

    async def person_context_by_id(self, person_id: str) -> str:
        if self.person_repo is None:
            return "Hồ sơ nhân sự\nKho nhân sự chưa được cấu hình."
        person = await self.person_repo.get_by_id(person_id)
        if person is None:
            return "Hồ sơ nhân sự\nNgười này không còn trong dữ liệu."
        return await self._render_person_context(person)

    async def _render_person_context(self, person) -> str:
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
        linked_meeting_ids = (
            await self.activity_link_repo.linked_meeting_ids([task.id for task in tasks])
            if self.activity_link_repo is not None
            else set()
        )
        meetings = [
            meeting
            for meeting in meetings
            if meeting.id not in linked_meeting_ids
            and not any(_meeting_duplicates_task(meeting, task) for task in tasks)
        ]

        lines = [f"Hồ sơ · {person.display_name}"]
        if person.relationship:
            lines.append(f"Quan hệ: {relationship_label(person.relationship)}")
        if person.notes:
            lines.extend(f"- {line}" for line in person_note_lines(person.notes))
        lines.extend(
            [
                "",
                (
                    f"Tổng quan: {len(tasks)} task · {len(commitments)} cam kết · "
                    f"{len(followups)} follow-up · {len(meetings)} lịch riêng · "
                    f"{len(memories)} ghi nhớ."
                ),
            ]
        )
        if tasks:
            lines.extend(["", "Việc đang mở"])
            lines.extend(_task_lines(tasks, self.display_timezone))
        if commitments:
            lines.extend(["", "Cam kết"])
            lines.extend(_commitment_lines(commitments, self.display_timezone))
        if followups:
            lines.extend(["", "Follow-up"])
            lines.extend(_followup_lines(followups, self.display_timezone))
        if meetings:
            lines.extend(["", "Lịch riêng gần đây/sắp tới"])
            lines.extend(_meeting_lines(meetings[:5], self.display_timezone))
        if memories:
            lines.extend(["", "Ghi nhớ liên quan"])
            lines.extend(_memory_lines(memories[:8]))
        if not any((tasks, commitments, followups, meetings, memories)):
            lines.extend(["", "Chưa có việc, cam kết hoặc lịch đang mở với người này."])
        return "\n".join(lines)

    async def project_context(self, query: str) -> str:
        matches = await self.project_repo.find_matches(query)
        if not matches:
            return f"Project {query}\nEm chưa thấy project này trong dữ liệu."
        if len(matches) > 1:
            return _ambiguous_entity_message("project", matches)
        project = matches[0]
        project_ids = await self._get_descendant_ids(project.id)
        tasks = []
        followups = []
        memories = []
        commitments = []
        meetings = []
        for pid in project_ids:
            tasks.extend(await self.task_repo.list_active_by_project(pid))
            followups.extend(await self.followup_repo.list_open_by_project(pid))
            memories.extend(await self.memory_repo.list_active_by_project(pid))
            if self.commitment_repo is not None:
                commitments.extend(await self.commitment_repo.list_open_by_project(pid))
            if self.meeting_repo is not None:
                meetings.extend(await self.meeting_repo.list_by_project(pid))
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
        lines.append("Project health")
        lines.extend(
            self.project_health_service.health_lines(
                project=project,
                tasks=tasks,
                commitments=commitments,
                followups=followups,
                memories=memories,
                now=datetime.now(UTC),
            )
        )
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
            project_ids = await self._get_descendant_ids(project.id)
            tasks = []
            followups = []
            memories = []
            commitments = []
            meetings = []
            for pid in project_ids:
                tasks.extend(await self.task_repo.list_active_by_project(pid))
                followups.extend(await self.followup_repo.list_open_by_project(pid))
                memories.extend(await self.memory_repo.list_active_by_project(pid))
                if self.commitment_repo is not None:
                    commitments.extend(await self.commitment_repo.list_open_by_project(pid))
                if self.meeting_repo is not None:
                    meetings.extend(await self.meeting_repo.list_by_project(pid))
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

    async def _get_descendant_ids(self, project_id: str) -> list[str]:
        descendant_ids = [project_id]
        queue = [project_id]
        while queue:
            curr_id = queue.pop(0)
            children = await self.project_repo.list_children(curr_id)
            for child in children:
                if child.id not in descendant_ids:
                    descendant_ids.append(child.id)
                    queue.append(child.id)
        return descendant_ids

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
        children_by_parent: dict[str, list] = defaultdict(list)
        for project in projects:
            if project.parent_project_id:
                children_by_parent[project.parent_project_id].append(project)
        active_container_ids = {
            parent_id
            for parent_id, children in children_by_parent.items()
            if any(str(child.status) == ProjectStatus.ACTIVE.value for child in children)
        }
        task_project_ids = {task.project_id for task in active if task.project_id}
        projects_without_next_action = [
            project
            for project in projects
            if str(project.status) == ProjectStatus.ACTIVE.value
            and project.project_type
            in {
                ProjectType.PRODUCT,
                ProjectType.INITIATIVE,
                ProjectType.CLIENT_PROJECT,
                ProjectType.INDEPENDENT_PROJECT,
            }
            and project.id not in active_container_ids
            and not _project_has_task_in_tree(project.id, children_by_parent, task_project_ids)
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
        predeadline_warning_hours: int = 0,
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
            elif (
                predeadline_warning_hours > 0
                and task.due_at
                and task.due_at <= now + timedelta(hours=predeadline_warning_hours)
            ):
                nudges.append(
                    (
                        "task_deadline_warning",
                        task.id,
                        f"Task sắp đến hạn: {task.title}\nHạn: {_format_due(task.due_at, self.display_timezone)}",
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


def _briefing_action_lines(
    scored_tasks, display_timezone: tzinfo, reference_date=None
) -> list[str]:
    lines: list[str] = []
    for index, (task, _score, reasons) in enumerate(scored_tasks, 1):
        reason = _briefing_reason(reasons)
        due = _format_due(task.due_at, display_timezone, reference_date)
        lines.append(
            f"{index}. {task.title}{_task_recurrence_badge(task)} — {reason}; hạn {due}."
        )
    return lines


def _ranked_task_lines(
    ranked_tasks, display_timezone: tzinfo, reference_date=None
) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(ranked_tasks, 1):
        task = item.task
        due = _format_due(task.due_at, display_timezone, reference_date)
        lines.append(
            f"{index}. {task.title}{_task_recurrence_badge(task)} - {item.reason}; hạn {due}."
        )
    return lines


def _ranked_task_ids(ranked_tasks) -> set[str]:
    return {
        item.task.id
        for item in ranked_tasks
        if getattr(getattr(item, "task", None), "id", None)
    }


def _tasks_excluding(tasks, excluded_ids: set[str]) -> list:
    return [task for task in tasks if getattr(task, "id", None) not in excluded_ids]


def _waiting_action_label(task) -> str:
    if str(task.status) == "blocked":
        return "cần quyết định cách gỡ chặn, chưa nên xem là việc làm ngay"
    return "đang chờ người khác, chỉ cần follow-up nếu đã đến hạn"


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
    label = "người" if entity_type == "person" else "dự án"
    names = [
        match.display_name if entity_type == "person" else match.name
        for match in matches
    ]
    lines = "\n".join(f"{index}. {name}" for index, name in enumerate(names, 1))
    return f"Em thấy nhiều {label} cùng khớp. Anh chọn tên đầy đủ giúp em:\n{lines}"


def _ambiguous_prep_message(people, projects) -> str:
    lines: list[str] = []
    for person in people:
        lines.append(f"- Người: {person.display_name}")
    for project in projects:
        lines.append(f"- Dự án: {project.name}")
    return "Em thấy nhiều kết quả cùng khớp. Anh chọn tên đầy đủ giúp em:\n" + "\n".join(lines)


def _task_lines(tasks, display_timezone: tzinfo = UTC, note_map: dict | None = None) -> list[str]:
    lines: list[str] = []
    for index, task in enumerate(tasks, 1):
        details = [f"Hạn: {_format_due(task.due_at, display_timezone)}"]
        details.append(f"Trạng thái: {_label_status(task.status)}")
        if task.priority:
            details.append(f"Ưu tiên: {_label_priority(task.priority)}")
        lines.append(f"{index}. {task.title}{_task_recurrence_badge(task)}")
        lines.append(f"   {' | '.join(details)}")
    return lines


def _agenda_task_lines(tasks, display_timezone: tzinfo = UTC) -> list[str]:
    return [
        f"{index}. {_format_due(task.due_at, display_timezone)} · {task.title}{_task_recurrence_badge(task)}"
        for index, task in enumerate(tasks, 1)
    ]


def _scored_task_lines(scored_tasks, display_timezone: tzinfo = UTC) -> list[str]:
    lines: list[str] = []
    for index, (task, _score, reasons) in enumerate(scored_tasks, 1):
        lines.append(f"{index}. {task.title}{_task_recurrence_badge(task)}")
        lines.append(
            f"   Lý do: {_briefing_reason(reasons)} | Hạn: {_format_due(task.due_at, display_timezone)}"
        )
    return lines


def _task_recurrence_badge(task) -> str:
    label = _recurrence_label(task.recurrence_rule)
    return f" · 🔁 {label}" if label else ""


def _recurrence_label(rule: str | None) -> str | None:
    if rule is None:
        return None
    labels = {"daily": "Hằng ngày", "weekly": "Hằng tuần"}
    if rule in labels:
        return labels[rule]
    if rule.startswith("weekly:"):
        return "Hằng tuần"
    match = re.fullmatch(r"interval:(\d+)([dw])", rule)
    if match:
        count = int(match.group(1))
        unit = "ngày" if match.group(2) == "d" else "tuần"
        return f"Mỗi {count} {unit}"
    return None


def _format_time(value: datetime | None, display_timezone: tzinfo) -> str:
    if value is None:
        return "chưa rõ giờ"
    return value.astimezone(display_timezone).strftime("%H:%M")


def _format_due(
    value: datetime | None, display_timezone: tzinfo, reference_date=None
) -> str:
    if value is None:
        return "chưa có hạn"
    local_value = value.astimezone(display_timezone)
    today = reference_date or datetime.now(UTC).astimezone(display_timezone).date()
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
        lines.append(f"{index}. {item.title}")
        lines.append(f"   {' | '.join(details)}")
    return lines


def _memory_lines(memories, note_map: dict | None = None) -> list[str]:
    lines = []
    for index, item in enumerate(memories, 1):
        lines.append(f"{index}. [{item.bucket}/{item.kind}] {item.content}")
    return lines


def _meeting_lines(meetings, display_timezone: tzinfo, note_map: dict | None = None) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(meetings, 1):
        starts = _format_due(item.starts_at, display_timezone)
        lines.append(f"{index}. {item.title}")
        lines.append(f"   Bắt đầu: {starts}")
    return lines


def _schedule_conflicts(tasks, meetings, display_timezone: tzinfo) -> list[str]:
    intervals: list[tuple[str, datetime, datetime]] = []
    for task in tasks:
        if task.due_at is None:
            continue
        end = task.due_at + timedelta(minutes=task.duration_minutes or 30)
        intervals.append((task.title, task.due_at, end))
    for meeting in meetings:
        if meeting.starts_at is None:
            continue
        end = meeting.ends_at or meeting.starts_at + timedelta(minutes=60)
        intervals.append((meeting.title, meeting.starts_at, end))
    intervals.sort(key=lambda item: item[1])
    conflicts: list[str] = []
    for index, current in enumerate(intervals):
        for other in intervals[index + 1 :]:
            if other[1] >= current[2]:
                break
            start = max(current[1], other[1]).astimezone(display_timezone)
            conflicts.append(
                f"{start:%H:%M}: “{current[0]}” trùng với “{other[0]}”"
            )
            if len(conflicts) >= 3:
                return conflicts
    return conflicts


def _meeting_duplicates_task(meeting, task) -> bool:
    if meeting.starts_at is None or task.due_at is None:
        return False
    if abs((meeting.starts_at - task.due_at).total_seconds()) > 60:
        return False
    if meeting.person_id and task.person_id and meeting.person_id != task.person_id:
        return False
    return meeting.source_note_id == task.source_note_id


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


def _project_has_task_in_tree(
    project_id: str,
    children_by_parent: dict[str, list],
    task_project_ids: set[str],
) -> bool:
    if project_id in task_project_ids:
        return True
    return any(
        _project_has_task_in_tree(child.id, children_by_parent, task_project_ids)
        for child in children_by_parent.get(project_id, [])
    )


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
