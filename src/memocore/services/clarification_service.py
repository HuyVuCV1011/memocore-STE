from __future__ import annotations

from collections.abc import Callable
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Any

from memocore.adapters.storage.repositories import (
    ClarificationRequestRepository,
    CommitmentRepository,
    FollowUpRepository,
    ReminderRepository,
    TaskRepository,
    parse_model_datetime,
)
from memocore.domain.models import ClarificationRequest, EventType, FeedbackSignal
from memocore.services.event_service import EventService
from memocore.services.reminder_service import ReminderService
from memocore.services.daily_closeout_service import decode_closeout_field
from memocore.services.task_operation_service import (
    RecurrenceBacklog,
    TaskOperationService,
)
from memocore.services.task_batch import (
    TaskBatchSnapshot,
    decode_batch_field,
    encode_batch_field,
)


@dataclass(frozen=True)
class ClarificationResult:
    handled: bool
    message: str
    reply_markup: Any = None


class ClarificationService:
    def __init__(
        self,
        clarification_repo: ClarificationRequestRepository,
        reminder_repo: ReminderRepository,
        reminder_service: ReminderService,
        event_service: EventService,
        default_timezone: tzinfo = UTC,
        task_repo: TaskRepository | None = None,
        followup_repo: FollowUpRepository | None = None,
        commitment_repo: CommitmentRepository | None = None,
        task_operation_service: TaskOperationService | None = None,
        confirmation_ttl: timedelta = timedelta(minutes=15),
        now_provider: Callable[[], datetime] | None = None,
        default_reminder_time: time = time(hour=9),
    ):
        self.clarification_repo = clarification_repo
        self.reminder_repo = reminder_repo
        self.reminder_service = reminder_service
        self.event_service = event_service
        self.default_timezone = default_timezone
        self.task_repo = task_repo
        self.followup_repo = followup_repo
        self.commitment_repo = commitment_repo
        self.confirmation_ttl = confirmation_ttl
        self.now_provider = now_provider or (lambda: datetime.now(UTC))
        self.default_reminder_time = default_reminder_time
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

    async def request_recurrence_backlog(
        self,
        source_chat_id: str,
        backlogs: list[RecurrenceBacklog],
        *,
        source_message_id: str | None = None,
    ) -> ClarificationRequest | None:
        if not backlogs:
            return None
        payload = [
            {
                "task_id": backlog.next_task_id,
                "missed_count": backlog.missed_count,
                "next_future_due": backlog.next_future_due.isoformat(),
                "expected_updated_at": backlog.expected_updated_at.isoformat(),
            }
            for backlog in backlogs
        ]
        total_missed = sum(item.missed_count for item in backlogs)
        question = (
            f"Có {total_missed} kỳ định kỳ đã lỡ trên {len(backlogs)} task. "
            "Anh muốn giữ từng kỳ hay bỏ qua tới kỳ tiếp theo trong tương lai?"
        )
        request = await self.clarification_repo.create(
            ClarificationRequest(
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
                entity_type="recurrence_backlog_policy",
                entity_id=",".join(item.next_task_id for item in backlogs),
                field_name="backlog|" + json.dumps(payload, separators=(",", ":")),
                question=question,
            )
        )
        await self.event_service.append_event(
            EventType.CLARIFICATION_REQUESTED,
            "clarification_request",
            request.id,
            {
                "entity_type": request.entity_type,
                "task_count": len(backlogs),
                "missed_count": total_missed,
            },
        )
        return request

    def is_answer_for_pending(
        self, pending: ClarificationRequest, answer_text: str
    ) -> bool:
        if pending.entity_type == "task" and pending.field_name.startswith("status|"):
            return _is_yes(answer_text) or _is_no(answer_text)
        if pending.entity_type.startswith("task_selection_"):
            normalized = _normalize_text(answer_text)
            if _is_no(answer_text) or re.search(r"\b\d+\b", normalized):
                return True
            # Option titles are embedded in the persisted question.  Treat an
            # exact-name reply as part of the active selection instead of a new
            # capture intent.
            return bool(normalized) and normalized in _normalize_text(pending.question)
        if pending.entity_type == "task_recurrence_scope":
            return _recurrence_scope_choice(answer_text, recurring=True) is not None
        if pending.entity_type == "recurrence_backlog_policy":
            return _recurrence_backlog_choice(answer_text) is not None
        if pending.entity_type == "task_bulk_done":
            return (
                _is_yes(answer_text)
                or _is_no(answer_text)
                or _is_edit(answer_text)
                or bool(re.search(r"\b\d+\b", answer_text))
            )
        if pending.entity_type == "daily_closeout":
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
        generic_cancel = answer_text.strip().lower() in {
            "cancel",
            "skip",
            "never mind",
            "nevermind",
        }
        backlog_skip = (
            pending.entity_type == "recurrence_backlog_policy"
            and _recurrence_backlog_choice(answer_text) == "skip"
        )
        if generic_cancel and not backlog_skip:
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

        if pending.entity_type == "recurrence_backlog_policy" and self.task_repo:
            if pending.created_at < self.now_provider().astimezone(UTC) - self.confirmation_ttl:
                await self.clarification_repo.cancel(pending.id, answer_text)
                return ClarificationResult(
                    True,
                    "Lựa chọn backlog này đã hết hạn. Các kỳ hiện tại vẫn được giữ nguyên.",
                )
            choice = _recurrence_backlog_choice(answer_text)
            if choice is None:
                return ClarificationResult(
                    True,
                    "Anh chọn “Giữ từng kỳ” hoặc “Bỏ qua kỳ đã lỡ” nhé.",
                    _recurrence_backlog_keyboard(),
                )
            if choice == "keep":
                await self.clarification_repo.resolve(pending.id, answer_text)
                return ClarificationResult(
                    True,
                    "Dạ, em giữ nguyên từng kỳ đã lỡ để anh xử lý lần lượt.",
                )

            payload = _backlog_payload(pending.field_name)
            moved = 0
            skipped = 0
            async with self.task_repo.database.transaction():
                for item in payload:
                    task = await self.task_repo.get_by_id(item["task_id"])
                    expected_updated_at = datetime.fromisoformat(
                        item["expected_updated_at"]
                    )
                    if (
                        task is None
                        or str(task.status)
                        not in {"candidate", "open", "waiting", "blocked"}
                        or task.updated_at != expected_updated_at
                    ):
                        skipped += 1
                        continue
                    future_due = datetime.fromisoformat(item["next_future_due"])
                    if not await self.task_repo.reschedule_recurrence_occurrence(
                        task.id,
                        future_due,
                    ):
                        skipped += 1
                        continue
                    moved += 1
                    await self.event_service.append_event(
                        EventType.TASK_RECURRENCE_BACKLOG_SKIPPED,
                        "task",
                        task.id,
                        {
                            "missed_count": item["missed_count"],
                            "next_future_due": future_due.isoformat(),
                        },
                    )
                await self.clarification_repo.resolve(pending.id, answer_text)
            message = f"Đã bỏ qua backlog cho {moved} task định kỳ."
            if skipped:
                message += f" Bỏ qua {skipped} task đã thay đổi."
            return ClarificationResult(True, message)

        if pending.entity_type == "task_recurrence_scope" and self.task_repo:
            task = await self.task_repo.get_by_id(pending.entity_id)
            if task is None:
                await self.clarification_repo.cancel(pending.id, answer_text)
                return ClarificationResult(True, "Task này không còn tồn tại.")
            field_parts = pending.field_name.split("|")
            due_str, requested_rule = field_parts[1:3]
            duration_minutes = _duration_from_field_parts(field_parts[3:])
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
            async with self.task_repo.database.transaction():
                await self.task_repo.update_due_at(task.id, due_at)
                if duration_minutes is not None:
                    await self.task_repo.update_duration(task.id, duration_minutes)
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
                        "recurrence_rule": (
                            requested_rule
                            if choice == "recurring"
                            else task.recurrence_rule
                        ),
                        "duration_minutes": duration_minutes,
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

        if pending.entity_type == "daily_closeout" and self.task_repo:
            if _is_no(answer_text):
                await self.clarification_repo.cancel(pending.id, answer_text)
                return ClarificationResult(
                    True, "Dạ, em giữ nguyên các task, follow-up và commitment."
                )
            selected_groups = _closeout_selected_groups(answer_text)
            if selected_groups is None:
                return ClarificationResult(
                    True,
                    "Anh xác nhận hoặc hủy closeout này giúp em nha.",
                )
            payload = decode_closeout_field(pending.field_name)
            if payload is None:
                await self.clarification_repo.cancel(pending.id, answer_text)
                return ClarificationResult(
                    True,
                    "Closeout preview này bị lỗi định dạng nên em chưa thay đổi gì.",
                )
            due_at = datetime.fromisoformat(payload["due_at"])
            moved = 0
            skipped = 0
            followups_moved = 0
            followups_skipped = 0
            commitments_moved = 0
            commitments_skipped = 0
            async with self.task_repo.database.transaction():
                if "tasks" in selected_groups:
                    for item in payload.get("tasks", []):
                        task = await self.task_repo.get_by_id(item["id"])
                        expected_updated_at = datetime.fromisoformat(item["updated_at"])
                        if (
                            task is None
                            or str(task.status) != item["status"]
                            or task.updated_at != expected_updated_at
                        ):
                            skipped += 1
                            continue
                        await self.task_repo.update_due_at(task.id, due_at)
                        moved += 1
                if self.followup_repo and "followups" in selected_groups:
                    for item in payload.get("followups", []):
                        followup = await self.followup_repo.get_by_id(item["id"])
                        expected_updated_at = datetime.fromisoformat(item["updated_at"])
                        if (
                            followup is None
                            or str(followup.status) != item["status"]
                            or followup.updated_at != expected_updated_at
                        ):
                            followups_skipped += 1
                            continue
                        await self.followup_repo.update_due_at(followup.id, due_at)
                        followups_moved += 1
                elif not self.followup_repo and "followups" in selected_groups:
                    followups_skipped += len(payload.get("followups", []))
                if self.commitment_repo and "commitments" in selected_groups:
                    for item in payload.get("commitments", []):
                        commitment = await self.commitment_repo.get_by_id(item["id"])
                        expected_updated_at = datetime.fromisoformat(item["updated_at"])
                        if (
                            commitment is None
                            or str(commitment.status) != item["status"]
                            or commitment.updated_at != expected_updated_at
                        ):
                            commitments_skipped += 1
                            continue
                        await self.commitment_repo.update_due_at(commitment.id, due_at)
                        commitments_moved += 1
                elif not self.commitment_repo and "commitments" in selected_groups:
                    commitments_skipped += len(payload.get("commitments", []))
                await self.clarification_repo.resolve(pending.id, answer_text)
                await self.event_service.append_event(
                    EventType.DAILY_CLOSEOUT_APPLIED,
                    "clarification_request",
                    pending.id,
                    {
                        "task_count": moved,
                        "skipped_task_count": skipped,
                        "followup_count": followups_moved,
                        "skipped_followup_count": followups_skipped,
                        "commitment_count": commitments_moved,
                        "skipped_commitment_count": commitments_skipped,
                        "due_at": due_at.isoformat(),
                        "selected_groups": sorted(selected_groups),
                        "items": {
                            group: payload.get(group, [])
                            for group in sorted(selected_groups)
                        },
                    },
                )
            local_due = due_at.astimezone(self.default_timezone).strftime("%H:%M %d/%m/%Y")
            message = (
                f"Dạ, em đã chuyển {moved} task, {followups_moved} follow-up "
                f"và {commitments_moved} commitment sang {local_due}."
            )
            skipped_total = skipped + followups_skipped + commitments_skipped
            if skipped_total:
                message += f" Bỏ qua {skipped_total} mục đã thay đổi sau preview."
            return ClarificationResult(True, message)

        # Check for task / status confirmations
        if pending.entity_type == "task_bulk_done" and self.task_repo:
            now = self.now_provider().astimezone(UTC)
            if pending.created_at < now - self.confirmation_ttl:
                await self.clarification_repo.cancel(pending.id, answer_text)
                return ClarificationResult(
                    handled=True,
                    message=(
                        "Xác nhận này đã hết hạn vì danh sách task có thể đã thay đổi. "
                        "Anh mở lại danh sách rồi yêu cầu hoàn thành lần nữa nhé."
                    ),
                )
            task_ids = [
                task_id.strip()
                for task_id in pending.entity_id.split(",")
                if task_id.strip()
            ]
            snapshots, selected_ids = decode_batch_field(pending.field_name)
            tasks = [
                task
                for task_id in task_ids
                if (task := await self.task_repo.get_by_id(task_id)) is not None
            ]
            if not snapshots:
                snapshots = [TaskBatchSnapshot.from_task(task) for task in tasks]
            if _is_edit(answer_text):
                selected_ids = [task.id for task in tasks]
                question = _batch_selection_question(tasks, set(selected_ids))
                await self.clarification_repo.update_prompt(
                    pending.id,
                    field_name=encode_batch_field(
                        snapshots,
                        selected_ids=selected_ids,
                    ),
                    question=question,
                )
                return ClarificationResult(
                    True,
                    question,
                    _batch_selection_keyboard(tasks, set(selected_ids)),
                )
            if selected_ids is not None and not _is_yes(answer_text) and not _is_no(
                answer_text
            ):
                indexes = {
                    int(value)
                    for value in re.findall(r"\b\d+\b", answer_text)
                    if 1 <= int(value) <= len(tasks)
                }
                if not indexes:
                    return ClarificationResult(
                        True,
                        "Anh chọn ít nhất một số task trong danh sách nhé.",
                        _batch_selection_keyboard(tasks, set(selected_ids)),
                    )
                if len(indexes) == 1:
                    index = next(iter(indexes))
                    task_id = tasks[index - 1].id
                    selected = set(selected_ids)
                    if task_id in selected:
                        selected.remove(task_id)
                    else:
                        selected.add(task_id)
                else:
                    selected = {tasks[index - 1].id for index in indexes}
                selected_ids = [
                    task.id for task in tasks if task.id in selected
                ]
                question = _batch_selection_question(tasks, set(selected_ids))
                await self.clarification_repo.update_prompt(
                    pending.id,
                    field_name=encode_batch_field(
                        snapshots,
                        selected_ids=selected_ids,
                    ),
                    question=question,
                )
                return ClarificationResult(
                    True,
                    question,
                    _batch_selection_keyboard(tasks, set(selected_ids)),
                )
            if _is_yes(answer_text):
                requested_ids = selected_ids if selected_ids is not None else task_ids
                snapshot_by_id = {
                    snapshot.task_id: snapshot for snapshot in snapshots
                }
                eligible_ids: list[str] = []
                stale_count = 0
                for task_id in requested_ids:
                    task = await self.task_repo.get_by_id(task_id)
                    snapshot = snapshot_by_id.get(task_id)
                    if snapshot is not None and not snapshot.matches(task):
                        stale_count += 1
                        continue
                    if task is None:
                        stale_count += 1
                        continue
                    eligible_ids.append(task_id)
                if not eligible_ids:
                    await self.clarification_repo.cancel(pending.id, answer_text)
                    return ClarificationResult(
                        True,
                        "Không còn task nào giữ nguyên từ lúc preview nên em chưa thay đổi gì.",
                    )
                result = await self.task_operation_service.complete_many(
                    eligible_ids,
                    transition="completed_from_bulk_confirmation",
                    now=now,
                )
                await self.clarification_repo.resolve(pending.id, answer_text)
                skipped = stale_count + len(result.skipped_task_ids)
                backlog_request = await self.request_recurrence_backlog(
                    pending.source_chat_id,
                    [
                        operation.recurrence_backlog
                        for operation in result.results
                        if operation.recurrence_backlog is not None
                    ],
                    source_message_id=pending.source_message_id,
                )
                backlog_text = (
                    f"\n{backlog_request.question}" if backlog_request else ""
                )
                return ClarificationResult(
                    handled=True,
                    message=(
                        f"Đã rõ. Đã đánh dấu xong "
                        f"{len(result.completed_tasks)} task."
                        + (
                            f" Bỏ qua {skipped} task không còn ở trạng thái đang mở."
                            if skipped
                            else ""
                        )
                        + backlog_text
                    ),
                    reply_markup=_batch_result_keyboard(
                        result.batch_event_id,
                        include_backlog=backlog_request is not None,
                    ),
                )
            if _is_no(answer_text):
                await self.clarification_repo.cancel(pending.id, answer_text)
                return ClarificationResult(
                    handled=True,
                    message="Dạ, em đã hủy đánh dấu xong các task này.",
                )
            return ClarificationResult(
                handled=True,
                message="Anh trả lời 'có' để xác nhận hoặc 'không' để hủy nhé.",
            )

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
                    field_parts = pending.field_name.split("|")
                    due_str = field_parts[1]
                    duration_minutes = _duration_from_field_parts(field_parts[2:])
                    due_at = datetime.fromisoformat(due_str)
                    await self.task_repo.update_due_at(pending.entity_id, due_at)
                    if duration_minutes is not None:
                        await self.task_repo.update_duration(
                            pending.entity_id, duration_minutes
                        )
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
                    if status_val == "done":
                        operation = await self.task_operation_service.complete(
                            pending.entity_id,
                            transition="completed_from_confirmation",
                            now=self.now_provider().astimezone(UTC),
                        )
                        next_task = operation.next_task
                    else:
                        await self.task_repo.update_status(
                            pending.entity_id, status_val
                        )
                    await self.clarification_repo.resolve(pending.id, answer_text)
                    task = await self.task_repo.get_by_id(pending.entity_id)
                    title = task.title if task else ""
                    backlog_request = (
                        await self.request_recurrence_backlog(
                            pending.source_chat_id,
                            [operation.recurrence_backlog],
                            source_message_id=pending.source_message_id,
                        )
                        if status_val == "done"
                        and operation.recurrence_backlog is not None
                        else None
                    )
                    if next_task is not None:
                        next_due = next_task.due_at.astimezone(
                            self.default_timezone
                        ).strftime("%H:%M %d/%m/%Y")
                        message = (
                            f"Dạ, em đã đánh dấu xong kỳ hiện tại của “{title}” "
                            f"và tạo kỳ kế tiếp lúc {next_due}."
                        )
                        if backlog_request is not None:
                            message += f"\n{backlog_request.question}"
                        return ClarificationResult(
                            handled=True,
                            message=message,
                            reply_markup=(
                                _recurrence_backlog_keyboard()
                                if backlog_request is not None
                                else None
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
                        operation = await self.task_operation_service.complete(
                            target_task_id,
                            transition="completed_from_selection_confirmation",
                            now=self.now_provider().astimezone(UTC),
                        )
                        await self.clarification_repo.resolve(pending.id, answer_text)
                        backlog_request = (
                            await self.request_recurrence_backlog(
                                pending.source_chat_id,
                                [operation.recurrence_backlog],
                                source_message_id=pending.source_message_id,
                            )
                            if operation.recurrence_backlog is not None
                            else None
                        )
                        message = f"Đã rõ. Đã đánh dấu xong task: {task.title}."
                        if backlog_request is not None:
                            message += f"\n{backlog_request.question}"
                        return ClarificationResult(
                            handled=True,
                            message=message,
                            reply_markup=(
                                _recurrence_backlog_keyboard()
                                if backlog_request is not None
                                else None
                            ),
                        )
                    elif pending.entity_type == "task_selection_due_update":
                        field_parts = pending.field_name.split("|")
                        due_str = field_parts[1]
                        duration_minutes = _duration_from_field_parts(field_parts[2:])
                        due_at = datetime.fromisoformat(due_str)
                        await self.task_repo.update_due_at(target_task_id, due_at)
                        if duration_minutes is not None:
                            await self.task_repo.update_duration(
                                target_task_id, duration_minutes
                            )
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
                        operation = await self.task_operation_service.rename(
                            target_task_id,
                            new_title,
                            transition="renamed_from_selection_confirmation",
                        )
                        await self.clarification_repo.resolve(pending.id, answer_text)
                        return ClarificationResult(
                            handled=True,
                            message=(
                                f"Dạ, em đã đổi tên task thành “{new_title}”."
                                + (
                                    f" Em cũng đã đồng bộ {operation.linked_artifacts_updated} lịch liên quan."
                                    if operation.linked_artifacts_updated
                                    else ""
                                )
                            ),
                        )

            await self.event_service.append_event(
                EventType.CLARIFICATION_FAILED,
                "clarification_request",
                pending.id,
                {
                    "reason": "invalid_selection",
                    "answer_text": answer_text,
                    "options": num_tasks,
                },
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
                    
                    if self.task_operation_service is not None:
                        await self.task_operation_service.cancel(target_task_id)
                    else:
                        await self.task_repo.update_status(target_task_id, "cancelled")
                    await self.event_service.append_event(
                        EventType.NOTE_PROCESSED,
                        "note",
                        pending.source_message_id or "system",
                        {"conversation_intent": "memory_correction", "cancelled_task_id": target_task_id},
                    )
                    await self.event_service.record_feedback(
                        FeedbackSignal.CORRECTION,
                        "task",
                        target_task_id,
                        source_chat_id=pending.source_chat_id,
                        source_message_id=pending.source_message_id,
                        action="cancel_misclassified_task_after_clarification",
                    )
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

        remind_at = parse_clarification_datetime(
            answer_text,
            default_timezone=self.default_timezone,
            default_time=self.default_reminder_time,
        )
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
    default_time: time = time(hour=9),
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
    if "day after tomorrow" in lowered or "ngay mot" in lowered:
        target_date = now.date() + timedelta(days=2)
    elif "tomorrow" in lowered or "ngay mai" in lowered or _has_word(lowered, "mai"):
        target_date = now.date() + timedelta(days=1)
    elif "today" in lowered or "hom nay" in lowered or "toi nay" in lowered:
        target_date = now.date()
    else:
        target_date = _next_named_weekday(lowered, now)

    if target_date is None:
        return None

    parsed_time = _parse_time(lowered)
    if parsed_time is None:
        parsed_time = default_time

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


def _closeout_selected_groups(text: str) -> set[str] | None:
    if _is_yes(text):
        return {"tasks", "followups", "commitments"}
    normalized = _normalize_text(text)
    if normalized.startswith("closeout:apply:"):
        groups = {
            group
            for group in normalized.removeprefix("closeout:apply:").split(",")
            if group in {"tasks", "followups", "commitments"}
        }
        return groups or None
    aliases = {
        "chi task": {"tasks"},
        "task": {"tasks"},
        "chi follow up": {"followups"},
        "chi follow-up": {"followups"},
        "follow up": {"followups"},
        "follow-up": {"followups"},
        "chi commitment": {"commitments"},
        "commitment": {"commitments"},
    }
    return aliases.get(normalized)


def _is_edit(text: str) -> bool:
    return _normalize_text(text) in {
        "chon lai",
        "edit",
        "sua lua chon",
    }


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


def _recurrence_backlog_choice(text: str) -> str | None:
    normalized = _normalize_text(text)
    if normalized in {"1", "giu", "giu tung ky", "keep", "keep missed"}:
        return "keep"
    if normalized in {
        "2",
        "bo qua",
        "bo qua ky da lo",
        "skip",
        "skip missed",
    }:
        return "skip"
    return None


def _backlog_payload(field_name: str) -> list[dict[str, Any]]:
    if not field_name.startswith("backlog|"):
        return []
    try:
        payload = json.loads(field_name.split("|", 1)[1])
    except (TypeError, ValueError):
        return []
    return payload if isinstance(payload, list) else []


def _recurrence_backlog_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Giữ từng kỳ", callback_data="clar:scope:1"),
                InlineKeyboardButton(
                    "Bỏ qua kỳ đã lỡ",
                    callback_data="clar:scope:2",
                ),
            ]
        ]
    )


def _batch_selection_question(tasks, selected_ids: set[str]) -> str:
    lines = ["Chọn các task cần hoàn thành:"]
    for index, task in enumerate(tasks, 1):
        marker = "☑" if task.id in selected_ids else "☐"
        lines.append(f"{marker} {index}. {task.title}")
    lines.append("Nhấn task để chọn/bỏ chọn, sau đó xác nhận.")
    return "\n".join(lines)


def _batch_selection_keyboard(tasks, selected_ids: set[str]):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = [
        [
            InlineKeyboardButton(
                f"{'☑' if task.id in selected_ids else '☐'} {task.title[:32]}",
                callback_data=f"clar:scope:{index}",
            )
        ]
        for index, task in enumerate(tasks, 1)
    ]
    rows.append(
        [
            InlineKeyboardButton("Xác nhận", callback_data="clar:scope:yes"),
            InlineKeyboardButton("Hủy", callback_data="clar:scope:no"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _batch_result_keyboard(
    event_id: str | None,
    *,
    include_backlog: bool,
):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    if include_backlog:
        rows.append(
            [
                InlineKeyboardButton("Giữ từng kỳ", callback_data="clar:scope:1"),
                InlineKeyboardButton(
                    "Bỏ qua kỳ đã lỡ",
                    callback_data="clar:scope:2",
                ),
            ]
        )
    if event_id is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    "↩ Hoàn tác batch",
                    callback_data=f"work:u:e:{event_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows) if rows else None


def _duration_from_field_parts(parts: list[str]) -> int | None:
    for part in parts:
        if not part.startswith("duration="):
            continue
        try:
            value = int(part.split("=", 1)[1])
        except ValueError:
            return None
        return value if value > 0 else None
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
    # MemoCore V4 currently has one Vietnamese assistant voice.  Guessing the
    # reply language from a short answer such as "1" or "task 1" caused the
    # assistant to switch to English mid-conversation.
    return vi
