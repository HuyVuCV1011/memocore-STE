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
from memocore.services.reminder_service import ReminderService


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
    ):
        self.clarification_repo = clarification_repo
        self.reminder_repo = reminder_repo
        self.reminder_service = reminder_service
        self.event_service = event_service
        self.default_timezone = default_timezone
        self.task_repo = task_repo

    async def request_reminder_time(
        self,
        *,
        source_chat_id: str,
        reminder_id: str,
        reminder_title: str,
        source_message_id: str | None = None,
    ) -> ClarificationRequest:
        question = f"Khi nào bạn muốn được nhắc về \"{reminder_title}\"?"
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
                    "Được, mình hủy bỏ yêu cầu này.",
                    "Cancelled this request.",
                ),
            )

        # Check for task / status confirmations
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
                    await self.task_repo.update_status(pending.entity_id, status_val)
                    await self.event_service.append_event(
                        EventType.TASK_DONE,
                        "task",
                        pending.entity_id,
                        {"transition": "completed_from_confirmation"},
                    )
                    await self.clarification_repo.resolve(pending.id, answer_text)
                    task = await self.task_repo.get_by_id(pending.entity_id)
                    title = task.title if task else ""
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
        if pending.entity_type in {"task_selection_done", "task_selection_due_update"} and self.task_repo:
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
            match = re.search(r"\b(\d+)\b", answer_text)
            if match:
                choice = int(match.group(1))
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
                        await self.task_repo.update_status(target_task_id, "done")
                        await self.event_service.append_event(
                            EventType.TASK_DONE,
                            "task",
                            target_task_id,
                            {"transition": "completed_from_selection_confirmation"},
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
            match = re.search(r"\b(\d+)\b", answer_text)
            if match:
                choice = int(match.group(1))
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
                message="Mình chưa áp dụng được câu trả lời này, nên item gốc vẫn giữ nguyên.",
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
                message="Mình chưa hiểu thời gian đó. Thử kiểu 'hôm nay 14h', 'mai 9h', hoặc '2 tiếng sau'.",
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
    elif "today" in lowered or "hom nay" in lowered:
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
    return normalized in {"co", "yes", "y", "dung", "dung roi", "ok", "okay", "u", "uh", "dun", "yes sir"}


def _is_no(text: str) -> bool:
    normalized = _normalize_text(text)
    return normalized in {"khong", "no", "n", "k", "huy", "cancel", "skip", "never mind", "nevermind"}


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


def write_feedback_signal(intent: str, raw_text: str, context: dict) -> None:
    import json
    import os
    os.makedirs("data", exist_ok=True)
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "intent": intent,
        "raw_text": raw_text,
        "context": context
    }
    with open("data/user_feedback.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
