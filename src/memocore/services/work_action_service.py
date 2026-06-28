from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo

from memocore.adapters.storage.repositories import ReminderRepository, TaskRepository
from memocore.domain.models import EventType, ReminderStatus
from memocore.domain.schemas import AssistantAction, AssistantResponse, AssistantSection
from memocore.services.event_service import EventService
from memocore.services.task_operation_service import TaskOperationService


class WorkActionService:
    def __init__(
        self,
        task_repo: TaskRepository,
        reminder_repo: ReminderRepository,
        event_service: EventService,
        display_timezone: tzinfo = UTC,
        task_operation_service: TaskOperationService | None = None,
    ):
        self.task_repo = task_repo
        self.reminder_repo = reminder_repo
        self.event_service = event_service
        self.display_timezone = display_timezone
        self.task_operation_service = task_operation_service

    async def tasks_view(self) -> AssistantResponse:
        tasks = await self.task_repo.list_active()
        if not tasks:
            return AssistantResponse(title="Tasks đang mở", summary="Không có task đang mở.")
        visible = tasks[:5]
        lines = [
            f"{index}. {_priority_icon(task.priority)} {task.title}{_recurrence_badge(task.recurrence_rule)} · {_format_due(task.due_at, self.display_timezone)}"
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

    async def agenda_view(
        self,
        summary: str,
        target_date: date,
        *,
        title: str,
    ) -> AssistantResponse:
        local_today = datetime.now(UTC).astimezone(self.display_timezone).date()
        tasks = await self.task_repo.list_active()
        visible = [
            task
            for task in tasks
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
                        label="✅ Xong",
                        action_id=f"work:q:t:done:{task.id}",
                        row=index,
                    ),
                    AssistantAction(
                        label="⏰ Đổi giờ",
                        action_id=f"work:q:t:due:{task.id}",
                        row=index,
                    ),
                    AssistantAction(
                        label="🔥 Ưu tiên",
                        action_id=f"work:q:t:pri:{task.id}",
                        row=index,
                    ),
                ]
            )
        return AssistantResponse(title=title, summary=summary, actions=actions)

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
        if kind not in {"t", "r"}:
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
        if kind == "t" and action == "cancel":
            return _confirmation(
                "Xác nhận bỏ task",
                f"Bỏ task “{label}” khỏi danh sách đang mở?",
                f"work:x:t:cancel:{entity_id}",
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
            else:
                await self.reminder_repo.update_status(entity_id, ReminderStatus.CANCELLED)
        elif kind == "t" and action == "cancel" and len(args) == 1:
            await self.task_repo.update_status(entity_id, "cancelled")
        elif action == "due" and len(args) == 2:
            due = self._due_from_code(args[0])
            if due is None:
                return None
            if kind == "t":
                await self.task_repo.update_due_at(entity_id, due)
            else:
                await self.reminder_repo.update_schedule(entity_id, due)
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
            "task" if kind == "t" else "reminder",
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
        else:
            return None
        await self.event_service.append_event(
            EventType.WORK_ITEM_UNDONE,
            "work_event",
            event_id,
            {"restored": before},
        )
        return AssistantResponse(title="Đã hoàn tác", summary="Trạng thái cũ đã được khôi phục.")

    async def _get(self, kind: str, entity_id: str):
        if kind == "t":
            return await self.task_repo.get_by_id(entity_id)
        return await self.reminder_repo.get_by_id(entity_id)

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


def _format_due(value: datetime | None, display_timezone: tzinfo) -> str:
    if value is None:
        return "chưa có hạn"
    return value.astimezone(display_timezone).strftime("%H:%M %d/%m")


def _priority_icon(priority: str) -> str:
    return {"high": "🔴", "medium": "🟡", "low": "🔵"}.get(priority, "🟡")


def _recurrence_badge(rule: str | None) -> str:
    return {
        "daily": " · 🔁 Hằng ngày",
        "weekly": " · 🔁 Hằng tuần",
    }.get(rule, "")


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
