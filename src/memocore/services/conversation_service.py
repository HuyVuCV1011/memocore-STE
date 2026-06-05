from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
import re
import unicodedata

from memocore.adapters.storage.repositories import NoteRepository, TaskRepository
from memocore.domain.models import EventType, Note, Task, TaskStatus, ClarificationRequest
from memocore.domain.schemas import CaptureRequest, CaptureResponse
from memocore.services.capture_service import CaptureService
from memocore.services.clarification_service import parse_clarification_datetime
from memocore.services.event_service import EventService
from memocore.services.memory_service import MemoryService
from memocore.services.secretary_service import SecretaryService
from memocore.services.intent_classifier_service import IntentClassifierService


@dataclass(frozen=True)
class ConversationResult:
    intent: str
    reply: str
    captured: bool = False


class ConversationService:
    def __init__(
        self,
        capture_service: CaptureService,
        secretary_service: SecretaryService,
        note_repo: NoteRepository,
        task_repo: TaskRepository,
        memory_service: MemoryService,
        event_service: EventService,
        intent_classifier_service: IntentClassifierService | None = None,
    ):
        self.capture_service = capture_service
        self.secretary_service = secretary_service
        self.note_repo = note_repo
        self.task_repo = task_repo
        self.memory_service = memory_service
        self.event_service = event_service
        self.intent_classifier_service = intent_classifier_service

    def _deterministic_route(self, text: str) -> str | None:
        normalized = _normalize_text(text)
        if text.startswith("/"):
            command = text.split()[0].removeprefix("/")
            if command == "today":
                return "query_today"
            if command == "todays":
                return "query_today"
            if command == "tomorrow":
                return "query_tomorrow"
            if command == "memory":
                return "query_memory"
            if command == "tasks":
                return "query_tasks"
            if command == "reminders":
                return "query_reminders"
            if command == "projects":
                return "query_projects"
            if command == "waiting":
                return "query_tasks"

        if _is_new_task_followup(normalized):
            return "capture_previous_as_task"

        if _is_delete_all_tasks(normalized):
            return "delete_all_tasks"

        if _is_planning_or_checklist_capture(normalized):
            return "capture_note"
        
        # Check exact Vietnamese/English queries and commands
        if normalized in {
            "in ra ghi nho",
            "in ra cac ghi nho ve toi",
            "toi da luu gi ve ban than",
            "tôi đã lưu gì về bản thân",
            "what have you saved about me",
            "what have you saved about me?",
            "memory"
        }:
            return "query_memory"
            
        if normalized in {
            "hom nay toi can lam gi",
            "hom nay can lam gi",
            "today's agenda",
            "agenda today",
            "hôm nay tôi cần làm gì",
            "hôm nay cần làm gì"
        }:
            return "query_today"

        if _is_tomorrow_query(normalized):
            return "query_tomorrow"

        if _has_explicit_memory_signal(normalized):
            return "capture_memory"

        if normalized in {
            "what open tasks do i have",
            "tasks dang mo",
            "open tasks"
        }:
            return "query_tasks"
            
        return None

    async def handle_text(self, request: CaptureRequest) -> ConversationResult:
        # Layer 1: Deterministic Router
        intent = self._deterministic_route(request.raw_text)
        confidence = 1.0
        ambiguity_detected = False
        clarification_question = None
        target_entity_hints = None

        # Layer 2: AI Intent Classifier
        if intent is None:
            if self.intent_classifier_service is not None:
                try:
                    classification = await self.intent_classifier_service.classify(request.raw_text)
                    intent = classification.intent
                    confidence = classification.confidence
                    ambiguity_detected = classification.ambiguity_detected
                    clarification_question = classification.clarification_question
                    target_entity_hints = classification.target_entity_hints

                    safe_intents = {
                        "query_today",
                        "query_tomorrow",
                        "query_memory",
                        "query_tasks",
                        "query_reminders",
                        "query_projects",
                        "casual_or_noop",
                        "clarification_answer",
                        "needs_clarification",
                    }
                    if intent not in safe_intents:
                        if confidence < 0.6 or ambiguity_detected:
                            intent = "needs_clarification"
                except Exception:
                    fallback_intent = classify_intent(request.raw_text)
                    safe_intents = {
                        "query_today",
                        "query_tomorrow",
                        "query_memory",
                        "query_tasks",
                        "query_reminders",
                        "query_projects",
                        "casual_or_noop",
                        "clarification_answer",
                        "needs_clarification",
                    }
                    if fallback_intent not in safe_intents:
                        intent = "needs_clarification"
                    else:
                        intent = fallback_intent
            else:
                intent = classify_intent(request.raw_text)

        # Layer 3: Action Executor & Writer
        if intent == "capture_previous_as_task":
            response = await self._capture_previous_message(request)
            return ConversationResult(
                intent=intent,
                reply=format_capture_response(response),
                captured=True,
            )

        # Only capture/extraction when intent is clearly capture_task, capture_reminder, capture_memory or legacy capture_note
        if intent in {"capture_task", "capture_reminder", "capture_memory", "capture_note"}:
            response = await self.capture_service.capture(request)
            return ConversationResult(
                intent=intent,
                reply=format_capture_response(response),
                captured=True,
            )

        if intent == "casual_or_noop":
            return ConversationResult(
                intent=intent,
                reply=_localized(request.raw_text, "Mình nghe rồi.", "Got it."),
            )

        if intent == "needs_clarification":
            reply_msg = clarification_question or _localized(
                request.raw_text,
                "Mình chưa rõ ý bạn. Bạn có thể nói rõ hơn được không?",
                "I'm not sure what you mean. Could you please specify?",
            )
            return ConversationResult(intent=intent, reply=reply_msg)

        if intent == "mark_task_done" and _must_capture_instead_of_complete(request.raw_text):
            response = await self.capture_service.capture(request)
            return ConversationResult(
                intent="capture_note",
                reply=format_capture_response(response),
                captured=True,
            )

        note = await self._record_interaction(request, intent)

        if intent == "query_today":
            return ConversationResult(intent=intent, reply=await self.secretary_service.today())
        if intent == "query_tomorrow":
            return ConversationResult(intent=intent, reply=await self.secretary_service.tomorrow())
        if intent in {"query_tasks", "query_tasks_due"}:
            project_name = _extract_project_name(request.raw_text)
            if project_name:
                return ConversationResult(
                    intent="query_projects",
                    reply=await self.secretary_service.project_tasks(project_name),
                )
            return ConversationResult(intent=intent, reply=await self.secretary_service.tasks())
        if intent == "query_reminders":
            return ConversationResult(intent=intent, reply=await self.secretary_service.reminders())
        if intent == "query_projects":
            project_name = _extract_project_name(request.raw_text)
            if project_name:
                return ConversationResult(
                    intent=intent,
                    reply=await self.secretary_service.project_tasks(project_name),
                )
            return ConversationResult(intent=intent, reply=await self.secretary_service.projects())
        if intent == "query_memory":
            return ConversationResult(
                intent=intent,
                reply=await self.secretary_service.memories(bucket=_memory_bucket(request.raw_text)),
            )
        if intent == "mark_task_done":
            return ConversationResult(
                intent=intent,
                reply=await self._mark_task_done(request.raw_text, note.id, request, target_entity_hints, ambiguity_detected),
            )
        if intent == "delete_all_tasks":
            return ConversationResult(
                intent=intent,
                reply=await self._delete_all_tasks(request.raw_text),
            )
        if intent in {"update_task", "update_task_due"}:
            return ConversationResult(
                intent=intent,
                reply=await self._update_task_due(request.raw_text, note.id, request, target_entity_hints, ambiguity_detected),
            )
        if intent == "memory_delete":
            return ConversationResult(
                intent=intent,
                reply=await self._delete_memory(request.raw_text),
            )
        if intent in {"correction_feedback", "memory_correction"}:
            return ConversationResult(
                intent=intent,
                reply=await self._correct_recent_object(request.raw_text, note.id, request),
            )
        if intent == "clarification_answer":
            return ConversationResult(
                intent=intent,
                reply=_localized(
                    request.raw_text,
                    "Mình chưa có câu hỏi nào đang chờ câu trả lời.",
                    "I do not have a pending clarification right now.",
                ),
            )
        return ConversationResult(
            intent=intent,
            reply=_localized(request.raw_text, "Mình nghe rồi.", "Got it."),
        )

    async def _record_interaction(self, request: CaptureRequest, intent: str) -> Note:
        existing = await self.note_repo.find_by_source_message(
            request.source, request.source_chat_id, request.source_message_id
        )
        if existing:
            return existing
        note = Note(
            source=request.source,
            source_message_id=request.source_message_id,
            source_chat_id=request.source_chat_id,
            raw_text=request.raw_text,
        )
        await self.note_repo.create(note)
        await self.event_service.append_event(EventType.NOTE_CAPTURED, "note", note.id)
        await self.note_repo.update_processed(note.id, f"Conversation intent: {intent}", [intent])
        await self.event_service.append_event(
            EventType.NOTE_PROCESSED, "note", note.id, {"conversation_intent": intent}
        )
        return note

    async def _create_clarification_request(self, request: ClarificationRequest) -> ClarificationRequest:
        created = await self.capture_service.clarification_service.clarification_repo.create(request)
        await self.event_service.append_event(
            EventType.CLARIFICATION_REQUESTED,
            "clarification_request",
            created.id,
            {
                "entity_type": created.entity_type,
                "entity_id": created.entity_id,
                "field_name": created.field_name,
            },
        )
        return created

    async def _delete_all_tasks(self, raw_text: str) -> str:
        if not _is_delete_all_tasks(_normalize_text(raw_text)):
            return _localized(
                raw_text,
                "Bạn muốn hủy toàn bộ task đang mở phải không? Hãy nói rõ: 'xoá toàn bộ task đang có'.",
                "Do you want to cancel all open tasks? Please say: 'clear all open tasks'.",
            )
        active_tasks = await self.task_repo.list_active()
        for task in active_tasks:
            await self.task_repo.update_status(task.id, TaskStatus.CANCELLED.value)
            await self.event_service.append_event(
                EventType.USER_FEEDBACK_RECORDED,
                "task",
                task.id,
                {"pattern": raw_text, "action": "bulk_cancel_task"},
            )
        return _localized(
            raw_text,
            f"Đã hủy {len(active_tasks)} task đang mở.",
            f"Cancelled {len(active_tasks)} open task(s).",
        )

    async def _capture_previous_message(self, request: CaptureRequest) -> CaptureResponse:
        recent_notes = await self.note_repo.list_recent_by_chat(
            request.source,
            request.source_chat_id,
            limit=5,
        )
        current_message_id = request.source_message_id
        for note in recent_notes:
            if note.source_message_id == current_message_id:
                continue
            if _is_new_task_followup(_normalize_text(note.raw_text)):
                continue
            if not note.raw_text.strip():
                continue
            return await self.capture_service.capture(
                CaptureRequest(
                    source=request.source,
                    source_message_id=request.source_message_id,
                    source_chat_id=request.source_chat_id,
                    raw_text=note.raw_text,
                )
            )

        note = await self._record_interaction(request, "needs_clarification")
        return CaptureResponse(
            note_id=note.id,
            summary=_localized(
                request.raw_text,
                "Mình chưa thấy nội dung trước đó để tạo task mới. Bạn gửi lại nội dung task giúp mình nhé.",
                "I cannot find the previous message to turn into a new task. Please send the task text again.",
            ),
        )

    async def _mark_task_done(
        self,
        raw_text: str,
        note_id: str,
        request: CaptureRequest,
        target_entity_hints: str | None = None,
        ambiguity_detected: bool = False,
    ) -> str:
        query = target_entity_hints if target_entity_hints else _task_completion_query(raw_text)
        if not query:
            query = _task_completion_query(raw_text)
        matches = _ranked_task_matches(query, await self.task_repo.list_active())
        if not matches:
            return _localized(
                raw_text,
                "Mình chưa tìm thấy task khớp để đánh dấu xong. Bạn nói rõ tên task giúp mình nhé?",
                "I could not find a matching open task. Which task should I mark done?",
            )
        chat_id = request.source_chat_id or "system"
        msg_id = request.source_message_id
        if len(matches) > 1:
            titles = "\n".join(f"{index}. {task.title}" for index, task in enumerate(matches[:5], 1))
            question = _localized(
                raw_text,
                f"Mình thấy vài task có thể khớp. Bạn muốn đánh dấu task nào?\n{titles}",
                f"I found a few possible tasks. Which one should I mark done?\n{titles}",
            )
            if self.capture_service.clarification_service:
                clar_req = ClarificationRequest(
                    source_chat_id=chat_id,
                    source_message_id=msg_id,
                    entity_type="task_selection_done",
                    entity_id=",".join(task.id for task in matches[:5]),
                    field_name="status|done",
                    question=question,
                )
                await self._create_clarification_request(clar_req)
            return question

        task = matches[0]
        is_strong = _is_strong_match(query, task.title)
        if not is_strong or ambiguity_detected:
            question = _localized(
                raw_text,
                f"Bạn muốn đánh dấu xong task '{task.title}' phải không?",
                f"Do you want to mark task '{task.title}' as completed?",
            )
            if self.capture_service.clarification_service:
                clar_req = ClarificationRequest(
                    source_chat_id=chat_id,
                    source_message_id=msg_id,
                    entity_type="task",
                    entity_id=task.id,
                    field_name="status|done",
                    question=question,
                )
                await self._create_clarification_request(clar_req)
            return question

        await self.task_repo.update_status(task.id, TaskStatus.DONE.value)
        await self.event_service.append_event(
            EventType.TASK_DONE,
            "task",
            task.id,
            {"source_note_id": note_id, "transition": "completed_from_conversation"},
        )
        return _localized(raw_text, f"Đã đánh dấu xong: {task.title}", f"Marked done: {task.title}")

    async def _update_task_due(
        self,
        raw_text: str,
        note_id: str,
        request: CaptureRequest,
        target_entity_hints: str | None = None,
        ambiguity_detected: bool = False,
    ) -> str:
        due_at = _extract_task_due_at(raw_text, self.secretary_service.display_timezone)
        if due_at is None:
            return _localized(
                raw_text,
                "Mình hiểu là bạn muốn đổi hạn task, nhưng chưa đọc được giờ/hạn mới. Bạn nói lại kiểu 'hôm nay 17h' giúp mình nhé?",
                "I understand you want to change a task deadline, but I could not read the new time.",
            )
        query = target_entity_hints if target_entity_hints else _task_due_update_query(raw_text)
        if not query:
            query = _task_due_update_query(raw_text)
        matches = _ranked_task_matches(query, await self.task_repo.list_active())
        if not matches:
            return _localized(
                raw_text,
                "Mình chưa tìm thấy task khớp để đổi hạn. Bạn nói rõ tên task cần sửa giúp mình nhé?",
                "I could not find a matching open task. Which task should I update?",
            )
        chat_id = request.source_chat_id or "system"
        msg_id = request.source_message_id
        formatted_due = _format_local_datetime(due_at, self.secretary_service.display_timezone)
        if len(matches) > 1:
            titles = "\n".join(f"{index}. {task.title}" for index, task in enumerate(matches[:5], 1))
            question = _localized(
                raw_text,
                f"Mình thấy vài task có thể khớp. Bạn muốn đổi hạn task nào?\n{titles}",
                f"I found a few possible tasks. Which one should I update?\n{titles}",
            )
            if self.capture_service.clarification_service:
                clar_req = ClarificationRequest(
                    source_chat_id=chat_id,
                    source_message_id=msg_id,
                    entity_type="task_selection_due_update",
                    entity_id=",".join(task.id for task in matches[:5]),
                    field_name=f"due_at|{due_at.isoformat()}",
                    question=question,
                )
                await self._create_clarification_request(clar_req)
            return question

        task = matches[0]
        is_strong = _is_strong_match(query, task.title)
        if not is_strong or ambiguity_detected:
            question = _localized(
                raw_text,
                f"Bạn muốn đổi hạn task '{task.title}' thành {formatted_due} phải không?",
                f"Do you want to update the deadline for task '{task.title}' to {formatted_due}?",
            )
            if self.capture_service.clarification_service:
                clar_req = ClarificationRequest(
                    source_chat_id=chat_id,
                    source_message_id=msg_id,
                    entity_type="task",
                    entity_id=task.id,
                    field_name=f"due_at|{due_at.isoformat()}",
                    question=question,
                )
                await self._create_clarification_request(clar_req)
            return question

        await self.task_repo.update_due_at(task.id, due_at)
        await self.event_service.append_event(
            EventType.NOTE_PROCESSED,
            "task",
            task.id,
            {"source_note_id": note_id, "conversation_intent": "update_task_due"},
        )
        return _localized(
            raw_text,
            f"Đã đổi hạn: {task.title} -> {formatted_due}",
            f"Updated deadline: {task.title} -> {formatted_due}",
        )

    async def _delete_memory(self, raw_text: str) -> str:
        query = _memory_delete_query(raw_text)
        if query:
            deleted = await self.memory_service.delete_matching(query)
            if deleted:
                return _localized(
                    raw_text,
                    f"Đã xoá {deleted} memory khớp.",
                    f"Deleted {deleted} matching memory item(s).",
                )
        if _is_contextual_delete(raw_text):
            items = await self.memory_service.memory_repo.list_active()
            if len(items) == 1:
                await self.memory_service.reject(items[0].id)
                return _localized(
                    raw_text,
                    f"Đã bỏ memory gần nhất: {items[0].content}",
                    f"Rejected the recent memory: {items[0].content}",
                )
        return _localized(
            raw_text,
            "Bạn muốn mình xoá memory nào? Nói thêm vài từ trong nội dung đó giúp mình nhé.",
            "Which memory should I delete? Please include a few words from it.",
        )

    async def _correct_recent_object(self, raw_text: str, note_id: str, request: CaptureRequest) -> str:
        normalized = _normalize_text(raw_text)
        chat_id = request.source_chat_id or "system"
        msg_id = request.source_message_id

        if "khong phai task" in normalized or "not a task" in normalized:
            tasks = await self.task_repo.list_recent_active(limit=3)
            if len(tasks) == 1:
                task = tasks[0]
                await self.task_repo.update_status(task.id, TaskStatus.CANCELLED.value)
                await self.event_service.append_event(
                    EventType.NOTE_PROCESSED,
                    "note",
                    note_id,
                    {"conversation_intent": "memory_correction", "cancelled_task_id": task.id},
                )
                await self.event_service.append_event(
                    EventType.USER_FEEDBACK_RECORDED,
                    "task",
                    task.id,
                    {"pattern": raw_text, "action": "cancel_task"},
                )
                write_feedback_signal("correction_feedback", raw_text, {"cancelled_task_id": task.id, "title": task.title})
                return _localized(
                    raw_text,
                    f"Đã hủy task gần nhất: {task.title}",
                    f"Cancelled the recent task: {task.title}",
                )
            if len(tasks) > 1:
                titles = "\n".join(f"{index}. {task.title}" for index, task in enumerate(tasks, 1))
                question = _localized(
                    raw_text,
                    f"Mình thấy vài task gần đây. Cái nào không phải task?\n{titles}",
                    f"I found a few recent tasks. Which one is not a task?\n{titles}",
                )
                if self.capture_service.clarification_service:
                    clar_req = ClarificationRequest(
                        source_chat_id=chat_id,
                        source_message_id=msg_id,
                        entity_type="task_selection_cancel",
                        entity_id=",".join(t.id for t in tasks),
                        field_name="status|cancelled",
                        question=question,
                    )
                    await self._create_clarification_request(clar_req)
                return question

        if "khong phai memory" in normalized or "not a memory" in normalized:
            memories = await self.memory_service.memory_repo.list_active()
            if memories:
                mem = memories[0]
                await self.memory_service.reject(mem.id)
                await self.event_service.append_event(
                    EventType.USER_FEEDBACK_RECORDED,
                    "memory_item",
                    mem.id,
                    {"pattern": raw_text, "action": "reject_memory"},
                )
                write_feedback_signal("correction_feedback", raw_text, {"rejected_memory_id": mem.id, "content": mem.content})
                return _localized(
                    raw_text,
                    f"Đã bỏ memory gần nhất: {mem.content}",
                    f"Rejected the recent memory: {mem.content}",
                )

        if "dung luu" in normalized or "dont save" in normalized or "forget that" in normalized or "dung nho" in normalized:
            recent_tasks = await self.task_repo.list_recent_active(limit=1)
            recent_memories = await self.memory_service.memory_repo.list_active()
            
            task = recent_tasks[0] if recent_tasks else None
            mem = recent_memories[0] if recent_memories else None
            
            target = None
            if task and mem:
                if task.created_at >= mem.created_at:
                    target = "task"
                else:
                    target = "memory"
            elif task:
                target = "task"
            elif mem:
                target = "memory"
                
            if target == "task":
                await self.task_repo.update_status(task.id, TaskStatus.CANCELLED.value)
                await self.event_service.append_event(
                    EventType.USER_FEEDBACK_RECORDED,
                    "task",
                    task.id,
                    {"pattern": raw_text, "action": "cancel_task"},
                )
                write_feedback_signal("correction_feedback", raw_text, {"cancelled_task_id": task.id, "title": task.title})
                return _localized(
                    raw_text,
                    f"Đã hủy task gần nhất: {task.title}",
                    f"Cancelled the recent task: {task.title}",
                )
            elif target == "memory":
                await self.memory_service.reject(mem.id)
                await self.event_service.append_event(
                    EventType.USER_FEEDBACK_RECORDED,
                    "memory_item",
                    mem.id,
                    {"pattern": raw_text, "action": "reject_memory"},
                )
                write_feedback_signal("correction_feedback", raw_text, {"rejected_memory_id": mem.id, "content": mem.content})
                return _localized(
                    raw_text,
                    f"Đã bỏ memory gần nhất: {mem.content}",
                    f"Rejected the recent memory: {mem.content}",
                )

        return _localized(
            raw_text,
            "Mình chưa đủ ngữ cảnh để sửa đúng mục. Bạn nói rõ task hoặc memory nào cần sửa nhé?",
            "I need a little more context. Which task or memory should I correct?",
        )


def _is_past_or_completed(normalized: str) -> bool:
    tokens = normalized.split()
    return any(token in tokens for token in ("da", "xong", "done", "finished", "completed"))


def _has_explicit_task_signal(normalized: str) -> bool:
    if _is_past_or_completed(normalized):
        return False
    vi_signals = {"can", "phai", "nhiem vu", "cong viec", "task", "todo", "job", "soan", "mua", "lam", "viet", "nop", "gui", "goi"}
    en_signals = {"need to", "have to", "write", "send", "call", "buy", "draft", "prepare", "submit"}
    tokens = normalized.split()
    return any(sig in tokens for sig in vi_signals) or any(sig in normalized for sig in en_signals)


def _has_explicit_reminder_signal(normalized: str) -> bool:
    return any(sig in normalized for sig in ("nhac", "remind"))


def _has_explicit_memory_signal(normalized: str) -> bool:
    return any(
        sig in normalized
        for sig in (
            "ten la", "name is", "thich", "like", "birthday", "sinh nhat",
            "nho rang", "ghi nho rang", "remember that", "please remember that", "keep in mind that",
            "vo toi", "chong toi", "con toi", "bo toi", "me toi", "my wife", "my husband",
            "my son", "my daughter", "my father", "my mother", "sinh ngay", "born on"
        )
    )


def _has_explicit_capture_signal(normalized: str) -> bool:
    # Do not treat "note" or "notes" as a capture signal on its own
    tokens = normalized.split()
    if "notes" in tokens or "note" in tokens:
        # Only capture if there is a clear task/reminder signal
        if not _has_explicit_task_signal(normalized) and not _has_explicit_reminder_signal(normalized):
            return False

    return (
        _has_explicit_task_signal(normalized) or
        _has_explicit_reminder_signal(normalized) or
        _has_explicit_memory_signal(normalized) or
        any(sig in normalized for sig in ("save that", "store that", "keep in mind", "luu rang", "ghi nho rang", "nho rang"))
    )


def _is_new_task_followup(normalized: str) -> bool:
    return normalized in {
        "task moi",
        "task moi do",
        "day la task moi",
        "la task moi",
        "new task",
        "this is a new task",
        "that is a new task",
    }


def _is_planning_or_checklist_capture(normalized: str) -> bool:
    has_planning_signal = any(
        signal in normalized
        for signal in (
            "toi can check",
            "can check",
            "check nhanh",
            "can kiem tra",
            "kiem tra nhanh",
            "can ra soat",
            "ra soat",
            "can xem",
            "xem qua",
            "review",
        )
    )
    if not has_planning_signal:
        return False
    work_signals = (
        "xong chua",
        "tien do",
        "du an",
        "project",
        "sap xep",
        "nhan su",
        "bai tap",
        "hoc vien",
        "cv",
        "pc",
        "mindx",
    )
    return any(signal in normalized for signal in work_signals)


def _is_delete_all_tasks(normalized: str) -> bool:
    has_delete_signal = any(
        signal in normalized
        for signal in (
            "xoa toan bo",
            "huy toan bo",
            "xoa het",
            "huy het",
            "delete all",
            "clear all",
            "cancel all",
        )
    )
    has_task_signal = any(signal in normalized for signal in ("task", "tasks", "viec", "cong viec"))
    return has_delete_signal and has_task_signal


def _must_capture_instead_of_complete(raw_text: str) -> bool:
    normalized = _normalize_text(raw_text)
    return _is_planning_or_checklist_capture(normalized) or _is_completion_status_question(normalized)


def classify_intent(raw_text: str) -> str:
    normalized = _normalize_text(raw_text)
    if not normalized:
        return "casual_or_noop"
    if _is_casual(normalized):
        return "casual_or_noop"
    if _is_clarification_answer(normalized):
        return "clarification_answer"
    if _is_memory_delete(normalized):
        return "memory_delete"
    if _is_delete_all_tasks(normalized):
        return "delete_all_tasks"
    if _is_task_due_update(normalized):
        return "update_task_due"
    if _is_memory_correction(normalized):
        return "memory_correction"
    if _is_planning_or_checklist_capture(normalized):
        return "capture_note"
    if _is_task_completion(normalized):
        return "mark_task_done"
    if _is_today_query(normalized):
        return "query_today"
    if _is_tomorrow_query(normalized):
        return "query_tomorrow"
    if _is_memory_query(normalized):
        return "query_memory"
    if _is_project_query(normalized):
        return "query_projects"
    if _is_reminder_query(normalized):
        return "query_reminders"
    if _is_task_query(normalized):
        return "query_tasks"
    
    # Check for explicit capture signals
    if _has_explicit_capture_signal(normalized):
        return "capture_note"
        
    # Default fallback for unknown vague text
    action_keywords = {"xong", "done", "finished", "completed", "mua", "lam", "doi", "sua", "cap nhat", "xoa", "forget", "delete"}
    tokens = set(normalized.split())
    if tokens & action_keywords:
        return "needs_clarification"
    return "casual_or_noop"


def format_capture_response(response: CaptureResponse) -> str:
    text = (
        f"Captured: {response.summary}\n"
        f"{response.tasks_created} task(s) | "
        f"{response.tasks_completed} completed | "
        f"{response.reminders_created} reminder(s) | "
        f"{response.memories_created} memory item(s)"
    )
    if response.memories_deleted:
        text += f" | {response.memories_deleted} memory deleted"
    if response.errors:
        text += "\nExtraction had issues, raw note saved."
    if response.clarification_question:
        text += f"\n{response.clarification_question}"
    return text


def _ranked_task_matches(query: str, tasks: list[Task]) -> list[Task]:
    query_tokens = _meaningful_tokens(query)
    if not query_tokens:
        return []
    scored: list[tuple[float, Task]] = []
    for task in tasks:
        title_tokens = _meaningful_tokens(task.title)
        if not title_tokens:
            continue
        overlap = query_tokens & title_tokens
        score = len(overlap) / max(1, min(len(query_tokens), len(title_tokens)))
        if score >= 0.5 or (len(overlap) >= 2 and len(query_tokens) <= 3):
            scored.append((score, task))
    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.2:
        return [task for _, task in scored]
    return [scored[0][1]] if scored else []


def _task_completion_query(raw_text: str) -> str:
    cleanup = (
        "toi da lam xong",
        "da lam xong",
        "lam xong",
        "da xong",
        "da",
        "xong",
        "hoan thanh",
        "da hoan thanh",
        "i finished",
        "finished",
        "completed",
        "done",
        "viec",
        "task",
    )
    query = _normalize_text(raw_text)
    for word in cleanup:
        query = query.replace(word, " ")
    return " ".join(query.split())


def _task_due_update_query(raw_text: str) -> str:
    query = _normalize_text(raw_text)
    cleanup_patterns = (
        r"\b\d{1,2}(?:[:h]\d{2})?\s*(?:am|pm)?\b",
        r"\b(?:doi|sua|cap nhat|chinh|dat)\b",
        r"\b(?:lai|gio|han|deadline|due|due date|han chot|thanh|la|luc|vao|cho|task|viec|hom nay|today)\b",
    )
    for pattern in cleanup_patterns:
        query = re.sub(pattern, " ", query)
    return " ".join(query.split())


def _extract_task_due_at(raw_text: str, display_timezone) -> datetime | None:
    parsed = parse_clarification_datetime(raw_text, default_timezone=display_timezone)
    if parsed is not None:
        return parsed.astimezone(UTC)

    normalized = _normalize_text(raw_text)
    parsed_time = _parse_clock_time(normalized)
    if parsed_time is None:
        return None
    now = datetime.now(display_timezone)
    return datetime.combine(now.date(), parsed_time, tzinfo=display_timezone).astimezone(UTC)


def _memory_delete_query(raw_text: str) -> str:
    cleanup = (
        "xoa memory lien quan den",
        "xoa thong tin lien quan den",
        "xoa memory",
        "xoa thong tin",
        "dung luu cai nay",
        "dung luu",
        "dung nho cai nay",
        "dung nho",
        "forget memory about",
        "forget memory",
        "forget that",
        "delete memory about",
        "delete memory",
        "delete that",
        "cai nay",
        "lien quan den",
        "lien quan",
    )
    query = _normalize_text(raw_text)
    for word in cleanup:
        query = query.replace(word, " ")
    return " ".join(query.split())


def _extract_project_name(raw_text: str) -> str | None:
    normalized = _normalize_text(raw_text)
    match = re.search(r"\bproject\s+(.+?)(?:\s+(?:con|co|dang|chua|tasks?|viec|open|unfinished)\b|$)", normalized)
    if match:
        return match.group(1).strip()
    return None


def _memory_bucket(raw_text: str) -> str | None:
    normalized = _normalize_text(raw_text)
    if any(signal in normalized for signal in ("ban than", "profile", "about me", "ve toi")):
        return "profile"
    if "project" in normalized:
        return "project"
    return None


def _is_today_query(normalized: str) -> bool:
    return "hom nay" in normalized and any(
        signal in normalized for signal in ("can lam gi", "lich", "agenda", "viec gi", "today")
    )


def _is_tomorrow_query(normalized: str) -> bool:
    return any(day in normalized for day in ("mai", "ngay mai", "tomorrow")) and any(
        signal in normalized for signal in ("can lam gi", "lich", "agenda", "viec gi", "what")
    )


def _is_task_query(normalized: str) -> bool:
    return any(
        signal in normalized
        for signal in (
            "task nao",
            "viec nao",
            "con gi chua xong",
            "tasks",
            "open tasks",
            "what do i need to do",
        )
    )


def _is_reminder_query(normalized: str) -> bool:
    return any(signal in normalized for signal in ("reminder", "reminders", "nhac nho", "lich nhac"))


def _is_project_query(normalized: str) -> bool:
    return "project" in normalized and any(
        signal in normalized
        for signal in ("con gi", "chua xong", "tasks", "viec", "dang mo", "open", "unfinished")
    )


def _is_memory_query(normalized: str) -> bool:
    vi_query_signals = {
        "toi da luu gi", "da luu gi", "luu gi ve ban than",
        "memory cua toi", "co memory gi", "ghi nho cua toi",
        "cac ghi nho", "in ra cac ghi nho", "danh sach ghi nho", "xem ghi nho"
    }
    en_query_signals = {
        "what do you remember", "what have you saved", "about me",
        "show me my notes", "list my notes", "my notes", "show notes",
        "list notes", "notes", "show memory", "list memory", "view memory"
    }
    if normalized in {"memory", "ghi nho", "notes"}:
        return True
    return any(sig in normalized for sig in vi_query_signals) or any(sig in normalized for sig in en_query_signals)


def _is_task_completion(normalized: str) -> bool:
    if _is_completion_status_question(normalized):
        return False
    return bool(re.search(r"\bda\s+.+\s+xong\b", normalized)) or any(
        signal in normalized
        for signal in (
            "da lam xong",
            "lam xong",
            "da xong",
            "hoan thanh",
            "da hoan thanh",
            "i finished",
            "finished",
            "completed",
            "done",
        )
    )


def _is_completion_status_question(normalized: str) -> bool:
    return any(
        signal in normalized
        for signal in (
            "xong chua",
            "da lam xong chua",
            "lam xong chua",
            "done yet",
            "finished yet",
            "completed yet",
        )
    )


def _is_task_due_update(normalized: str) -> bool:
    has_update_signal = any(
        signal in normalized
        for signal in (
            "doi lai gio",
            "doi gio",
            "doi han",
            "sua gio",
            "sua han",
            "cap nhat gio",
            "cap nhat han",
            "han chot",
            "deadline",
            "due date",
        )
    )
    return has_update_signal and _parse_clock_time(normalized) is not None


def _is_memory_correction(normalized: str) -> bool:
    return any(
        signal in normalized
        for signal in (
            "khong phai task",
            "cai nay khong phai",
            "sua lai",
            "sua la",
            "dinh chinh",
            "actually",
            "not a task",
            "correct that",
        )
    )


def _is_memory_delete(normalized: str) -> bool:
    return any(
        signal in normalized
        for signal in (
            "dung luu",
            "dung nho",
            "xoa memory",
            "xoa thong tin",
            "forget memory",
            "forget that",
            "delete memory",
        )
    )


def _is_contextual_delete(raw_text: str) -> bool:
    normalized = _normalize_text(raw_text)
    return any(signal in normalized for signal in ("dung luu cai nay", "dung nho cai nay", "forget that"))


def _is_clarification_answer(normalized: str) -> bool:
    return normalized in {"co", "khong", "yes", "no"} or bool(re.fullmatch(r"\d{1,2}(:\d{2})?", normalized))


def _is_casual(normalized: str) -> bool:
    return normalized in {"hi", "hello", "hey", "chao", "xin chao", "ok", "okay", "cam on", "thanks"}


def _parse_clock_time(value: str) -> time | None:
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


def _format_local_datetime(value: datetime, display_timezone) -> str:
    local = value.astimezone(display_timezone)
    return local.strftime("%H:%M %d/%m/%Y")


def _meaningful_tokens(value: str) -> set[str]:
    stopwords = {
        "toi",
        "da",
        "dang",
        "can",
        "lam",
        "xong",
        "hoan",
        "thanh",
        "project",
        "du",
        "an",
        "the",
        "a",
        "an",
        "and",
        "done",
        "finished",
        "completed",
        "complete",
        "viec",
        "task",
    }
    tokens = set(_normalize_text(value).split())
    meaningful = {token for token in tokens if token not in stopwords and len(token) > 1}
    return meaningful or tokens


def _is_strong_match(query: str, title: str) -> bool:
    q_tokens = _clean_task_name_query(query)
    t_tokens = _clean_task_name_query(title)
    if not q_tokens or not t_tokens:
        return False
    return q_tokens.issubset(t_tokens)


def _clean_task_name_query(text: str) -> set[str]:
    tokens = _meaningful_tokens(text)
    time_words = {
        "chot", "han", "deadline", "due", "time", "gio", "ngay", "mai", 
        "hom", "nay", "chieu", "sang", "toi", "trua", "clock", "am", "pm",
        "lai", "doi", "sua", "cap", "nhat"
    }
    cleaned = set()
    for token in tokens:
        if token in time_words:
            continue
        if any(char.isdigit() for char in token):
            continue
        cleaned.add(token)
    return cleaned


def _localized(raw_text: str, vi: str, en: str) -> str:
    return vi if _looks_vietnamese(raw_text) else en


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


def _normalize_text(value: str) -> str:
    lowered = value.lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    ascii_text = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join("".join(char if char.isalnum() else " " for char in ascii_text).split())


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
