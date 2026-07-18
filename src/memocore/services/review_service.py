from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from memocore.adapters.storage.repositories import (
    ClarificationRequestRepository,
    CommitmentRepository,
    FollowUpRepository,
    MemoryItemRepository,
    ProjectRepository,
    TaskRepository,
)
from memocore.domain.models import EventType, FeedbackSignal, FeedbackStatus, MemoryStatus
from memocore.domain.schemas import AssistantAction, AssistantResponse, AssistantSection
from memocore.services.event_service import EventService
from memocore.services.backup_service import BackupService

UNDOABLE_EVENT_TYPES = {
    EventType.WORK_ITEM_CHANGED,
    EventType.TASK_BATCH_COMPLETED,
    EventType.TASK_DONE,
    EventType.FOLLOWUP_DONE,
    EventType.COMMITMENT_DONE,
    EventType.DAILY_CLOSEOUT_APPLIED,
}
ACTIONABLE_PROJECT_TYPES = {
    "product",
    "initiative",
    "client_project",
    "independent_project",
}
QUALITY_ATTENTION_EVENT_TYPES = {
    EventType.CLARIFICATION_FAILED,
    EventType.BACKUP_FAILED,
    EventType.MODEL_OUTPUT_INVALID,
    EventType.EXTRACTION_LIKELY_INCOMPLETE,
    EventType.NOTE_FAILED,
    EventType.REMINDER_FAILED,
}


@dataclass(frozen=True)
class ProjectHealthItem:
    project: object
    reasons: list[str]


@dataclass(frozen=True)
class ProjectHealthReview:
    priority: list[ProjectHealthItem]
    backlog: list[ProjectHealthItem]

    @property
    def total(self) -> int:
        return len(self.priority) + len(self.backlog)


class ReviewService:
    """One small inbox for uncertain or unfinished system state."""

    def __init__(
        self,
        memory_repo: MemoryItemRepository,
        task_repo: TaskRepository,
        clarification_repo: ClarificationRequestRepository,
        event_service: EventService,
        project_repo: ProjectRepository | None = None,
        commitment_repo: CommitmentRepository | None = None,
        followup_repo: FollowUpRepository | None = None,
        backup_service: BackupService | None = None,
    ):
        self.memory_repo = memory_repo
        self.task_repo = task_repo
        self.clarification_repo = clarification_repo
        self.event_service = event_service
        self.project_repo = project_repo
        self.commitment_repo = commitment_repo
        self.followup_repo = followup_repo
        self.backup_service = backup_service

    async def overview(self) -> AssistantResponse:
        memories = await self.memory_repo.list_all()
        tasks = await self.task_repo.list_active()
        project_review = await self._project_health_review(tasks)
        commitment_review = await self._commitment_hygiene_items()
        pending = await self.clarification_repo.list_pending()
        since = datetime.now(UTC) - timedelta(days=30)
        duplicates = await self.event_service.list_recent(
            EventType.MEMORY_DUPLICATE_SUGGESTED, since=since, limit=100
        )
        alias_suggestions = await self.event_service.list_recent(
            EventType.ENTITY_ALIAS_SUGGESTED, since=since, limit=100
        )
        alias_resolved = await self._resolved_alias_suggestion_ids(since)
        unresolved_aliases = [
            event for event in alias_suggestions if event.id not in alias_resolved
        ]
        review_memories = [
            item
            for item in memories
            if str(item.status) == MemoryStatus.CANDIDATE.value
            or item.conflict_state == "conflict"
        ]
        undated_tasks = [task for task in tasks if task.due_at is None]
        failed_clarifications = await self.event_service.list_recent(
            EventType.CLARIFICATION_FAILED, since=since, limit=100
        )
        system_failures = await self.event_service.list_recent(
            EventType.BACKUP_FAILED, since=since, limit=100
        )
        recent_undo = await self._recent_undoable_events(limit=5)
        feedback_events = await self.event_service.list_recent(
            EventType.USER_FEEDBACK_RECORDED, since=since, limit=100
        )
        feedback = [
            event
            for event in feedback_events
            if event.payload.get("schema_version") == 1
            and event.payload.get("signal") in {signal.value for signal in FeedbackSignal}
        ]
        resolved_feedback_ids = await self._resolved_feedback_ids(since)
        open_feedback = [
            event
            for event in feedback
            if event.payload.get("status") == FeedbackStatus.OPEN.value
            and event.id not in resolved_feedback_ids
        ]
        feedback_counts = Counter(event.payload["signal"] for event in feedback)
        decision_count = (
            len(review_memories)
            + len(unresolved_aliases)
            + len(pending)
            + len(open_feedback)
            + len(system_failures)
        )

        return AssistantResponse(
            title="Cần xem lại",
            summary=(
                f"{decision_count} mục cần quyết định: {len(review_memories)} ghi nhớ, "
                f"{len(unresolved_aliases)} liên kết tên, {len(pending)} câu hỏi, "
                f"{len(open_feedback)} phản hồi và {len(system_failures)} cảnh báo hệ thống."
            ),
            sections=[
                AssistantSection(
                    heading="Công việc nên rà sau",
                    lines=[
                        f"{len(undated_tasks)} task chưa có hạn",
                        f"{len(commitment_review)} commitment thiếu hạn/ngữ cảnh",
                        f"{len(project_review.priority)} project cần chọn next action trước",
                        f"{len(project_review.backlog)} project khác trong backlog hygiene",
                    ],
                ),
                AssistantSection(
                    heading="Chất lượng 30 ngày",
                    lines=[
                        f"Gợi ý trùng: {len(duplicates)}",
                        f"Clarification chưa giải quyết được: {len(failed_clarifications)}",
                        f"Gần đây có thể hoàn tác: {len(recent_undo)}",
                        f"Project health backlog: {project_review.total}",
                        f"Cảnh báo hệ thống: {len(system_failures)}",
                        (
                            "Phản hồi: "
                            f"{feedback_counts[FeedbackSignal.ACCEPTED.value]} chấp nhận · "
                            f"{feedback_counts[FeedbackSignal.EDITED.value]} chỉnh sửa · "
                            f"{feedback_counts[FeedbackSignal.REJECTED.value]} từ chối · "
                            f"{feedback_counts[FeedbackSignal.IGNORED.value]} bỏ qua · "
                            f"{feedback_counts[FeedbackSignal.CORRECTION.value]} sửa sai"
                        ),
                    ],
                )
            ],
            footer="Các mục cần quyết định được ưu tiên trước; metric 30 ngày chỉ để soi xu hướng.",
            actions=[
                AssistantAction(label="🧠 Ghi nhớ", action_id="mem:t:review:0", row=0),
                AssistantAction(label="👤 Tên người", action_id="nav:review:people", row=0),
                AssistantAction(label="📁 Tên dự án", action_id="nav:review:projects", row=1),
                AssistantAction(label="❓ Đang chờ", action_id="nav:review:clarifications", row=1),
                AssistantAction(label="💬 Phản hồi", action_id="nav:review:feedback", row=2),
                AssistantAction(label="⚙️ Hệ thống", action_id="nav:review:system", row=2),
                AssistantAction(label="↩ Gần đây", action_id="nav:review:recent", row=3),
                AssistantAction(label="📍 Project health", action_id="nav:review:project-health", row=3),
                AssistantAction(label="🤝 Cam kết", action_id="nav:review:commitments", row=4),
                AssistantAction(label="📋 Task", action_id="nav:work:tasks", row=4),
                AssistantAction(label="📈 Chất lượng", action_id="nav:review:quality", row=5),
            ],
        )

    async def recent_operations(self) -> AssistantResponse:
        events = await self._recent_undoable_events(limit=5)
        lines = [
            f"{index}. {_undoable_event_label(event.event_type)} · {_format_event_time(event.created_at)}"
            for index, event in enumerate(events, 1)
        ] or ["Không có thao tác gần đây nào còn có thể hoàn tác."]
        actions = [
            AssistantAction(
                label=f"↩ Hoàn tác {index}",
                action_id=f"work:u:e:{event.id}",
                row=index - 1,
            )
            for index, event in enumerate(events, 1)
        ]
        actions.append(AssistantAction(label="Quay lại", action_id="nav:review", row=6))
        return AssistantResponse(
            title="Gần đây có thể hoàn tác",
            summary=f"{len(events)} thao tác còn trong vùng an toàn để undo.",
            sections=[AssistantSection(lines=lines)],
            footer="MemoCore chỉ khôi phục mục chưa bị sửa tiếp sau thao tác gốc.",
            actions=actions,
        )

    async def clarifications(self) -> AssistantResponse:
        pending = await self.clarification_repo.list_pending()
        lines = [request.question for request in pending[:8]] or [
            "Không có câu hỏi làm rõ nào đang chờ."
        ]
        return AssistantResponse(
            title="Clarification đang chờ",
            sections=[AssistantSection(lines=lines)],
            footer=(
                "Trả lời trực tiếp câu hỏi gần nhất trong chat, hoặc gửi một command mới để hủy ngữ cảnh cũ."
                if pending
                else None
            ),
            actions=[AssistantAction(label="Quay lại", action_id="nav:review", row=0)],
        )

    async def feedback(self) -> AssistantResponse:
        since = datetime.now(UTC) - timedelta(days=30)
        events = await self.event_service.list_recent(
            EventType.USER_FEEDBACK_RECORDED,
            since=since,
            limit=100,
        )
        resolved_ids = await self._resolved_feedback_ids(since)
        open_events = [
            event
            for event in events
            if event.payload.get("schema_version") == 1
            and event.payload.get("status") == FeedbackStatus.OPEN.value
            and event.id not in resolved_ids
        ]
        visible = open_events[:8]
        lines = [
            (
                f"{index}. {_feedback_signal_label(event.payload.get('signal'))} · "
                f"{_artifact_label(event.entity_type)}"
            )
            for index, event in enumerate(visible, 1)
        ] or ["Không có phản hồi nào đang chờ xử lý."]
        actions = [
            AssistantAction(
                label=f"Đã xử lý {index}",
                action_id=f"nav:rf:{event.id}",
                row=index - 1,
            )
            for index, event in enumerate(visible, 1)
        ]
        actions.append(AssistantAction(label="Quay lại", action_id="nav:review", row=8))
        return AssistantResponse(
            title="Phản hồi cần xử lý",
            summary=f"{len(open_events)} mục đang mở trong 30 ngày gần nhất.",
            sections=[AssistantSection(lines=lines)],
            footer=(
                "Danh sách chỉ hiện loại phản hồi và loại dữ liệu; nội dung riêng tư không xuất hiện trong màn hình chất lượng."
            ),
            actions=actions,
        )

    async def system(self) -> AssistantResponse:
        since = datetime.now(UTC) - timedelta(days=30)
        backup_failures = await self.event_service.list_recent(
            EventType.BACKUP_FAILED,
            since=since,
            limit=20,
        )
        restore_drills = await self.event_service.list_recent(
            EventType.RESTORE_DRILL_COMPLETED,
            since=since,
            limit=20,
        )
        lines: list[str] = []
        recovery_report_failure = False
        if self.backup_service is not None:
            report_state, report = self.backup_service.read_restore_report()
            if report_state == "invalid":
                recovery_report_failure = True
                lines.append("Recovery journal không đọc được · cần chạy doctor")
            elif report and report.get("status") != "passed":
                recovery_report_failure = True
                lines.append(
                    "Recovery "
                    f"{report.get('status')} · {report.get('phase')} · "
                    f"{report.get('failure_code', 'unknown')} · "
                    f"{report.get('recovery_code') or report.get('recovery_phase', 'not_completed')}"
                )
        if backup_failures:
            for index, event in enumerate(backup_failures[:8], 1):
                operation = event.payload.get("operation", "backup")
                code = event.payload.get("failure_code", "unknown")
                rollback = event.payload.get("rollback")
                suffix = f" · rollback {rollback}" if rollback else ""
                lines.append(
                    f"{index}. Backup lỗi · {event.created_at.date().isoformat()} · "
                    f"{operation}/{code}{suffix}"
                )
        elif not recovery_report_failure:
            lines.append("Không có lỗi backup nào trong 30 ngày gần nhất.")
        lines.append(f"Restore drill đã ghi nhận: {len(restore_drills)}")
        return AssistantResponse(
            title="Hệ thống cần xem lại",
            summary=(
                f"{len(backup_failures) + int(recovery_report_failure)} "
                "cảnh báo backup/recovery trong 30 ngày gần nhất."
            ),
            sections=[AssistantSection(lines=lines)],
            footer=(
                "Màn hình này chỉ hiện trạng thái vận hành; không hiển thị nội dung note, task hay memory."
            ),
            actions=[AssistantAction(label="Quay lại", action_id="nav:review", row=0)],
        )

    async def project_health(self) -> AssistantResponse:
        tasks = await self.task_repo.list_active()
        review = await self._project_health_review(tasks)
        visible_priority = review.priority[:5]
        visible_backlog = review.backlog[:5] if not visible_priority else []
        lines: list[str] = []
        if visible_priority:
            lines.append("Cần quyết định trước")
            lines.extend(
                f"{index}. {item.project.name} · {_project_health_reason_label(item)}"
                for index, item in enumerate(visible_priority, 1)
            )
            remaining_priority = len(review.priority) - len(visible_priority)
            if remaining_priority > 0:
                lines.append(f"- Còn {remaining_priority} project cần quyết định khác.")
        if visible_backlog:
            lines.append("Backlog hygiene")
            lines.extend(
                f"{index}. {item.project.name} · {_project_health_reason_label(item)}"
                for index, item in enumerate(visible_backlog, 1)
            )
            remaining_backlog = len(review.backlog) - len(visible_backlog)
            if remaining_backlog > 0:
                lines.append(f"- Còn {remaining_backlog} project backlog khác.")
        elif review.backlog and visible_priority:
            lines.append(f"Backlog hygiene: còn {len(review.backlog)} project khác.")
        if not lines:
            lines = ["Không có project active nào thiếu next action."]
        return AssistantResponse(
            title="Project health",
            summary=(
                f"{len(review.priority)} project cần quyết định trước · "
                f"{len(review.backlog)} project khác trong backlog hygiene."
            ),
            sections=[AssistantSection(lines=lines)],
            footer=(
                "Mở /project <tên> để xem chi tiết; backlog hygiene không phải inbox quyết định ngay."
            ),
            actions=[AssistantAction(label="Quay lại", action_id="nav:review", row=0)],
        )

    async def commitments(self) -> AssistantResponse:
        items = await self._commitment_hygiene_items()
        visible = items[:8]
        lines = [
            f"{index}. {item.title} · {', '.join(_commitment_hygiene_reasons(item))}"
            for index, item in enumerate(visible, 1)
        ] or ["Không có commitment mở nào thiếu hạn hoặc ngữ cảnh."]
        remaining = len(items) - len(visible)
        if remaining > 0:
            lines.append(f"- Còn {remaining} commitment khác nên rà sau.")
        return AssistantResponse(
            title="Commitment cần rà",
            summary=f"{len(items)} commitment cần bổ sung metadata.",
            sections=[AssistantSection(lines=lines)],
            footer=(
                "Màn này chỉ phát hiện commitment thiếu thông tin; vào /work để hoàn thành, dời hạn hoặc hủy."
            ),
            actions=[
                AssistantAction(label="Mở cam kết", action_id="nav:work:commitments", row=0),
                AssistantAction(label="Quay lại", action_id="nav:review", row=1),
            ],
        )

    async def quality_report(self) -> AssistantResponse:
        now = datetime.now(UTC)
        current_since = now - timedelta(days=7)
        previous_since = now - timedelta(days=14)
        events = await self.event_service.list_recent(since=previous_since, limit=500)
        current_events = [event for event in events if event.created_at >= current_since]
        previous_events = [event for event in events if event.created_at < current_since]
        current_attention = _attention_signal_count(current_events)
        previous_attention = _attention_signal_count(previous_events)
        current_feedback = [
            event
            for event in current_events
            if event.event_type == EventType.USER_FEEDBACK_RECORDED
            and event.payload.get("schema_version") == 1
        ]
        feedback_counts = Counter(
            event.payload.get("signal")
            for event in current_feedback
            if event.payload.get("signal") in {signal.value for signal in FeedbackSignal}
        )
        pending = await self.clarification_repo.list_pending()
        open_feedback = await self._open_feedback_events(now - timedelta(days=30))
        unresolved_aliases = await self._unresolved_alias_events(now - timedelta(days=30))
        system_failures = await self.event_service.list_recent(
            EventType.BACKUP_FAILED,
            since=now - timedelta(days=30),
            limit=100,
        )
        recent_undo = await self._recent_undoable_events(limit=5)
        return AssistantResponse(
            title="Báo cáo chất lượng",
            summary=(
                f"7 ngày qua có {current_attention} tín hiệu cần chú ý "
                f"({_trend_label(current_attention, previous_attention)} so với 7 ngày trước)."
            ),
            sections=[
                AssistantSection(
                    heading="7 ngày gần nhất",
                    lines=[
                        f"Phản hồi đã ghi nhận: {len(current_feedback)}",
                        (
                            "Sửa/chê/sai: "
                            f"{feedback_counts[FeedbackSignal.EDITED.value]} chỉnh · "
                            f"{feedback_counts[FeedbackSignal.REJECTED.value]} từ chối · "
                            f"{feedback_counts[FeedbackSignal.CORRECTION.value]} sửa sai"
                        ),
                        f"Clarification fail: {_event_count(current_events, EventType.CLARIFICATION_FAILED)}",
                        f"Lỗi backup/runtime: {_event_count(current_events, EventType.BACKUP_FAILED)}",
                        (
                            "Model/extraction cần soi: "
                            f"{_event_count(current_events, EventType.MODEL_OUTPUT_INVALID) + _event_count(current_events, EventType.EXTRACTION_LIKELY_INCOMPLETE)}"
                        ),
                    ],
                ),
                AssistantSection(
                    heading="Đang mở",
                    lines=[
                        f"{len(open_feedback)} phản hồi cần xử lý",
                        f"{len(pending)} clarification đang chờ",
                        f"{len(unresolved_aliases)} liên kết tên chưa chốt",
                        f"{len(system_failures)} cảnh báo hệ thống trong 30 ngày",
                        f"{len(recent_undo)} thao tác còn có thể undo",
                    ],
                ),
            ],
            footer=(
                "Báo cáo này chỉ dùng số đếm và loại tín hiệu; nội dung riêng tư nằm ở các màn xử lý tương ứng."
            ),
            actions=[
                AssistantAction(label="Phản hồi", action_id="nav:review:feedback", row=0),
                AssistantAction(label="Đang chờ", action_id="nav:review:clarifications", row=0),
                AssistantAction(label="Hệ thống", action_id="nav:review:system", row=1),
                AssistantAction(label="Quay lại", action_id="nav:review", row=2),
            ],
        )

    async def resolve_feedback(self, event_id: str) -> AssistantResponse | None:
        resolved = await self.event_service.resolve_feedback(event_id)
        if resolved is None:
            return None
        response = await self.feedback()
        return AssistantResponse(
            title="Đã đánh dấu xử lý",
            summary=response.summary,
            sections=response.sections,
            footer=response.footer,
            actions=response.actions,
        )

    async def _resolved_alias_suggestion_ids(self, since: datetime) -> set[str]:
        resolved: set[str] = set()
        for event_type in (
            EventType.ENTITY_ALIAS_CONFIRMED,
            EventType.ENTITY_ALIAS_REJECTED,
            EventType.ENTITY_ALIAS_IGNORED,
        ):
            for event in await self.event_service.list_recent(
                event_type,
                since=since,
                limit=200,
            ):
                suggestion_id = event.payload.get("suggestion_event_id")
                if suggestion_id:
                    resolved.add(suggestion_id)
        return resolved

    async def _resolved_feedback_ids(self, since: datetime) -> set[str]:
        return {
            event.payload["feedback_event_id"]
            for event in await self.event_service.list_recent(
                EventType.USER_FEEDBACK_RESOLVED,
                since=since,
                limit=200,
            )
            if event.payload.get("feedback_event_id")
        }

    async def _open_feedback_events(self, since: datetime) -> list:
        events = await self.event_service.list_recent(
            EventType.USER_FEEDBACK_RECORDED,
            since=since,
            limit=100,
        )
        resolved_ids = await self._resolved_feedback_ids(since)
        return [
            event
            for event in events
            if event.payload.get("schema_version") == 1
            and event.payload.get("status") == FeedbackStatus.OPEN.value
            and event.id not in resolved_ids
        ]

    async def _unresolved_alias_events(self, since: datetime) -> list:
        alias_suggestions = await self.event_service.list_recent(
            EventType.ENTITY_ALIAS_SUGGESTED, since=since, limit=100
        )
        alias_resolved = await self._resolved_alias_suggestion_ids(since)
        return [event for event in alias_suggestions if event.id not in alias_resolved]

    async def _commitment_hygiene_items(self) -> list:
        if self.commitment_repo is None:
            return []
        commitments = await self.commitment_repo.list_open()
        return [
            item
            for item in commitments
            if item.due_at is None or (item.person_id is None and item.project_id is None)
        ]

    async def _project_health_review(self, tasks) -> ProjectHealthReview:
        projects = await self._project_health_candidates(tasks)
        recent_cutoff = datetime.now(UTC) - timedelta(days=30)
        priority = [
            item
            for item in projects
            if _has_immediate_project_risk(item)
            or max(
                _sort_datetime(item.project.updated_at),
                _sort_datetime(item.project.last_seen_at),
            )
            >= recent_cutoff
        ]
        backlog = [item for item in projects if item not in priority]
        return ProjectHealthReview(priority=priority, backlog=backlog)

    async def _project_health_candidates(self, tasks) -> list[ProjectHealthItem]:
        if self.project_repo is None:
            return []
        projects, children_by_parent = await self._actionable_leaf_projects()
        project_by_id = {project.id: project for project in projects}
        task_project_ids = {task.project_id for task in tasks if task.project_id}
        now = datetime.now(UTC)

        def has_task_in_tree(project_id: str) -> bool:
            if project_id in task_project_ids:
                return True
            return any(has_task_in_tree(child.id) for child in children_by_parent.get(project_id, []))

        reasons_by_project: dict[str, list[str]] = {}
        for project in projects:
            if not has_task_in_tree(project.id):
                reasons_by_project.setdefault(project.id, []).append("thiếu next action")
        for task in tasks:
            if task.project_id not in project_by_id:
                continue
            status = str(task.status)
            if task.due_at is not None and task.due_at < now and status not in {"done", "cancelled"}:
                reasons_by_project.setdefault(task.project_id, []).append("task quá hạn")
            if status in {"waiting", "blocked"}:
                reasons_by_project.setdefault(task.project_id, []).append("việc chờ/bị chặn")
        commitments = await self.commitment_repo.list_open() if self.commitment_repo else []
        for commitment in commitments:
            if (
                commitment.project_id in project_by_id
                and commitment.due_at is not None
                and commitment.due_at < now
            ):
                reasons_by_project.setdefault(commitment.project_id, []).append(
                    "commitment quá hạn"
                )
        followups = await self.followup_repo.list_open() if self.followup_repo else []
        for followup in followups:
            if (
                followup.project_id in project_by_id
                and followup.due_at is not None
                and followup.due_at < now
            ):
                reasons_by_project.setdefault(followup.project_id, []).append("follow-up quá hạn")
        candidates = [
            ProjectHealthItem(project=project, reasons=_dedupe_reasons(reasons))
            for project_id, reasons in reasons_by_project.items()
            if (project := project_by_id.get(project_id)) is not None
        ]
        return sorted(
            candidates,
            key=lambda item: _sort_datetime(item.project.updated_at),
            reverse=True,
        )

    async def _actionable_leaf_projects(self) -> tuple[list, dict[str, list]]:
        if self.project_repo is None:
            return [], {}
        all_projects = await self.project_repo.list_all()
        children_by_parent: dict[str, list] = {}
        for project in all_projects:
            if project.parent_project_id:
                children_by_parent.setdefault(project.parent_project_id, []).append(project)
        active_container_ids = {
            parent_id
            for parent_id, children in children_by_parent.items()
            if any(str(child.status) == "active" for child in children)
        }
        projects = [
            project
            for project in all_projects
            if str(project.status) == "active"
            and str(project.project_type) in ACTIONABLE_PROJECT_TYPES
            and project.id not in active_container_ids
        ]
        return projects, children_by_parent

    async def _recent_undoable_events(self, limit: int) -> list:
        candidates = [
            event
            for event in await self.event_service.list_recent(limit=100)
            if event.event_type in UNDOABLE_EVENT_TYPES
        ]
        events = []
        for event in candidates:
            if not await self.event_service.was_undone(event.id):
                events.append(event)
            if len(events) >= limit:
                break
        return events


def _feedback_signal_label(value: str | None) -> str:
    return {
        FeedbackSignal.ACCEPTED.value: "Đã chấp nhận",
        FeedbackSignal.EDITED.value: "Đã chỉnh sửa",
        FeedbackSignal.REJECTED.value: "Đã từ chối",
        FeedbackSignal.IGNORED.value: "Đã bỏ qua",
        FeedbackSignal.CORRECTION.value: "Hệ thống bị sửa",
    }.get(value or "", "Phản hồi")


def _artifact_label(value: str) -> str:
    return {
        "task": "task",
        "memory": "memory",
        "memory_item": "memory",
        "person": "người",
        "project": "dự án",
    }.get(value, "dữ liệu")


def _commitment_hygiene_reasons(item) -> list[str]:
    reasons = []
    if item.due_at is None:
        reasons.append("chưa có hạn")
    if item.person_id is None and item.project_id is None:
        reasons.append("chưa gắn người/project")
    return reasons or ["đủ metadata"]


def _project_health_reason_label(item: ProjectHealthItem) -> str:
    reasons = "; ".join(item.reasons[:4])
    if len(item.reasons) > 4:
        reasons = f"{reasons}; +{len(item.reasons) - 4} tín hiệu"
    return f"{_project_age_label(item.project)} · {reasons}"


def _has_immediate_project_risk(item: ProjectHealthItem) -> bool:
    return any("quá hạn" in reason or "chờ" in reason or "bị chặn" in reason for reason in item.reasons)


def _dedupe_reasons(reasons: list[str]) -> list[str]:
    order = [
        "task quá hạn",
        "việc chờ/bị chặn",
        "commitment quá hạn",
        "follow-up quá hạn",
        "thiếu next action",
    ]
    return [reason for reason in order if reason in set(reasons)]


def _attention_signal_count(events) -> int:
    return sum(
        1
        for event in events
        if event.event_type in QUALITY_ATTENTION_EVENT_TYPES
        or (
            event.event_type == EventType.USER_FEEDBACK_RECORDED
            and event.payload.get("signal")
            in {
                FeedbackSignal.EDITED.value,
                FeedbackSignal.REJECTED.value,
                FeedbackSignal.CORRECTION.value,
            }
        )
    )


def _event_count(events, event_type: EventType) -> int:
    return sum(1 for event in events if event.event_type == event_type)


def _trend_label(current: int, previous: int) -> str:
    if current == previous:
        return "không đổi"
    if current > previous:
        return f"tăng {current - previous}"
    return f"giảm {previous - current}"


def _undoable_event_label(event_type: EventType) -> str:
    return {
        EventType.WORK_ITEM_CHANGED: "Cập nhật công việc",
        EventType.TASK_BATCH_COMPLETED: "Hoàn thành batch task",
        EventType.TASK_DONE: "Đóng task",
        EventType.FOLLOWUP_DONE: "Đóng follow-up",
        EventType.COMMITMENT_DONE: "Đóng commitment",
        EventType.DAILY_CLOSEOUT_APPLIED: "Closeout cuối ngày",
    }.get(event_type, "Thao tác")


def _format_event_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%d/%m %H:%M UTC")


def _sort_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _project_age_label(project) -> str:
    touched_at = max(_sort_datetime(project.updated_at), _sort_datetime(project.last_seen_at))
    age_days = (datetime.now(UTC) - touched_at).days
    if age_days <= 0:
        return "mới chạm hôm nay"
    if age_days < 30:
        return f"{age_days} ngày chưa có next action"
    return f"{age_days} ngày trong backlog"
