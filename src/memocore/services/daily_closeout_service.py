from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
import json

from memocore.adapters.storage.repositories import (
    ClarificationRequestRepository,
    CommitmentRepository,
    FollowUpRepository,
    TaskRepository,
)
from memocore.domain.models import ClarificationRequest, EventType
from memocore.domain.schemas import AssistantAction, AssistantResponse, AssistantSection
from memocore.services.event_service import EventService


@dataclass(frozen=True)
class CloseoutCandidate:
    id: str
    title: str
    status: str
    due_at: str | None
    updated_at: str


@dataclass(frozen=True)
class CloseoutPreview:
    tasks: list[CloseoutCandidate]
    followups: list[CloseoutCandidate]
    commitments: list[CloseoutCandidate]

    @property
    def total(self) -> int:
        return len(self.tasks) + len(self.followups) + len(self.commitments)


class DailyCloseoutService:
    """Preview and confirm small end-of-day state changes."""

    def __init__(
        self,
        task_repo: TaskRepository,
        clarification_repo: ClarificationRequestRepository,
        event_service: EventService,
        *,
        followup_repo: FollowUpRepository | None = None,
        commitment_repo: CommitmentRepository | None = None,
        display_timezone: tzinfo = UTC,
    ):
        self.task_repo = task_repo
        self.clarification_repo = clarification_repo
        self.event_service = event_service
        self.followup_repo = followup_repo
        self.commitment_repo = commitment_repo
        self.display_timezone = display_timezone

    async def preview(
        self,
        *,
        source_chat_id: str,
        source_message_id: str | None = None,
        now: datetime | None = None,
    ) -> AssistantResponse:
        now = now or datetime.now(UTC)
        closeout = CloseoutPreview(
            tasks=self._carry_candidates(await self.task_repo.list_active(), now),
            followups=(
                self._carry_candidates(await self.followup_repo.list_open(), now)
                if self.followup_repo
                else []
            ),
            commitments=(
                self._carry_candidates(await self.commitment_repo.list_open(), now)
                if self.commitment_repo
                else []
            ),
        )
        if closeout.total == 0:
            return AssistantResponse(
                title="End-of-day closeout",
                summary="Không có task, follow-up hoặc commitment nào cần kéo sang ngày mai.",
                sections=[
                    AssistantSection(
                        heading="Gợi ý",
                        lines=[
                            "Anh có thể ghi thêm một ưu tiên cho ngày mai bằng /task hoặc lưu memory nếu có điều cần nhớ."
                        ],
                    )
                ],
            )

        tomorrow_due = _tomorrow_morning(now, self.display_timezone)
        field_payload = {
            "schema_version": 1,
            "due_at": tomorrow_due.isoformat(),
            "tasks": [candidate.__dict__ for candidate in closeout.tasks],
            "followups": [candidate.__dict__ for candidate in closeout.followups],
            "commitments": [candidate.__dict__ for candidate in closeout.commitments],
        }
        question = _preview_question(closeout, tomorrow_due, self.display_timezone)
        await self.clarification_repo.create(
            ClarificationRequest(
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
                entity_type="daily_closeout",
                entity_id=",".join(
                    [
                        *[candidate.id for candidate in closeout.tasks],
                        *[candidate.id for candidate in closeout.followups],
                        *[candidate.id for candidate in closeout.commitments],
                    ]
                ),
                field_name="closeout|" + json.dumps(field_payload, separators=(",", ":")),
                question=question,
            )
        )
        await self.event_service.append_event(
            EventType.DAILY_CLOSEOUT_PREVIEWED,
            "telegram_chat",
            source_chat_id,
            {
                "task_count": len(closeout.tasks),
                "followup_count": len(closeout.followups),
                "commitment_count": len(closeout.commitments),
                "due_at": tomorrow_due.isoformat(),
            },
            created_at=now,
        )
        return AssistantResponse(
            title="End-of-day closeout",
            summary=(
                f"Preview: chuyển {closeout.total} mục sang "
                f"{tomorrow_due.astimezone(self.display_timezone).strftime('%H:%M ngày %d/%m')}."
            ),
            sections=[AssistantSection(heading="Sẽ cập nhật", lines=question.splitlines())],
            footer="Chưa ghi gì vào task, follow-up hoặc commitment cho tới khi anh xác nhận.",
            actions=[
                AssistantAction(label="Xác nhận", action_id="closeout:confirm", row=0),
                AssistantAction(label="Hủy", action_id="closeout:cancel", row=0),
            ],
        )

    def _carry_candidates(self, tasks, now: datetime) -> list[CloseoutCandidate]:
        local_now = now.astimezone(self.display_timezone)
        day_end = datetime.combine(
            local_now.date(), time.max, tzinfo=self.display_timezone
        ).astimezone(UTC)
        candidates = [
            task
            for task in tasks
            if str(task.status) in {"candidate", "open", "waiting", "blocked"}
            and (task.due_at is None or task.due_at <= day_end)
        ]
        candidates.sort(
            key=lambda task: (
                0 if task.due_at and task.due_at < now else 1,
                0 if str(getattr(task, "priority", "")) == "high" else 1,
                task.due_at or datetime.max.replace(tzinfo=UTC),
                task.created_at,
            )
        )
        return [
            CloseoutCandidate(
                id=task.id,
                title=task.title,
                status=str(task.status),
                due_at=task.due_at.isoformat() if task.due_at else None,
                updated_at=task.updated_at.isoformat(),
            )
            for task in candidates[:5]
        ]


def decode_closeout_field(field_name: str) -> dict | None:
    if not field_name.startswith("closeout|"):
        return None
    try:
        payload = json.loads(field_name.split("|", 1)[1])
    except json.JSONDecodeError:
        return None
    if payload.get("schema_version") != 1:
        return None
    return payload


def _tomorrow_morning(now: datetime, display_timezone: tzinfo) -> datetime:
    local_now = now.astimezone(display_timezone)
    local_due = datetime.combine(
        local_now.date() + timedelta(days=1),
        time(hour=9),
        tzinfo=display_timezone,
    )
    return local_due.astimezone(UTC)


def _preview_question(
    closeout: CloseoutPreview,
    due_at: datetime,
    display_timezone: tzinfo,
) -> str:
    local_due = due_at.astimezone(display_timezone).strftime("%H:%M ngày %d/%m")
    lines = [f"Chuyển {closeout.total} mục sang {local_due}:"]
    _append_group(lines, "Task", closeout.tasks)
    _append_group(lines, "Follow-up", closeout.followups)
    _append_group(lines, "Commitment", closeout.commitments)
    lines.append("Anh xác nhận để em cập nhật, hoặc hủy để giữ nguyên.")
    return "\n".join(lines)


def _append_group(
    lines: list[str],
    label: str,
    candidates: list[CloseoutCandidate],
) -> None:
    if not candidates:
        return
    lines.append(f"{label}:")
    for index, candidate in enumerate(candidates, 1):
        lines.append(f"{index}. {candidate.title}")
