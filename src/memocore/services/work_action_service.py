from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo
import re

from memocore.adapters.storage.repositories import (
    CommitmentRepository,
    FollowUpRepository,
    ReminderRepository,
    TaskRepository,
)
from memocore.domain.models import (
    CommitmentStatus,
    EventType,
    FollowUpStatus,
    ReminderStatus,
)
from memocore.domain.schemas import AssistantAction, AssistantResponse, AssistantSection
from memocore.services.event_service import EventService
from memocore.services.task_operation_service import TaskOperationService
from memocore.services.work_state_service import WorkStateService


class WorkActionService:
    def __init__(
        self,
        task_repo: TaskRepository,
        reminder_repo: ReminderRepository,
        event_service: EventService,
        display_timezone: tzinfo = UTC,
        task_operation_service: TaskOperationService | None = None,
        followup_repo: FollowUpRepository | None = None,
        commitment_repo: CommitmentRepository | None = None,
    ):
        self.task_repo = task_repo
        self.reminder_repo = reminder_repo
        self.event_service = event_service
        self.display_timezone = display_timezone
        self.task_operation_service = task_operation_service
        self.followup_repo = followup_repo
        self.commitment_repo = commitment_repo
        self.work_state_service = WorkStateService(display_timezone)

    async def tasks_view(self) -> AssistantResponse:
        tasks = await self.task_repo.list_active()
        if not tasks:
            return AssistantResponse(title="Tasks đang mở", summary="Không có task đang mở.")
        state = self.work_state_service.classify(tasks)
        ordered = [
            *[item.task for item in state.next_actions],
            *state.waiting,
            *state.blocked,
            *state.unscheduled,
            *state.upcoming,
        ]
        visible = _unique_tasks(ordered)[:5]
        lines = [
            f"{index}. {_priority_icon(task.priority)} {task.title}{_recurrence_badge(task.recurrence_rule)} · {_task_hint(task, self.display_timezone)}"
            for index, task in enumerate(visible, 1)
        ]
        actions: list[AssistantAction] = []
        for index, task in enumerate(visible):
            actions.extend(
                [
                    AssistantAction(label="✅ Xong", action_id=f"work:q:t:done:{task.id}", row=index),
                    AssistantAction(label="🗑 Bỏ", action_id=f"work:q:t:cancel:{task.id}", row=index),
                    AssistantAction(label="⏰ Đổi hạn", action_id=f"work:q:t:due:{task.id}", row=index),
                    AssistantAction(label="🔥 Ưu tiên", action_id=f"work:q:t:pri:{task.id}", row=index),
                ]
            )
        footer = None if len(tasks) <= 5 else f"Đang hiện 5/{len(tasks)} task."
        return AssistantResponse(
            title="Tasks đang mở",
            sections=[AssistantSection(lines=lines)],
            footer=footer,
            actions=actions,
        )

    async def reminders_view(self) -> AssistantResponse:
        reminders = [
            item
            for item in await self.reminder_repo.list_recent()
            if item.status not in {ReminderStatus.SENT, ReminderStatus.CANCELLED}
        ]
        if not reminders:
            return AssistantResponse(title="Nhắc nhở", summary="Chưa có reminder đang hoạt động.")
        visible = reminders[:5]
        lines = [
            f"{index}. {item.title} · {_format_due(item.remind_at, self.display_timezone)}"
            for index, item in enumerate(visible, 1)
        ]
        actions: list[AssistantAction] = []
        for index, item in enumerate(visible):
            actions.extend(
                [
                    AssistantAction(label="✅ Xong", action_id=f"work:q:r:done:{item.id}", row=index),
                    AssistantAction(label="⏰ Đổi giờ", action_id=f"work:q:r:due:{item.id}", row=index),
                ]
            )
        return AssistantResponse(
            title="Nhắc nhở",
            sections=[AssistantSection(lines=lines)],
            actions=actions,
        )

    async def waiting_view(self) -> AssistantResponse:
        tasks = await self.task_repo.list_active()
        state = self.work_state_service.classify(tasks)
        followups = await self.followup_repo.list_open() if self.followup_repo else []
        waiting_tasks = _unique_tasks([*state.waiting, *state.blocked])[:5]
        visible_followups = followups[:5]
        if not waiting_tasks and not visible_followups:
            return AssistantResponse(
                title="Đang chờ",
                summary="Không có task đang chờ, bị chặn hoặc follow-up đang mở.",
            )
        lines: list[str] = []
        actions: list[AssistantAction] = []
        if waiting_tasks:
            lines.append("Task đang chờ/bị chặn")
            for index, task in enumerate(waiting_tasks, 1):
                lines.append(f"{index}. {task.title} · {_task_hint(task, self.display_timezone)}")
                actions.extend(
                    [
                        AssistantAction(
                            label=f"✅ Task {index}",
                            action_id=f"work:q:t:done:{task.id}",
                            row=index,
                        ),
                        AssistantAction(
                            label=f"⏰ Task {index}",
                            action_id=f"work:q:t:due:{task.id}",
                            row=index,
                        ),
                    ]
                )
        if visible_followups:
            if lines:
                lines.append("")
            lines.append("Follow-up")
            row_offset = len(waiting_tasks) + 1
            for index, followup in enumerate(visible_followups, 1):
                lines.append(
                    f"{index}. {followup.title} · {_format_due(followup.due_at, self.display_timezone)}"
                )
                row = row_offset + index - 1
                actions.extend(
                    [
                        AssistantAction(
                            label=f"✅ Follow-up {index}",
                            action_id=f"work:q:f:done:{followup.id}",
                            row=row,
                        ),
                        AssistantAction(
                            label=f"⏰ Follow-up {index}",
                            action_id=f"work:q:f:due:{followup.id}",
                            row=row,
                        ),
                        AssistantAction(
                            label=f"🗑 Follow-up {index}",
                            action_id=f"work:q:f:cancel:{followup.id}",
                            row=row,
                        ),
                    ]
                )
        return AssistantResponse(
            title="Đang chờ",
            sections=[AssistantSection(lines=lines)],
            footer="Đóng mục chỉ khi đã nhận phản hồi hoặc không cần theo nữa.",
            actions=actions,
        )

    async def commitments_view(self) -> AssistantResponse:
        commitments = await self.commitment_repo.list_open() if self.commitment_repo else []
        if not commitments:
            return AssistantResponse(title="Cam kết", summary="Không có commitment đang mở.")
        visible = commitments[:5]
        lines = [
            f"{index}. {item.title} · {_format_due(item.due_at, self.display_timezone)}"
            for index, item in enumerate(visible, 1)
        ]
        actions: list[AssistantAction] = []
        for index, item in enumerate(visible):
            actions.extend(
                [
                    AssistantAction(
                        label="✅ Xong",
                        action_id=f"work:q:c:done:{item.id}",
                        row=index,
                    ),
                    AssistantAction(
                        label="⏰ Đổi hạn",
                        action_id=f"work:q:c:due:{item.id}",
                        row=index,
                    ),
                    AssistantAction(
                        label="🗑 Bỏ",
                        action_id=f"work:q:c:cancel:{item.id}",
                        row=index,
                    ),
                ]
            )
        return AssistantResponse(
            title="Cam kết",
            sections=[AssistantSection(lines=lines)],
            footer="Cam kết đã đóng có thể hoàn tác ngay sau khi cập nhật.",
            actions=actions,
        )

    async def agenda_view(
        self,
        summary: str,
        target_date: date,
        *,
        title: str,
        now: datetime | None = None,
    ) -> AssistantResponse:
        now = now or datetime.now(UTC)
        local_today = now.astimezone(self.display_timezone).date()
        tasks = await self.task_repo.list_active()
        state = self.work_state_service.classify(tasks, now)
        visible = [
            task
            for task in _unique_tasks([*state.overdue, *state.due_today])
            if task.due_at is not None
            and (
                task.due_at.astimezone(self.display_timezone).date() == target_date
                or (
                    target_date == local_today
                    and task.due_at.astimezone(self.display_timezone).date() < target_date
                )
            )
        ][:5]
        actions: list[AssistantAction] = []
        for index, task in enumerate(visible):
            actions.extend(
                [
                    AssistantAction(
                        label=f"✅ Task {index + 1}",
                        action_id=f"work:q:t:done:{task.id}",
                        row=index,
                    ),
                    AssistantAction(
                        label=f"⏰ Task {index + 1}",
                        action_id=f"work:q:t:due:{task.id}",
                        row=index,
                    ),
                ]
            )
        summary_lines = summary.splitlines()
        response_title = title
        if summary_lines and summary_lines[0].startswith(f"{title} -"):
            response_title = summary_lines[0]
            summary = "\n".join(summary_lines[1:]).lstrip() or None
        return AssistantResponse(title=response_title, summary=summary, actions=actions)

    async def handle(self, callback_data: str) -> AssistantResponse | None:
        if callback_data == "work:cancel":
            return AssistantResponse(title="Đã hủy", summary="Không có thay đổi nào được áp dụng.")
        parts = callback_data.split(":")
        if parts[0] != "work":
            return None
        if len(parts) == 4 and parts[1:3] == ["u", "e"]:
            return await self._undo(parts[3])
        if len(parts) < 5:
            return None
        phase, kind, action = parts[1], parts[2], parts[3]
        if kind not in {"t", "r", "f", "c"}:
            return None
        if phase == "q":
            return await self._question(kind, action, parts[4:])
        if phase == "x":
            return await self._execute(kind, action, parts[4:])
        return None

    async def _question(
        self, kind: str, action: str, args: list[str]
    ) -> AssistantResponse | None:
        if not args:
            return None
        entity_id = args[-1]
        entity = await self._get(kind, entity_id)
        if entity is None:
            return None
        label = entity.title
        if action == "done":
            return _confirmation(
                "Xác nhận hoàn thành",
                f"Đánh dấu “{label}” là xong?",
                f"work:x:{kind}:done:{entity_id}",
            )
        if kind in {"t", "f", "c"} and action == "cancel":
            return _confirmation(
                "Xác nhận bỏ",
                f"Bỏ “{label}” khỏi danh sách đang mở?",
                f"work:x:{kind}:cancel:{entity_id}",
            )
        if action == "due" and len(args) == 1:
            return AssistantResponse(
                title="Đổi hạn",
                summary=f"Chọn hạn mới cho “{label}”.",
                actions=[
                    AssistantAction(label="Tối nay 18:00", action_id=f"work:q:{kind}:setdue:today:{entity_id}", row=0),
                    AssistantAction(label="Mai 09:00", action_id=f"work:q:{kind}:setdue:tomorrow:{entity_id}", row=1),
                    AssistantAction(label="+1 tuần", action_id=f"work:q:{kind}:setdue:week:{entity_id}", row=2),
                ],
            )
        if action == "setdue" and len(args) == 2:
            code = args[0]
            due = self._due_from_code(code)
            if due is None:
                return None
            return _confirmation(
                "Xác nhận đổi hạn",
                f"Đổi “{label}” sang {_format_due(due, self.display_timezone)}?",
                f"work:x:{kind}:due:{code}:{entity_id}",
            )
        if kind == "t" and action == "pri" and len(args) == 1:
            return AssistantResponse(
                title="Đổi ưu tiên",
                summary=f"Chọn mức ưu tiên cho “{label}”.",
                actions=[
                    AssistantAction(label="🔴 Cao", action_id=f"work:q:t:setpri:high:{entity_id}", row=0),
                    AssistantAction(label="🟡 Vừa", action_id=f"work:q:t:setpri:medium:{entity_id}", row=1),
                    AssistantAction(label="🔵 Thấp", action_id=f"work:q:t:setpri:low:{entity_id}", row=2),
                ],
            )
        if kind == "t" and action == "setpri" and len(args) == 2:
            priority = args[0]
            if priority not in {"high", "medium", "low"}:
                return None
            return _confirmation(
                "Xác nhận đổi ưu tiên",
                f"Đổi “{label}” sang mức {priority}?",
                f"work:x:t:pri:{priority}:{entity_id}",
            )
        return None

    async def _execute(
        self, kind: str, action: str, args: list[str]
    ) -> AssistantResponse | None:
        if not args:
            return None
        entity_id = args[-1]
        entity = await self._get(kind, entity_id)
        if entity is None:
            return None
        before = _snapshot(kind, entity)
        next_task = None
        next_created = False
        if action == "done" and len(args) == 1:
            if kind == "t":
                _, next_task, next_created = (
                    await self.task_repo.complete_and_schedule_next(entity_id)
                )
            elif kind == "r":
                await self.reminder_repo.update_status(entity_id, ReminderStatus.CANCELLED)
            elif kind == "f" and self.followup_repo is not None:
                await self.followup_repo.update_status(entity_id, FollowUpStatus.DONE)
            elif kind == "c" and self.commitment_repo is not None:
                await self.commitment_repo.update_status(entity_id, CommitmentStatus.DONE)
            else:
                return None
        elif action == "cancel" and len(args) == 1:
            if kind == "t":
                await self.task_repo.update_status(entity_id, "cancelled")
            elif kind == "f" and self.followup_repo is not None:
                await self.followup_repo.update_status(entity_id, FollowUpStatus.CANCELLED)
            elif kind == "c" and self.commitment_repo is not None:
                await self.commitment_repo.update_status(entity_id, CommitmentStatus.CANCELLED)
            else:
                return None
        elif action == "due" and len(args) == 2:
            due = self._due_from_code(args[0])
            if due is None:
                return None
            if kind == "t":
                await self.task_repo.update_due_at(entity_id, due)
            elif kind == "r":
                await self.reminder_repo.update_schedule(entity_id, due)
            elif kind == "f" and self.followup_repo is not None:
                await self.followup_repo.update_due_at(entity_id, due)
            elif kind == "c" and self.commitment_repo is not None:
                await self.commitment_repo.update_due_at(entity_id, due)
            else:
                return None
        elif kind == "t" and action == "pri" and len(args) == 2:
            priority = args[0]
            if priority not in {"high", "medium", "low"}:
                return None
            await self.task_repo.update_priority(entity_id, priority)
        else:
            return None
        updated = await self._get(kind, entity_id)
        event = await self.event_service.append_event(
            EventType.WORK_ITEM_CHANGED,
            _entity_type(kind),
            entity_id,
            {
                "action": action,
                "before": before,
                "after": _snapshot(kind, updated),
                "next_task_id": next_task.id if next_task and next_created else None,
            },
        )
        if next_task is not None and next_created:
            await self.event_service.append_event(
                EventType.TASK_RECURRENCE_SCHEDULED,
                "task",
                next_task.id,
                {
                    "previous_task_id": entity_id,
                    "recurrence_rule": entity.recurrence_rule,
                },
            )
        return AssistantResponse(
            title="Đã cập nhật",
            summary=_diff_text(before, _snapshot(kind, updated)),
            actions=[
                AssistantAction(label="↩ Hoàn tác", action_id=f"work:u:e:{event.id}")
            ],
        )

    async def _undo(self, event_id: str) -> AssistantResponse | None:
        event = await self.event_service.get_event(event_id)
        if event is None:
            return None
        if event.event_type == EventType.TASK_BATCH_COMPLETED:
            if self.task_operation_service is None:
                return None
            result = await self.task_operation_service.undo_batch(event_id)
            if not result.restored_task_ids and not result.skipped_task_ids:
                return AssistantResponse(title="Batch đã được hoàn tác trước đó")
            summary = f"Đã khôi phục {len(result.restored_task_ids)} task."
            if result.skipped_task_ids:
                summary += (
                    f" Bỏ qua {len(result.skipped_task_ids)} task đã thay đổi sau batch."
                )
            return AssistantResponse(title="Đã hoàn tác batch", summary=summary)
        if event.event_type in {
            EventType.TASK_DONE,
            EventType.FOLLOWUP_DONE,
            EventType.COMMITMENT_DONE,
        }:
            return await self._undo_lifecycle_event(event_id)
        if event.event_type == EventType.DAILY_CLOSEOUT_APPLIED:
            return await self._undo_closeout_event(event_id)
        if event.event_type != EventType.WORK_ITEM_CHANGED:
            return None
        if await self.event_service.was_undone(event_id):
            return AssistantResponse(title="Đã hoàn tác trước đó")
        if (
            event.entity_type == "task"
            and event.payload.get("action") == "rename_task"
            and self.task_operation_service is not None
        ):
            restored = await self.task_operation_service.undo_event(event_id)
            if restored.task is None:
                return None
            return AssistantResponse(
                title="Đã hoàn tác",
                summary=f"Task trở lại thành “{restored.task.title}”.",
            )
        before = event.payload.get("before", {})
        if event.entity_type == "task":
            next_task_id = event.payload.get("next_task_id")
            if next_task_id:
                await self.task_repo.delete(next_task_id)
            await self.task_repo.update_status(event.entity_id, before["status"])
            await self.task_repo.update_due_at(event.entity_id, _parse_dt(before.get("due_at")))
            await self.task_repo.update_priority(event.entity_id, before["priority"])
        elif event.entity_type == "reminder":
            remind_at = _parse_dt(before.get("remind_at"))
            if remind_at is not None:
                await self.reminder_repo.update_schedule(
                    event.entity_id,
                    remind_at,
                    ReminderStatus(before["status"]),
                )
            else:
                await self.reminder_repo.update_status(
                    event.entity_id, ReminderStatus(before["status"])
                )
        elif event.entity_type == "followup":
            if self.followup_repo is None:
                return None
            await self.followup_repo.update_status(
                event.entity_id,
                FollowUpStatus(before["status"]),
            )
            await self.followup_repo.update_due_at(
                event.entity_id, _parse_dt(before.get("due_at"))
            )
        elif event.entity_type == "commitment":
            if self.commitment_repo is None:
                return None
            await self.commitment_repo.update_status(
                event.entity_id,
                CommitmentStatus(before["status"]),
            )
            await self.commitment_repo.update_due_at(
                event.entity_id, _parse_dt(before.get("due_at"))
            )
        else:
            return None
        await self.event_service.append_event(
            EventType.WORK_ITEM_UNDONE,
            "work_event",
            event_id,
            {"restored": before},
        )
        return AssistantResponse(title="Đã hoàn tác", summary="Trạng thái cũ đã được khôi phục.")

    async def _undo_lifecycle_event(self, event_id: str) -> AssistantResponse | None:
        event = await self.event_service.get_event(event_id)
        if event is None:
            return None
        if await self.event_service.was_undone(event_id):
            return AssistantResponse(title="Đã hoàn tác trước đó")
        before = event.payload.get("before")
        if not isinstance(before, dict) or not before:
            return None
        due_at = _parse_dt(before.get("due_at"))
        if event.entity_type == "task":
            next_task_id = event.payload.get("next_task_id")
            if next_task_id:
                await self.task_repo.delete(next_task_id)
            await self.task_repo.update_status(event.entity_id, before["status"])
            await self.task_repo.update_due_at(event.entity_id, due_at)
            await self.task_repo.update_priority(event.entity_id, before["priority"])
        elif event.entity_type == "followup":
            if self.followup_repo is None:
                return None
            await self.followup_repo.update_status(
                event.entity_id,
                FollowUpStatus(before["status"]),
            )
            await self.followup_repo.update_due_at(event.entity_id, due_at)
        elif event.entity_type == "commitment":
            if self.commitment_repo is None:
                return None
            await self.commitment_repo.update_status(
                event.entity_id,
                CommitmentStatus(before["status"]),
            )
            await self.commitment_repo.update_due_at(event.entity_id, due_at)
        else:
            return None
        await self.event_service.append_event(
            EventType.WORK_ITEM_UNDONE,
            "work_event",
            event_id,
            {"restored": before},
        )
        return AssistantResponse(title="Đã hoàn tác", summary="Open loop đã được khôi phục.")

    async def _undo_closeout_event(self, event_id: str) -> AssistantResponse | None:
        event = await self.event_service.get_event(event_id)
        if event is None:
            return None
        if await self.event_service.was_undone(event_id):
            return AssistantResponse(title="Đã hoàn tác trước đó")
        due_at = _parse_dt(event.payload.get("due_at"))
        items = event.payload.get("items", {})
        if due_at is None or not isinstance(items, dict):
            return None
        restored = 0
        skipped = 0
        for item in items.get("tasks", []):
            entity = await self.task_repo.get_by_id(item["id"])
            if entity is None or entity.due_at != due_at or str(entity.status) != item["status"]:
                skipped += 1
                continue
            await self.task_repo.update_due_at(entity.id, _parse_dt(item.get("due_at")))
            restored += 1
        for item in items.get("followups", []):
            if self.followup_repo is None:
                skipped += 1
                continue
            entity = await self.followup_repo.get_by_id(item["id"])
            if entity is None or entity.due_at != due_at or str(entity.status) != item["status"]:
                skipped += 1
                continue
            await self.followup_repo.update_due_at(entity.id, _parse_dt(item.get("due_at")))
            restored += 1
        for item in items.get("commitments", []):
            if self.commitment_repo is None:
                skipped += 1
                continue
            entity = await self.commitment_repo.get_by_id(item["id"])
            if entity is None or entity.due_at != due_at or str(entity.status) != item["status"]:
                skipped += 1
                continue
            await self.commitment_repo.update_due_at(entity.id, _parse_dt(item.get("due_at")))
            restored += 1
        await self.event_service.append_event(
            EventType.WORK_ITEM_UNDONE,
            "work_event",
            event_id,
            {"restored_count": restored, "skipped_count": skipped},
        )
        summary = f"Đã khôi phục {restored} mục từ closeout."
        if skipped:
            summary += f" Bỏ qua {skipped} mục đã thay đổi sau closeout."
        return AssistantResponse(title="Đã hoàn tác closeout", summary=summary)

    async def _get(self, kind: str, entity_id: str):
        if kind == "t":
            return await self.task_repo.get_by_id(entity_id)
        if kind == "r":
            return await self.reminder_repo.get_by_id(entity_id)
        if kind == "f" and self.followup_repo is not None:
            return await self.followup_repo.get_by_id(entity_id)
        if kind == "c" and self.commitment_repo is not None:
            return await self.commitment_repo.get_by_id(entity_id)
        return None

    def _due_from_code(self, code: str) -> datetime | None:
        local_now = datetime.now(UTC).astimezone(self.display_timezone)
        if code == "today":
            target = datetime.combine(local_now.date(), time(18, 0), self.display_timezone)
            if target <= local_now:
                target += timedelta(days=1)
            return target.astimezone(UTC)
        if code == "tomorrow":
            return datetime.combine(
                local_now.date() + timedelta(days=1),
                time(9, 0),
                self.display_timezone,
            ).astimezone(UTC)
        if code == "week":
            return (local_now + timedelta(days=7)).astimezone(UTC)
        return None


def _confirmation(title: str, summary: str, action_id: str) -> AssistantResponse:
    return AssistantResponse(
        title=title,
        summary=summary,
        actions=[
            AssistantAction(label="Xác nhận", action_id=action_id, row=0),
            AssistantAction(label="Hủy", action_id="work:cancel", row=0),
        ],
    )


def _snapshot(kind: str, entity) -> dict:
    if entity is None:
        return {}
    if kind == "t":
        return {
            "title": entity.title,
            "status": str(entity.status),
            "priority": entity.priority,
            "due_at": entity.due_at.isoformat() if entity.due_at else None,
        }
    if kind in {"f", "c"}:
        return {
            "title": entity.title,
            "status": str(entity.status),
            "due_at": entity.due_at.isoformat() if entity.due_at else None,
        }
    return {
        "title": entity.title,
        "status": str(entity.status),
        "remind_at": entity.remind_at.isoformat() if entity.remind_at else None,
    }


def _diff_text(before: dict, after: dict) -> str:
    changes = []
    for key in ("status", "priority", "due_at", "remind_at"):
        if before.get(key) != after.get(key):
            changes.append(f"{key}: {before.get(key) or 'trống'} → {after.get(key) or 'trống'}")
    return "\n".join(changes) if changes else "Không có thay đổi."


def _entity_type(kind: str) -> str:
    return {
        "t": "task",
        "r": "reminder",
        "f": "followup",
        "c": "commitment",
    }[kind]


def _format_due(value: datetime | None, display_timezone: tzinfo) -> str:
    if value is None:
        return "chưa có hạn"
    return value.astimezone(display_timezone).strftime("%H:%M %d/%m")


def _priority_icon(priority: str) -> str:
    return {"high": "🔴", "medium": "🟡", "low": "🔵"}.get(priority, "🟡")


def _recurrence_badge(rule: str | None) -> str:
    if rule == "daily":
        return " · 🔁 Hằng ngày"
    if rule == "weekly" or (rule or "").startswith("weekly:"):
        return " · 🔁 Hằng tuần"
    match = re.fullmatch(r"interval:(\d+)([dw])", rule or "")
    if match:
        unit = "ngày" if match.group(2) == "d" else "tuần"
        return f" · 🔁 Mỗi {int(match.group(1))} {unit}"
    return ""


def _task_hint(task, display_timezone: tzinfo) -> str:
    status = str(getattr(task, "status", "open"))
    if status == "waiting":
        return "đang chờ"
    if status == "blocked":
        return "bị chặn"
    return _format_due(task.due_at, display_timezone)


def _unique_tasks(tasks) -> list:
    seen: set[str] = set()
    result = []
    for task in tasks:
        task_id = getattr(task, "id", None)
        if task_id in seen:
            continue
        if task_id is not None:
            seen.add(task_id)
        result.append(task)
    return result


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
