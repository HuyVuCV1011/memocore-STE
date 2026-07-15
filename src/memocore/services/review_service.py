from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from memocore.adapters.storage.repositories import (
    ClarificationRequestRepository,
    MemoryItemRepository,
    ProjectRepository,
    TaskRepository,
)
from memocore.domain.models import EventType, FeedbackSignal, FeedbackStatus, MemoryStatus
from memocore.domain.schemas import AssistantAction, AssistantResponse, AssistantSection
from memocore.services.event_service import EventService


class ReviewService:
    """One small inbox for uncertain or unfinished system state."""

    def __init__(
        self,
        memory_repo: MemoryItemRepository,
        task_repo: TaskRepository,
        clarification_repo: ClarificationRequestRepository,
        event_service: EventService,
        project_repo: ProjectRepository | None = None,
    ):
        self.memory_repo = memory_repo
        self.task_repo = task_repo
        self.clarification_repo = clarification_repo
        self.event_service = event_service
        self.project_repo = project_repo

    async def overview(self) -> AssistantResponse:
        memories = await self.memory_repo.list_all()
        tasks = await self.task_repo.list_active()
        projects_without_next_action = await self._projects_without_next_action(tasks)
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

        return AssistantResponse(
            title="Cần xem lại",
            summary=(
                f"{len(review_memories)} ghi nhớ · {len(unresolved_aliases)} liên kết tên · "
                f"{len(pending)} câu hỏi đang chờ · {len(undated_tasks)} task chưa có hạn · "
                f"{len(projects_without_next_action)} project thiếu next action · "
                f"{len(open_feedback)} phản hồi chưa xử lý · {len(system_failures)} cảnh báo hệ thống."
            ),
            sections=[
                AssistantSection(
                    heading="Chất lượng 30 ngày",
                    lines=[
                        f"Gợi ý trùng: {len(duplicates)}",
                        f"Clarification chưa giải quyết được: {len(failed_clarifications)}",
                        f"Project thiếu next action: {len(projects_without_next_action)}",
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
            footer="Chỉ các mục chưa chắc chắn mới xuất hiện ở đây; dữ liệu bình thường không chen vào hội thoại hằng ngày.",
            actions=[
                AssistantAction(label="🧠 Ghi nhớ", action_id="mem:t:review:0", row=0),
                AssistantAction(label="👤 Tên người", action_id="nav:review:people", row=0),
                AssistantAction(label="📁 Tên dự án", action_id="nav:review:projects", row=1),
                AssistantAction(label="❓ Đang chờ", action_id="nav:review:clarifications", row=1),
                AssistantAction(label="💬 Phản hồi", action_id="nav:review:feedback", row=2),
                AssistantAction(label="⚙️ Hệ thống", action_id="nav:review:system", row=2),
                AssistantAction(label="📍 Project health", action_id="nav:review:project-health", row=3),
                AssistantAction(label="📋 Task", action_id="nav:work:tasks", row=4),
            ],
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
        lines = []
        if backup_failures:
            lines.extend(
                f"{index}. Backup lỗi · {event.created_at.date().isoformat()}"
                for index, event in enumerate(backup_failures[:8], 1)
            )
        else:
            lines.append("Không có lỗi backup nào trong 30 ngày gần nhất.")
        lines.append(f"Restore drill đã ghi nhận: {len(restore_drills)}")
        return AssistantResponse(
            title="Hệ thống cần xem lại",
            summary=f"{len(backup_failures)} lỗi backup trong 30 ngày gần nhất.",
            sections=[AssistantSection(lines=lines)],
            footer=(
                "Màn hình này chỉ hiện trạng thái vận hành; không hiển thị nội dung note, task hay memory."
            ),
            actions=[AssistantAction(label="Quay lại", action_id="nav:review", row=0)],
        )

    async def project_health(self) -> AssistantResponse:
        tasks = await self.task_repo.list_active()
        projects = await self._projects_without_next_action(tasks)
        lines = [f"{index}. {project.name}" for index, project in enumerate(projects[:10], 1)]
        if not lines:
            lines = ["Không có project active nào thiếu next action."]
        return AssistantResponse(
            title="Project thiếu next action",
            summary=f"{len(projects)} project active cần rà lại.",
            sections=[AssistantSection(lines=lines)],
            footer=(
                "Mở /project <tên> để xem Project health và thêm task tiếp theo nếu project còn hoạt động."
            ),
            actions=[AssistantAction(label="Quay lại", action_id="nav:review", row=0)],
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

    async def _projects_without_next_action(self, tasks) -> list:
        if self.project_repo is None:
            return []
        task_project_ids = {task.project_id for task in tasks if task.project_id}
        projects = [
            project
            for project in await self.project_repo.list_all()
            if str(project.status) == "active" and project.id not in task_project_ids
        ]
        return sorted(projects, key=lambda project: _sort_datetime(project.updated_at), reverse=True)


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


def _sort_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
