from __future__ import annotations

from datetime import UTC, datetime, timedelta

from memocore.adapters.storage.repositories import (
    ClarificationRequestRepository,
    MemoryItemRepository,
    TaskRepository,
)
from memocore.domain.models import EventType, MemoryStatus
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
    ):
        self.memory_repo = memory_repo
        self.task_repo = task_repo
        self.clarification_repo = clarification_repo
        self.event_service = event_service

    async def overview(self) -> AssistantResponse:
        memories = await self.memory_repo.list_all()
        tasks = await self.task_repo.list_active()
        pending = await self.clarification_repo.list_pending()
        since = datetime.now(UTC) - timedelta(days=30)
        duplicates = await self.event_service.list_recent(
            EventType.MEMORY_DUPLICATE_SUGGESTED, since=since, limit=100
        )
        alias_suggestions = await self.event_service.list_recent(
            EventType.ENTITY_ALIAS_SUGGESTED, since=since, limit=100
        )
        alias_confirmed = {
            event.payload.get("suggestion_event_id")
            for event in await self.event_service.list_recent(
                EventType.ENTITY_ALIAS_CONFIRMED, since=since, limit=200
            )
        }
        unresolved_aliases = [
            event for event in alias_suggestions if event.id not in alias_confirmed
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
        feedback = await self.event_service.list_recent(
            EventType.USER_FEEDBACK_RECORDED, since=since, limit=100
        )

        return AssistantResponse(
            title="Cần xem lại",
            summary=(
                f"{len(review_memories)} ghi nhớ · {len(unresolved_aliases)} liên kết tên · "
                f"{len(pending)} câu hỏi đang chờ · {len(undated_tasks)} task chưa có hạn."
            ),
            sections=[
                AssistantSection(
                    heading="Chất lượng 30 ngày",
                    lines=[
                        f"Gợi ý trùng: {len(duplicates)}",
                        f"Clarification chưa giải quyết được: {len(failed_clarifications)}",
                        f"Lần người dùng sửa hệ thống: {len(feedback)}",
                    ],
                )
            ],
            footer="Chỉ các mục chưa chắc chắn mới xuất hiện ở đây; dữ liệu bình thường không chen vào hội thoại hằng ngày.",
            actions=[
                AssistantAction(label="🧠 Ghi nhớ", action_id="mem:t:review:0", row=0),
                AssistantAction(label="👤 Tên người", action_id="nav:review:people", row=0),
                AssistantAction(label="📁 Tên dự án", action_id="nav:review:projects", row=1),
                AssistantAction(label="❓ Đang chờ", action_id="nav:review:clarifications", row=1),
                AssistantAction(label="📋 Task", action_id="nav:work:tasks", row=2),
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
