from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo

from memocore.adapters.storage.repositories import (
    ClarificationRequestRepository,
    ReminderRepository,
    TaskRepository,
    parse_model_datetime,
)
from memocore.domain.models import ClarificationRequest, EventType
from memocore.services.event_service import EventService
from memocore.services.feedback_log import write_feedback_signal
from memocore.services.reminder_service import ReminderService
from memocore.services.task_operation_service import TaskOperationService


@dataclass(frozen=True)
class ClarificationResult:
    handled: bool
    message: str


class ClarificationService:
    def __init__(
        self,
        clarification_repo: ClarificationRequestRepository,
        reminder_repo: ReminderRepository,
        reminder_service: ReminderService,
        event_service: EventService,
        default_timezone: tzinfo = UTC,
        task_repo: TaskRepository | None = None,
        task_operation_service: TaskOperationService | None = None,
    ):
        self.clarification_repo = clarification_repo
        self.reminder_repo = reminder_repo
        self.reminder_service = reminder_service
        self.event_service = event_service
        self.default_timezone = default_timezone
        self.task_repo = task_repo
        self.task_operation_service = (
            task_operation_service
            or (
                TaskOperationService(task_repo, event_service)
                if task_repo is not None
                else None
            )
        )

    async def request_reminder_time(
        self,
        *,
        source_chat_id: str,
        reminder_id: str,
        reminder_title: str,
        source_message_id: str | None = None,
    ) -> ClarificationRequest:
        question = f"Khi nào anh muốn được nhắc về \"{reminder_title}\"?"
        request = ClarificationRequest(
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            entity_type="reminder",
            entity_id=reminder_id,
            field_name="remind_at",
            question=question,
        )
        created = await self.clarification_repo.create(request)
        await self.event_service.append_event(
            EventType.CLARIFICATION_REQUESTED,
            "clarification_request",
            created.id,
            {"entity_type": "reminder", "entity_id": reminder_id, "field_name": "remind_at"},
        )
        return created

    async def find_pending_for_chat(self, source_chat_id: str) -> ClarificationRequest | None:
        return await self.clarification_repo.find_pending_for_chat(source_chat_id)

    def is_answer_for_pending(
        self, pending: ClarificationRequest, answer_text: str
    ) -> bool:
        if pending.entity_type == "task" and pending.field_name.startswith("status|"):
            return _is_yes(answer_text) or _is_no(answer_text)
        return False

    async def cancel_pending_for_chat(self, source_chat_id: str, reason: str) -> bool:
        pending = await self.find_pending_for_chat(source_chat_id)
        if pending is None:
            return False
        await self.clarification_repo.cancel(pending.id, reason)
        await self.event_service.append_event(
            EventType.CLARIFICATION_FAILED,
            "clarification_request",
            pending.id,
            {"reason": "superseded_by_new_intent", "answer_text": reason},
        )
        return True

    async def answer_pending(self, source_chat_id: str, answer_text: str) -> ClarificationResult:
        pending = await self.find_pending_for_chat(source_chat_id)
        if pending is None:
            return ClarificationResult(handled=False, message="")
        if answer_text.strip().lower() in {"cancel", "skip", "never mind", "nevermind"}:
            await self.clarification_repo.cancel(pending.id, answer_text)
            await self.event_service.append_event(
                EventType.CLARIFICATION_FAILED,
                "clarification_request",
                pending.id,
                {"reason": "user_cancelled"},
            )
            return ClarificationResult(
                handled=True,
                message=_localized(
                    answer_text,
                    "Được, em hủy bỏ yêu cầu này.",
                    "Cancelled this request.",
                ),
            )

        if pending.entity_type == "task_recurrence_scope" and self.task_repo:
            task = await self.task_repo.get_by_id(pending.entity_id)
            if task is None:
                await self.clarification_repo.cancel(pending.id, answer_text)
                return ClarificationResult(True, "Task này không còn tồn tại.")
            due_str, requested_rule = pending.field_name.split("|", 2)[1:]
            due_at = datetime.fromisoformat(due_str)
            choice = _recurrence_scope_choice(answer_text, recurring=bool(task.recurrence_rule))
            if choice == "cancel":
                await self.clarification_repo.cancel(pending.id, answer_text)
                return ClarificationResult(True, "Dạ, em đã hủy thay đổi.")
            if choice is None:
                if _normalize_text(answer_text) == _normalize_text(task.title):
                    return ClarificationResult(
                        True,
                        f"Em đang áp dụng cho “{task.title}”. Anh chọn cách áp dụng giúp em nha.",
                    )
                return ClarificationResult(
                    True,
                    (
                        "Anh chọn “Chỉ kỳ này”, “Kỳ này và các kỳ sau” hoặc “Hủy” nha."
                        if task.recurrence_rule
                        else "Anh chọn “Chỉ lần này”, “Lặp hằng ngày” hoặc “Hủy” nha."
                    ),
                )
            await self.task_repo.update_due_at(task.id, due_at)
            if choice == "recurring":
                await self.task_repo.update_recurrence(task.id, requested_rule)
            await self.clarification_repo.resolve(pending.id, answer_text)
            await self.event_service.append_event(
                EventType.CLARIFICATION_RESOLVED,
                "clarification_request",
                pending.id,
                {
                    "entity_type": "task",
                    "entity_id": task.id,
                    "scope": choice,
                    "due_at": due_at.isoformat(),
                    "recurrence_rule": requested_rule if choice == "recurring" else task.recurrence_rule,
                },
            )
            local_due = due_at.astimezone(self.default_timezone).strftime("%H:%M %d/%m/%Y")
            if choice == "recurring":
                label = "hằng ngày" if requested_rule == "daily" else "hằng tuần"
                return ClarificationResult(
                    True,
                    f"Dạ, em đã đổi hạn “{task.title}” sang {local_due} và đặt lặp {label}.",
                )
            return ClarificationResult(
                True,
                f"Dạ, em chỉ đổi kỳ hiện tại của “{task.title}” sang {local_due}.",
            )

        # Check for task / status confirmations
        if pending.entity_type == "task_due_missing" and self.task_repo:
            due_at = parse_clarification_datetime(answer_text, default_timezone=self.default_timezone)
            if due_at is None:
                return ClarificationResult(
                    handled=True,
                    message=_localized(
                        answer_text,
                        "Em chưa hiểu thời gian đó. Anh nói kiểu 'hôm nay 19h' hoặc 'mai 9h' giúp em nha.",
                        "I could not understand that time. Please use a format like 'today 7pm' or 'tomorrow 9am'.",
                    ),
                )
            await self.task_repo.update_due_at(pending.entity_id, due_at)
            await self.clarification_repo.resolve(pending.id, answer_text)
            task = await self.task_repo.get_by_id(pending.entity_id)
            title = task.title if task else ""
            return ClarificationResult(
                handled=True,
                message=_localized(
                    answer_text,
                    f"Em đã đổi hạn task '{title}' sang {due_at.astimezone(self.default_timezone).strftime('%H:%M %d/%m/%Y')}.",
                    f"Updated the deadline for task '{title}' to {due_at.astimezone(self.default_timezone).strftime('%H:%M %d/%m/%Y')}.",
                ),
            )

        if pending.entity_type == "task" and self.task_repo:
            if pending.field_name.startswith("due_at|"):
                if _is_yes(answer_text):
                    due_str = pending.field_name.split("|", 1)[1]
                    due_at = datetime.fromisoformat(due_str)
                    await self.task_repo.update_due_at(pending.entity_id, due_at)
                    await self.clarification_repo.resolve(pending.id, answer_text)
                    task = await self.task_repo.get_by_id(pending.entity_id)
                    title = task.title if task else ""
                    return ClarificationResult(
                        handled=True,
                        message=_localized(
                            answer_text,
                            f"Đã rõ. Đã cập nhật hạn chót cho task: {title}.",
                            f"Got it. Updated deadline for task: {title}.",
                        ),
                    )
                elif _is_no(answer_text):
                    await self.clarification_repo.cancel(pending.id, answer_text)
                    return ClarificationResult(
                        handled=True,
                        message=_localized(
                            answer_text,
                            "Đã hủy bỏ cập nhật hạn chót.",
                            "Deadline update cancelled.",
                        ),
                    )
                else:
                    return ClarificationResult(
                        handled=True,
                        message=_localized(
                            answer_text,
                            "Vui lòng trả lời 'có' để xác nhận hoặc 'không' để hủy bỏ.",
                            "Please reply 'yes' to confirm or 'no' to cancel.",
                        ),
                    )

            elif pending.field_name.startswith("status|"):
                if _is_yes(answer_text):
                    status_val = pending.field_name.split("|", 1)[1]
                    next_task = None
                    next_created = False
                    if status_val == "done":
                        operation = await self.task_operation_service.complete(
                            pending.entity_id,
                            transition="completed_from_confirmation",
                        )
                        completed = operation.task
                        next_task = operation.next_task
                        next_created = operation.next_created
                    else:
                        await self.task_repo.update_status(
                            pending.entity_id, status_val
                        )
                        completed = await self.task_repo.get_by_id(
                            pending.entity_id
                        )
                    await self.clarification_repo.resolve(pending.id, answer_text)
                    task = await self.task_repo.get_by_id(pending.entity_id)
                    title = task.title if task else ""
                    if next_task is not None:
                        next_due = next_task.due_at.astimezone(
                            self.default_timezone
                        ).strftime("%H:%M %d/%m/%Y")
                        return ClarificationResult(
                            handled=True,
                            message=(
                                f"Dạ, em đã đánh dấu xong kỳ hiện tại của “{title}” "
                                f"và tạo kỳ kế tiếp lúc {next_due}."
                            ),
                        )
                    return ClarificationResult(
                        handled=True,
                        message=_localized(
                            answer_text,
                            f"Đã rõ. Đã đánh dấu xong task: {title}.",
                            f"Got it. Marked task as completed: {title}.",
                        ),
                    )
                elif _is_no(answer_text):
                    await self.clarification_repo.cancel(pending.id, answer_text)
                    return ClarificationResult(
                        handled=True,
                        message=_localized(
                            answer_text,
                            "Đã hủy bỏ đánh dấu xong.",
                            "Task completion cancelled.",
                        ),
                    )
                else:
                    return ClarificationResult(
                        handled=True,
                        message=_localized(
                            answer_text,
                            "Vui lòng trả lời 'có' để xác nhận hoặc 'không' để hủy bỏ.",
                            "Please reply 'yes' to confirm or 'no' to cancel.",
                        ),
                    )

        # Check for task selection confirmation (multiple options)
        if pending.entity_type in {"task_selection_done", "task_selection_due_update", "task_selection_rename"} and self.task_repo:
            if _is_no(answer_text):
                await self.clarification_repo.cancel(pending.id, answer_text)
                return ClarificationResult(
                    handled=True,
                    message=_localized(
                        answer_text,
                        "Đã hủy bỏ lựa chọn.",
                        "Selection cancelled.",
                    ),
                )
            
            task_ids = [tid.strip() for tid in pending.entity_id.split(",") if tid.strip()]
            num_tasks = len(task_ids)
            
            # Try to parse index
            choice = await _selection_choice(answer_text, task_ids, self.task_repo)
            if choice is not None:
                if 1 <= choice <= num_tasks:
                    target_task_id = task_ids[choice - 1]
                    task = await self.task_repo.get_by_id(target_task_id)
                    if not task:
                        await self.clarification_repo.cancel(pending.id, answer_text)
                        return ClarificationResult(
                            handled=True,
                            message=_localized(
                                answer_text,
                                "Không tìm thấy task đã chọn.",
                                "Selected task not found.",
                            ),
                        )
                    
                    if pending.entity_type == "task_selection_done":
                        completed, next_task, created = (
                            await self.task_repo.complete_and_schedule_next(target_task_id)
                        )
                        await self.event_service.append_event(
                            EventType.TASK_DONE,
                            "task",
                            target_task_id,
                            {
                                "transition": "completed_from_selection_confirmation",
                                "next_task_id": next_task.id if next_task else None,
                            },
                        )
                        if next_task is not None and created:
                            await self.event_service.append_event(
                                EventType.TASK_RECURRENCE_SCHEDULED,
                                "task",
                                next_task.id,
                                {
                                    "previous_task_id": target_task_id,
                                    "recurrence_rule": completed.recurrence_rule if completed else None,
                                },
                            )
                        await self.clarification_repo.resolve(pending.id, answer_text)
                        return ClarificationResult(
                            handled=True,
                            message=_localized(
                                answer_text,
                                f"Đã rõ. Đã đánh dấu xong task: {task.title}.",
                                f"Got it. Marked task as completed: {task.title}.",
                            ),
                        )
                    elif pending.entity_type == "task_selection_due_update":
                        due_str = pending.field_name.split("|", 1)[1]
                        due_at = datetime.fromisoformat(due_str)
                        await self.task_repo.update_due_at(target_task_id, due_at)
                        await self.clarification_repo.resolve(pending.id, answer_text)
                        return ClarificationResult(
                            handled=True,
                            message=_localized(
                                answer_text,
                                f"Đã rõ. Đã cập nhật hạn chót cho task: {task.title}.",
                                f"Got it. Updated deadline for task: {task.title}.",
                            ),
                        )
                    elif pending.entity_type == "task_selection_rename":
                        new_title = pending.field_name.split("|", 1)[1]
                        await self.task_repo.update_title(target_task_id, new_title)
                        await self.event_service.append_event(
                            EventType.NOTE_PROCESSED,
                            "task",
                            target_task_id,
                            {
                                "transition": "renamed_from_selection_confirmation",
                                "new_title": new_title,
                            },
                        )
                        await self.clarification_repo.resolve(pending.id, answer_text)
                        return ClarificationResult(
                            handled=True,
                            message=_localized(
                                answer_text,
                                f"Đã rõ. Đã sửa tên task thành: {new_title}.",
                                f"Got it. Renamed task to: {new_title}.",
                            ),
                        )
            
            return ClarificationResult(
                handled=True,
                message=_localized(
                    answer_text,
                    f"Vui lòng nhập số từ 1 đến {num_tasks} để chọn, hoặc trả lời 'không' để hủy.",
                    f"Please enter a number from 1 to {num_tasks} to select, or 'no' to cancel.",
                ),
            )

        # Check for task selection cancellation confirmation (correction feedback)
        if pending.entity_type == "task_selection_cancel" and self.task_repo:
            if _is_no(answer_text):
                await self.clarification_repo.cancel(pending.id, answer_text)
                return ClarificationResult(
                    handled=True,
                    message=_localized(
                        answer_text,
                        "Đã hủy bỏ.",
                        "Cancelled.",
                    ),
                )
            
            task_ids = [tid.strip() for tid in pending.entity_id.split(",") if tid.strip()]
            num_tasks = len(task_ids)
            
            # Try to parse index
            choice = await _selection_choice(answer_text, task_ids, self.task_repo)
            if choice is not None:
                if 1 <= choice <= num_tasks:
                    target_task_id = task_ids[choice - 1]
                    task = await self.task_repo.get_by_id(target_task_id)
                    if not task:
                        await self.clarification_repo.cancel(pending.id, answer_text)
                        return ClarificationResult(
                            handled=True,
                            message=_localized(
                                answer_text,
                                "Không tìm thấy task.",
                                "Task not found.",
                            ),
                        )
                    
                    await self.task_repo.update_status(target_task_id, "cancelled")
                    await self.event_service.append_event(
                        EventType.NOTE_PROCESSED,
                        "note",
                        pending.source_message_id or "system",
                        {"conversation_intent": "memory_correction", "cancelled_task_id": target_task_id},
                    )
                    await self.event_service.append_event(
                        EventType.USER_FEEDBACK_RECORDED,
                        "task",
                        target_task_id,
                        {"pattern": answer_text, "action": "cancel_task"},
                    )
                    write_feedback_signal("correction_feedback", answer_text, {"cancelled_task_id": target_task_id, "title": task.title})
                    await self.clarification_repo.resolve(pending.id, answer_text)
                    return ClarificationResult(
                        handled=True,
                        message=_localized(
                            answer_text,
                            f"Đã hủy task gần nhất: {task.title}",
                            f"Cancelled the recent task: {task.title}",
                        ),
                    )
            
            return ClarificationResult(
                handled=True,
                message=_localized(
                    answer_text,
                    f"Vui lòng nhập số từ 1 đến {num_tasks} để hủy task, hoặc trả lời 'không' để bỏ qua.",
                    f"Please enter a number from 1 to {num_tasks} to cancel the task, or 'no' to skip.",
                ),
            )

        if pending.entity_type != "reminder" or pending.field_name != "remind_at":
            await self.clarification_repo.cancel(pending.id, answer_text)
            return ClarificationResult(
                handled=True,
                message="Em chưa áp dụng được câu trả lời này, nên item gốc vẫn giữ nguyên.",
            )

        remind_at = parse_clarification_datetime(answer_text, default_timezone=self.default_timezone)
        if remind_at is None:
            await self.event_service.append_event(
                EventType.CLARIFICATION_FAILED,
                "clarification_request",
                pending.id,
                {"reason": "unparseable_answer", "answer_text": answer_text},
            )
            return ClarificationResult(
                handled=True,
                message="Em chưa hiểu thời gian đó. Anh thử kiểu 'hôm nay 14h', 'mai 9h', hoặc '2 tiếng sau'.",
            )

        await self.reminder_repo.update_remind_at(pending.entity_id, remind_at)
        await self.reminder_service.schedule_reminder(pending.entity_id)
        await self.clarification_repo.resolve(pending.id, answer_text)
        await self.event_service.append_event(
            EventType.CLARIFICATION_RESOLVED,
            "clarification_request",
            pending.id,
            {"entity_type": "reminder", "entity_id": pending.entity_id},
        )
        return ClarificationResult(
            handled=True,
            message=(
                "Đã rõ. Reminder được đặt lúc "
                f"{remind_at.astimezone(self.default_timezone).strftime('%Y-%m-%d %H:%M')}."
            ),
        )


def parse_clarification_datetime(
    value: str,
    now: datetime | None = None,
    default_timezone: tzinfo = UTC,
) -> datetime | None:
    parsed = parse_model_datetime(value)
    if parsed is not None:
        return parsed.astimezone(UTC)

    now = now or datetime.now(default_timezone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=default_timezone)
    lowered = _normalize_text(value)
    relative = _parse_relative_duration(lowered, now)
    if relative is not None:
        return relative.astimezone(UTC)

    target_date = None
    if "tomorrow" in lowered or "ngay mai" in lowered or _has_word(lowered, "mai"):
        target_date = now.date() + timedelta(days=1)
    elif "today" in lowered or "hom nay" in lowered or "toi nay" in lowered:
        target_date = now.date()
    else:
        target_date = _next_named_weekday(lowered, now)

    if target_date is None:
        return None

    parsed_time = _parse_time(lowered)
    if parsed_time is None:
        parsed_time = time(hour=9)

    return datetime.combine(target_date, parsed_time, tzinfo=now.tzinfo).astimezone(UTC)


def _parse_relative_duration(value: str, now: datetime) -> datetime | None:
    match = re.search(
        r"\b(?:in\s+)?(\d{1,3})\s*(minute|minutes|min|mins|hour|hours|hr|hrs|day|days|phut|tieng|gio|ngay)\s*(?:sau)?\b",
        value,
    )
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit in {"minute", "minutes", "min", "mins", "phut"}:
        return now + timedelta(minutes=amount)
    if unit in {"hour", "hours", "hr", "hrs", "tieng", "gio"}:
        return now + timedelta(hours=amount)
    return now + timedelta(days=amount)


def _parse_time(value: str) -> time | None:
    match = re.search(r"\b(\d{1,2})(?:[:h](\d{0,2}))?\s*(am|pm)?\b", value)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = match.group(3)
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour=hour, minute=minute)


def _next_named_weekday(value: str, now: datetime):
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
        "thu hai": 0,
        "thu ba": 1,
        "thu tu": 2,
        "thu nam": 3,
        "thu sau": 4,
        "thu bay": 5,
        "chu nhat": 6,
        "thu 2": 0,
        "thu 3": 1,
        "thu 4": 2,
        "thu 5": 3,
        "thu 6": 4,
        "thu 7": 5,
        "cn": 6,
    }
    for name, weekday in weekdays.items():
        if name in value:
            days_ahead = (weekday - now.weekday()) % 7
            return now.date() + timedelta(days=days_ahead or 7)
    return None


def _has_word(value: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", value) is not None


def _normalize_text(value: str) -> str:
    lowered = value.strip().lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def _is_yes(text: str) -> bool:
    normalized = _normalize_text(text)
    return normalized in {
        "co",
        "yes",
        "y",
        "dung",
        "dung roi",
        "ok",
        "okay",
        "u",
        "uh",
        "uhm",
        "dun",
        "yes sir",
        "chuan",
        "chuan roi",
        "dong y",
        "xac nhan",
        "xac nhan xong",
        "done",
        "xong",
        "xong roi",
        "u xong roi",
        "uh xong roi",
        "ok xong",
        "ok xong roi",
        "lam di",
        "cu lam di",
    }


def _is_no(text: str) -> bool:
    normalized = _normalize_text(text)
    return normalized in {"khong", "no", "n", "k", "huy", "cancel", "skip", "never mind", "nevermind"}


def _recurrence_scope_choice(text: str, *, recurring: bool) -> str | None:
    normalized = _normalize_text(text)
    if normalized in {"huy", "cancel", "3", "so 3", "lua chon 3"}:
        return "cancel"
    if recurring:
        if normalized in {"chi ky nay", "1", "so 1", "lua chon 1"}:
            return "current"
        if normalized in {
            "ky nay va cac ky sau",
            "2",
            "so 2",
            "lua chon 2",
        }:
            return "recurring"
    else:
        if normalized in {"chi lan nay", "1", "so 1", "lua chon 1"}:
            return "current"
        if normalized in {
            "lap hang ngay",
            "lap hang tuan",
            "2",
            "so 2",
            "lua chon 2",
        }:
            return "recurring"
    return None


async def _selection_choice(
    answer_text: str, task_ids: list[str], task_repo: TaskRepository
) -> int | None:
    match = re.search(r"\b(\d+)\b", answer_text)
    if match:
        return int(match.group(1))
    normalized = _normalize_text(answer_text)
    exact: list[int] = []
    for index, task_id in enumerate(task_ids, 1):
        task = await task_repo.get_by_id(task_id)
        if task and _normalize_text(task.title) == normalized:
            exact.append(index)
    return exact[0] if len(exact) == 1 else None


def _looks_vietnamese(raw_text: str) -> bool:
    normalized = _normalize_text(raw_text)
    return any(
        signal in normalized
        for signal in (
            "toi",
            "hom nay",
            "viec",
            "xong",
            "khong",
            "dung",
            "xoa",
            "luu",
            "nho",
            "ban than",
            "cai nay",
            "doi",
            "sua",
            "gio",
            "han",
            "han chot",
            "co",
            "mua",
            "lam",
            "da",
        )
    )


def _localized(raw_text: str, vi: str, en: str) -> str:
    return vi if _looks_vietnamese(raw_text) else en
