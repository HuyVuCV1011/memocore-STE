from __future__ import annotations

from typing import Any
from dataclasses import dataclass
from datetime import UTC, datetime, time
import re
import unicodedata

from memocore.adapters.storage.repositories import NoteRepository, TaskRepository
from memocore.domain.models import EventType, Note, Reminder, Task, TaskStatus, ClarificationRequest
from memocore.domain.schemas import CaptureRequest, CaptureResponse
from memocore.services.capture_service import CaptureService
from memocore.services.clarification_service import parse_clarification_datetime
from memocore.services.event_service import EventService
from memocore.services.memory_service import MemoryService
from memocore.services.secretary_service import SecretaryService
from memocore.services.intent_classifier_service import IntentClassifierService
from memocore.services.knowledge_query_service import KnowledgeQueryService


@dataclass(frozen=True)
class ConversationResult:
    intent: str
    reply: str
    captured: bool = False
    reply_markup: Any = None


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
        knowledge_query_service: KnowledgeQueryService | None = None,
    ):
        self.capture_service = capture_service
        self.secretary_service = secretary_service
        self.note_repo = note_repo
        self.task_repo = task_repo
        self.memory_service = memory_service
        self.event_service = event_service
        self.intent_classifier_service = intent_classifier_service
        self.knowledge_query_service = knowledge_query_service

    def _deterministic_route(self, text: str) -> str | None:
        action_tag = _trailing_action_tag(text)
        if action_tag in {"li", "linkedin"}:
            return "capture_note"
        if action_tag in {"task", "t"}:
            return "capture_task"
        if action_tag in {"remind", "r"}:
            return "capture_reminder"
        if action_tag in {"mem", "m"}:
            return "capture_memory"

        normalized = _normalize_text(text)
        if text.startswith("/"):
            command = text.split()[0].removeprefix("/").lower()
            if command in {"linkedin", "li"}:
                return "capture_note"
            if command in {"task", "t"}:
                return "capture_task"
            if command in {"mem", "m"}:
                return "capture_memory"
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
            if command == "people":
                return "query_people"
            if command == "commitments":
                return "query_commitments"
            if command in {"person", "project", "context"}:
                return "query_context"
            if command == "prep":
                return "query_meeting_prep"
            if command == "waiting":
                return "query_tasks"

        if _is_new_task_followup(normalized):
            return "capture_previous_as_task"

        if _is_followup_detail_request(normalized):
            return "query_context"

        if _is_profile_question(normalized):
            return "query_profile"

        if _is_assistant_identity_question(normalized):
            return "query_assistant_identity"

        if _is_ste_mindx_compare_query(normalized):
            return "query_ste_mindx_compare"

        if _is_assign_task_to_person(normalized):
            return "assign_task_to_person"

        if _is_task_check_reminder(normalized):
            return "create_task_check_reminder"

        if _is_person_task_query(normalized):
            return "query_person_tasks"

        if _is_delete_all_tasks(normalized):
            return "delete_all_tasks"

        if _is_task_due_update(normalized) or _is_task_due_update_followup(normalized):
            return "update_task_due"

        if _is_task_completion(normalized):
            return "mark_task_done"

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

        if _is_project_query(normalized):
            return "query_projects"

        if _is_task_query(normalized):
            return "query_tasks"

        if _is_knowledge_question(normalized) or _looks_like_question(normalized):
            return "query_context"
            
        return None

    async def handle_text(self, request: CaptureRequest) -> ConversationResult:
        # Check if the text is just a slash command without content
        raw_text_lower = request.raw_text.strip().lower()
        empty_capture_inputs = {
            "/li",
            "/linkedin",
            "/task",
            "/t",
            "/mem",
            "/m",
            "#li",
            "#linkedin",
            "#task",
            "#t",
            "#remind",
            "#r",
            "#mem",
            "#m",
            "#ste",
            "#mindx",
        }
        if raw_text_lower in empty_capture_inputs:
            prompts = {
                "/li": "Bạn muốn lưu ghi chú gì? Hãy nhập nội dung sau lệnh. Ví dụ: `/li 5 bài học quản lý`",
                "/linkedin": "Bạn muốn lưu ghi chú gì? Hãy nhập nội dung sau lệnh. Ví dụ: `/linkedin 5 bài học quản lý`",
                "/task": "Bạn muốn tạo công việc gì? Hãy nhập nội dung sau lệnh. Ví dụ: `/task Hoàn thành slide STE`",
                "/t": "Bạn muốn tạo công việc gì? Hãy nhập nội dung sau lệnh. Ví dụ: `/t Hoàn thành slide STE`",
                "/mem": "Bạn muốn lưu ký ức gì? Hãy nhập nội dung sau lệnh. Ví dụ: `/mem Khởi nghiệp STE từ 2024`",
                "/m": "Bạn muốn lưu ký ức gì? Hãy nhập nội dung sau lệnh. Ví dụ: `/m Khởi nghiệp STE từ 2024`"
            }
            prompt = prompts.get(
                raw_text_lower,
                "Nút này gửi hashtag thành một tin nhắn riêng nên chưa có nội dung để lưu. "
                "Hãy gửi nội dung và đặt hashtag ở cuối, ví dụ: `Giao Nguyên làm outline #task`.",
            )
            return ConversationResult(
                intent="empty_command",
                reply=prompt,
            )

        # Check for task rename request before any other routing/classification
        rename_info = _parse_task_rename(request.raw_text)
        if rename_info:
            old_query, new_title, show_tasks = rename_info
            note = await self._record_interaction(request, "rename_task")
            reply = await self._rename_task(old_query, new_title, note.id, request, show_tasks)
            return ConversationResult(
                intent="rename_task",
                reply=reply,
            )

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
                        "query_profile",
                        "query_assistant_identity",
                        "query_ste_mindx_compare",
                        "query_person_tasks",
                        "query_tasks",
                        "query_reminders",
            "query_projects",
            "query_people",
            "query_commitments",
            "query_context",
            "query_meeting_prep",
            "casual_or_noop",
                        "clarification_answer",
                        "needs_clarification",
                        "assign_task_to_person",
                        "create_task_check_reminder",
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
                        "query_profile",
                        "query_assistant_identity",
                        "query_ste_mindx_compare",
                        "query_person_tasks",
                        "query_tasks",
                        "query_reminders",
                        "query_projects",
                        "query_people",
                        "query_commitments",
                        "query_context",
                        "query_meeting_prep",
                        "casual_or_noop",
                        "clarification_answer",
                        "needs_clarification",
                        "assign_task_to_person",
                        "create_task_check_reminder",
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
            # Strip slash command from raw_text and append corresponding hashtag
            raw_text = request.raw_text
            if raw_text.startswith("/"):
                parts = raw_text.split(maxsplit=1)
                if len(parts) > 1:
                    cmd = parts[0].lower().removeprefix("/")
                    content = parts[1].strip()
                    hashtag = ""
                    if cmd in {"linkedin", "li"}:
                        hashtag = " #li"
                    elif cmd in {"task", "t"}:
                        hashtag = " #task"
                    elif cmd in {"mem", "m"}:
                        hashtag = " #mem"
                    raw_text = content + hashtag
                    request = CaptureRequest(
                        source=request.source,
                        source_message_id=request.source_message_id,
                        source_chat_id=request.source_chat_id,
                        raw_text=raw_text,
                    )
            response = await self.capture_service.capture(request)
            reply_markup = None
            if response.entity_suggestion_ids:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup

                reply_markup = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "Xác nhận liên kết người/dự án",
                                callback_data=f"entity:p:{response.entity_suggestion_ids[0]}",
                            )
                        ]
                    ]
                )
            return ConversationResult(
                intent=intent,
                reply=format_capture_response(response),
                captured=True,
                reply_markup=reply_markup,
            )

        if intent == "casual_or_noop":
            if _looks_like_question(_normalize_text(request.raw_text)):
                intent = "query_context"
            elif _is_meaningful_statement(request.raw_text):
                note = await self._record_interaction(request, intent)
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("✍️ Lưu LinkedIn", callback_data=f"tag_prompt:li:{note.id}"),
                            InlineKeyboardButton("📋 Tạo Task", callback_data=f"tag_prompt:task:{note.id}"),
                        ],
                        [
                            InlineKeyboardButton("🧠 Lưu Ký ức", callback_data=f"tag_prompt:mem:{note.id}"),
                            InlineKeyboardButton("❌ Bỏ qua", callback_data=f"tag_prompt:ignore:{note.id}"),
                        ]
                    ]
                )
                return ConversationResult(
                    intent="tag_prompt",
                    reply="Mình thấy nội dung này có thể hữu ích nhưng chưa rõ bạn muốn lưu vào đâu. Bạn muốn lưu làm gì?",
                    reply_markup=keyboard,
                )
            else:
                return ConversationResult(
                    intent=intent,
                    reply=_localized(request.raw_text, "Mình nghe rồi.", "Got it."),
                )

        if intent == "query_context" and _is_followup_detail_request(_normalize_text(request.raw_text)):
            expanded = await self._expand_followup_query(request)
            if expanded:
                request = CaptureRequest(
                    source=request.source,
                    source_message_id=request.source_message_id,
                    source_chat_id=request.source_chat_id,
                    raw_text=expanded,
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

        knowledge_intents = {"query_context"}
        if intent in knowledge_intents and self.knowledge_query_service is not None:
            try:
                reply = await self.knowledge_query_service.answer(request.raw_text)
                return ConversationResult(intent=intent, reply=reply)
            except Exception:
                pass

        if intent == "query_today":
            return ConversationResult(intent=intent, reply=await self.secretary_service.today())
        if intent == "query_tomorrow":
            return ConversationResult(intent=intent, reply=await self.secretary_service.tomorrow())
        if intent == "query_assistant_identity":
            return ConversationResult(intent=intent, reply=_assistant_identity_answer())
        if intent == "query_ste_mindx_compare":
            return ConversationResult(intent=intent, reply=_ste_mindx_compare_answer())
        if intent == "query_person_tasks":
            return ConversationResult(intent=intent, reply=await self._answer_person_tasks(request.raw_text))
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
            return ConversationResult(
                intent=intent,
                reply=await self.secretary_service.projects(scope=_extract_project_scope(request.raw_text)),
            )
        if intent == "query_people":
            return ConversationResult(intent=intent, reply=await self.secretary_service.people())
        if intent == "query_commitments":
            return ConversationResult(intent=intent, reply=await self.secretary_service.commitments())
        if intent == "query_context":
            if self.knowledge_query_service is not None:
                try:
                    return ConversationResult(
                        intent=intent,
                        reply=await self.knowledge_query_service.answer(request.raw_text),
                    )
                except Exception:
                    pass
            query = _extract_context_query(request.raw_text)
            if not query:
                return ConversationResult(
                    intent=intent,
                    reply="Bạn muốn xem context nào? Nói tên person hoặc project giúp mình nhé.",
                )
            return ConversationResult(intent=intent, reply=await self.secretary_service.context(query))
        if intent == "query_meeting_prep":
            query = _extract_context_query(request.raw_text)
            if not query:
                return ConversationResult(
                    intent=intent,
                    reply="Bạn muốn chuẩn bị meeting nào? Nói tên person hoặc project giúp mình nhé.",
                )
            return ConversationResult(intent=intent, reply=await self.secretary_service.meeting_prep(query))
        if intent == "query_memory":
            return ConversationResult(
                intent=intent,
                reply=await self.secretary_service.memories(bucket=_memory_bucket(request.raw_text)),
            )
        if intent == "query_profile":
            return ConversationResult(
                intent=intent,
                reply=await self._answer_profile_question(request.raw_text),
            )
        if intent == "mark_task_done":
            return ConversationResult(
                intent=intent,
                reply=await self._mark_task_done(request.raw_text, note.id, request, target_entity_hints, ambiguity_detected),
            )
        if intent == "assign_task_to_person":
            return ConversationResult(
                intent=intent,
                reply=await self._assign_task_to_person(request.raw_text, note.id),
                captured=True,
            )
        if intent == "create_task_check_reminder":
            return ConversationResult(
                intent=intent,
                reply=await self._create_task_check_reminder(request.raw_text, note.id),
                captured=True,
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
            reply=_localized(
                request.raw_text,
                "Mình chưa xử lý được yêu cầu này. Bạn nói rõ hơn bạn muốn hỏi hay muốn mình làm gì nhé?",
                "I could not handle this request. Could you clarify what you want me to answer or do?",
            ),
        )

    async def _expand_followup_query(self, request: CaptureRequest) -> str | None:
        if not request.source_chat_id:
            return None
        recent_notes = await self.note_repo.list_recent_by_chat(
            request.source,
            request.source_chat_id,
            limit=8,
        )
        for note in recent_notes:
            if note.source_message_id == request.source_message_id:
                continue
            if any(
                tag in note.tags
                for tag in (
                    "query_context",
                    "query_profile",
                    "query_memory",
                    "query_projects",
                    "query_tasks",
                    "query_people",
                    "query_commitments",
                    "query_reminders",
                )
            ):
                if _is_more_remaining_followup(_normalize_text(request.raw_text)):
                    return f"{note.raw_text}. Nếu còn dữ liệu liên quan chưa nêu, hãy bổ sung phần còn lại."
                return f"{note.raw_text}. Hãy trả lời cụ thể hơn."
        return None

    async def _rename_task(
        self,
        old_query: str,
        new_title: str,
        note_id: str,
        request: CaptureRequest,
        show_tasks: bool = False,
    ) -> str:
        matches = _ranked_task_matches(old_query, await self.task_repo.list_active())
        if not matches:
            return _localized(
                request.raw_text,
                f"Mình không tìm thấy task đang mở nào khớp với '{old_query}'.",
                f"I couldn't find any open task matching '{old_query}'.",
            )

        chat_id = request.source_chat_id or "system"
        msg_id = request.source_message_id
        if len(matches) > 1:
            titles = "\n".join(f"{index}. {task.title}" for index, task in enumerate(matches[:5], 1))
            question = _localized(
                request.raw_text,
                f"Mình thấy vài task có thể khớp. Bạn muốn đổi tên task nào?\n{titles}",
                f"I found a few possible tasks. Which one should I rename?\n{titles}",
            )
            if self.capture_service.clarification_service:
                clar_req = ClarificationRequest(
                    source_chat_id=chat_id,
                    source_message_id=msg_id,
                    entity_type="task_selection_rename",
                    entity_id=",".join(task.id for task in matches[:5]),
                    field_name=f"title|{new_title}",
                    question=question,
                )
                await self._create_clarification_request(clar_req)
            return question

        task = matches[0]
        await self.task_repo.update_title(task.id, new_title)
        await self.event_service.append_event(
            EventType.NOTE_PROCESSED,
            "task",
            task.id,
            {
                "source_note_id": note_id,
                "conversation_intent": "rename_task",
                "old_title": task.title,
                "new_title": new_title,
            },
        )

        reply = _localized(
            request.raw_text,
            f"Đã sửa tiêu đề task:\n'{task.title}' thành '{new_title}'",
            f"Renamed task:\n'{task.title}' to '{new_title}'",
        )

        if show_tasks:
            tasks_list = await self.secretary_service.tasks()
            reply += f"\n\n{tasks_list}"

        return reply

    async def _answer_profile_question(self, raw_text: str) -> str:
        memories = await self.memory_service.memory_repo.list_active()
        profile_memories = [item.content for item in memories if str(item.bucket) == "profile"]
        if profile_memories:
            return (
                "Bạn đang có hai bối cảnh công việc chính:\n"
                "- Tại MindX, bạn làm quản lý và vận hành trong mảng Vận hành Giảng dạy, "
                "với hai vai trò TEGL+ và TOM.\n"
                "- Tại STE, bạn là người sáng lập và trực tiếp vận hành, tập trung vào dữ liệu/BI, "
                "xây dựng hệ thống, sản phẩm công nghệ, AI và giáo dục.\n"
                "Ngoài ra, bạn còn tham gia giảng dạy và huấn luyện. Vì vậy, công việc của bạn "
                "không gói gọn trong một nghề duy nhất; mô tả sát nhất là nhà quản lý vận hành, "
                "người xây dựng hệ thống và người sáng lập STE."
            )
        return "Mình chưa có đủ memory để xác định nghề nghiệp hoặc vai trò hiện tại của bạn."

    async def _answer_person_tasks(self, raw_text: str) -> str:
        person = await self._find_person_from_text(raw_text)
        if person is None:
            return "Bạn muốn xem task của ai? Nói tên người đó giúp mình nhé."
        tasks = await self.task_repo.list_active_by_person(person.id)
        if not tasks:
            return f"Hiện mình chưa thấy task đang mở nào được giao cho {person.display_name}."
        lines = [f"Task đang mở của {person.display_name}"]
        for index, task in enumerate(tasks, 1):
            due = _format_local_datetime(task.due_at, self.secretary_service.display_timezone) if task.due_at else "chưa có hạn"
            lines.append(f"{index}. {task.title} - Hạn: {due}")
        return "\n".join(lines)

    async def _assign_task_to_person(self, raw_text: str, note_id: str) -> str:
        person = await self._find_person_from_text(raw_text)
        title = _extract_assigned_task_title(raw_text)
        if person is None:
            return "Mình hiểu là bạn muốn giao task, nhưng chưa xác định được người nhận. Bạn nói rõ tên người đó giúp mình nhé."
        if not title:
            return f"Bạn muốn giao task gì cho {person.display_name}?"
        task = await self.task_repo.create(
            Task(
                title=title,
                person_id=person.id,
                source_note_id=note_id,
                confidence=0.95,
            )
        )
        await self.event_service.append_event(
            EventType.TASK_CANDIDATE_CREATED,
            "task",
            task.id,
            {"source_note_id": note_id, "assigned_person_id": person.id},
        )
        return f"Mình đã tạo task giao cho {person.display_name}: {task.title}."

    async def _create_task_check_reminder(self, raw_text: str, note_id: str) -> str:
        person = await self._find_person_from_text(raw_text)
        due_at = parse_clarification_datetime(raw_text, default_timezone=self.secretary_service.display_timezone)
        if due_at is None:
            return "Bạn muốn mình nhắc kiểm tra task này lúc nào? Ví dụ: 'mai 9h'."
        title = _extract_check_task_title(raw_text)
        person_text = f" của {person.display_name}" if person else ""
        reminder = await self.secretary_service.reminder_repo.create(
            Reminder(
                title=f"Kiểm tra task{person_text}: {title}" if title else f"Kiểm tra task{person_text}",
                remind_at=due_at,
                source_note_id=note_id,
                confidence=0.95,
            )
        )
        await self.event_service.append_event(
            EventType.REMINDER_CANDIDATE_CREATED,
            "reminder",
            reminder.id,
            {"source_note_id": note_id, "person_id": person.id if person else None},
        )
        await self.capture_service.reminder_service.schedule_reminder(reminder.id)
        local = due_at.astimezone(self.secretary_service.display_timezone).strftime("%H:%M %d/%m/%Y")
        return f"Mình đã đặt lịch nhắc bạn kiểm tra task{person_text} vào {local}."

    async def _find_person_from_text(self, raw_text: str):
        if self.secretary_service.person_repo is None:
            return None
        normalized = _normalize_text(raw_text)
        if re.search(r"\bnguyen\b", normalized):
            person = await self.secretary_service.person_repo.find_by_name_or_alias("Khôi Nguyên")
            if person is not None:
                return person
        names = _extract_possible_person_names(raw_text)
        for name in names:
            person = await self.secretary_service.person_repo.find_by_name_or_alias(name)
            if person is not None:
                return person
        return None

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
            recent_task = await self._recent_task_for_due_update(request, target_entity_hints)
            if recent_task is not None and self.capture_service.clarification_service:
                question = _localized(
                    raw_text,
                    f"Bạn muốn đổi hạn task '{recent_task.title}' sang lúc nào? Nói kiểu 'hôm nay 19h' giúp mình nhé.",
                    f"When should I set the deadline for task '{recent_task.title}'?",
                )
                await self._create_clarification_request(
                    ClarificationRequest(
                        source_chat_id=request.source_chat_id or "system",
                        source_message_id=request.source_message_id,
                        entity_type="task_due_missing",
                        entity_id=recent_task.id,
                        field_name="due_at",
                        question=question,
                    )
                )
                return question
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

    async def _recent_task_for_due_update(
        self,
        request: CaptureRequest,
        target_entity_hints: str | None,
    ) -> Task | None:
        query = target_entity_hints or _task_due_update_query(request.raw_text)
        if query:
            matches = _ranked_task_matches(query, await self.task_repo.list_active())
            if len(matches) == 1:
                return matches[0]
        recent_tasks = await self.task_repo.list_recent_active(limit=1)
        return recent_tasks[0] if recent_tasks else None

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
            "sinh ngay", "born on"
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
    if _is_profile_question(normalized):
        return "query_profile"
    if _is_assistant_identity_question(normalized):
        return "query_assistant_identity"
    if _is_ste_mindx_compare_query(normalized):
        return "query_ste_mindx_compare"
    if _is_assign_task_to_person(normalized):
        return "assign_task_to_person"
    if _is_task_check_reminder(normalized):
        return "create_task_check_reminder"
    if _is_person_task_query(normalized):
        return "query_person_tasks"
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
    if _is_people_query(normalized):
        return "query_people"
    if _is_commitment_query(normalized):
        return "query_commitments"
    if _is_meeting_prep_query(normalized):
        return "query_meeting_prep"
    if _is_context_query(normalized):
        return "query_context"
    if _is_reminder_query(normalized):
        return "query_reminders"
    if _is_project_query(normalized):
        return "query_projects"
    if _is_task_query(normalized):
        return "query_tasks"
    if _is_knowledge_question(normalized) or _looks_like_question(normalized):
        return "query_context"
    
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
    created_parts = []
    if response.tasks_created:
        created_parts.append(f"{response.tasks_created} task")
    if response.reminders_created:
        created_parts.append(f"{response.reminders_created} reminder")
    if response.memories_created:
        created_parts.append(f"{response.memories_created} ghi nhớ")
    if response.people_created:
        created_parts.append(f"{response.people_created} người")
    if response.meetings_created:
        created_parts.append(f"{response.meetings_created} meeting")
    if response.followups_created:
        created_parts.append(f"{response.followups_created} follow-up")
    if response.commitments_created:
        created_parts.append(f"{response.commitments_created} commitment")
    if response.tasks_completed:
        created_parts.append(f"{response.tasks_completed} task đã xong")

    if created_parts:
        text = f"Mình đã ghi nhận: {response.summary}\nĐã tạo/cập nhật: {', '.join(created_parts)}."
    else:
        text = f"Mình đã lưu ghi chú: {response.summary}"
    if response.memories_deleted:
        text += f"\nĐã xoá {response.memories_deleted} memory khớp."
    if response.errors:
        text += "\nCó phần mình chưa trích xuất chắc chắn, nhưng ghi chú gốc đã được lưu."
    if response.clarification_question:
        text += f"\n{response.clarification_question}"
    if response.duplicate_suggestions:
        text += "\n\nGợi ý kiểm tra trùng:\n- " + "\n- ".join(response.duplicate_suggestions)
    if response.entity_suggestion_ids:
        text += "\nMình nhận thấy một biệt danh/liên kết mới và cần bạn xác nhận."
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
        r"\b\d{1,2}(?:h\d{0,2}|:\d{2})?\s*(?:am|pm)?\b",
        r"\b(?:doi|sua|cap nhat|chinh|dat)\b",
        r"\b(?:lai|gio|han|chot|deadline|due|due date|han chot|thanh|la|luc|vao|cho|task|viec|hom nay|toi nay|today)\b",
    )
    for pattern in cleanup_patterns:
        query = re.sub(pattern, " ", query)
    return " ".join(query.split())


def _extract_task_due_at(raw_text: str, display_timezone) -> datetime | None:
    normalized = _normalize_text(raw_text)
    has_clock = _parse_clock_time(normalized) is not None
    has_relative = re.search(
        r"\b\d{1,3}\s*(?:phut|tieng|gio|ngay|minute|minutes|hour|hours|day|days)\s*(?:sau)?\b",
        normalized,
    ) is not None
    if not has_clock and not has_relative:
        return None
    parsed = parse_clarification_datetime(raw_text, default_timezone=display_timezone)
    if parsed is not None:
        return parsed.astimezone(UTC)

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
        candidate = match.group(1).strip()
        if candidate not in {"o", "tai", "ở", "toi", "minh", "cac", "nhung"} and not candidate.startswith(("o ", "tai ")):
            return candidate
    return None


def _extract_project_scope(raw_text: str) -> str | None:
    normalized = _normalize_text(raw_text)
    if "mindx" in normalized:
        return "mindx"
    if "ste" in normalized:
        return "ste"
    return None


def _extract_context_query(raw_text: str) -> str | None:
    normalized = _normalize_text(raw_text)
    if raw_text.startswith("/"):
        query = raw_text.partition(" ")[2].strip()
        return query or None
    cleanup_patterns = (
        r"\b(?:context|ngu canh|thong tin|xem context|cho toi context)\b",
        r"\b(?:meeting prep|prep|chuan bi hop|chuan bi meeting|truoc khi hop)\b",
        r"\b(?:voi|ve|cho|project|person|nguoi)\b",
    )
    query = normalized
    for pattern in cleanup_patterns:
        query = re.sub(pattern, " ", query)
    query = " ".join(query.split())
    return query or None


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
        signal in normalized for signal in ("can lam gi", "lich", "agenda", "viec gi", "thu may", "what")
    )


def _is_task_query(normalized: str) -> bool:
    if normalized in {"task", "tasks"}:
        return True
    return any(
        signal in normalized
        for signal in (
            "task cua toi",
            "toi co task",
            "toi dang co task",
            "toi co viec",
            "toi dang co viec",
            "dang co task gi",
            "dang co viec gi",
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
    if "project" not in normalized and "du an" not in normalized:
        return False
    return any(
        signal in normalized
        for signal in (
            "con gi",
            "chua xong",
            "tasks",
            "viec",
            "dang mo",
            "open",
            "unfinished",
            "in ra",
            "danh sach",
            "cac project",
            "project o",
            "project tai",
            "dang can lam",
            "can lam",
            "la gi",
        )
    )


def _is_person_task_query(normalized: str) -> bool:
    if _is_delete_all_tasks(normalized):
        return False
    first_person_subjects = ("toi", "minh", "tui", "tao", "em")
    if any(
        signal in normalized
        for signal in (
            "task nao",
            "viec nao",
            "task cua toi",
            "viec cua toi",
            "tasks",
            "open tasks",
        )
    ):
        return False
    if re.search(
        rf"^(?:{'|'.join(first_person_subjects)})\s+(?:dang co|co)\s+(?:task|viec)",
        normalized,
    ):
        return False
    has_task = "task" in normalized or "viec" in normalized
    asks = any(signal in normalized for signal in ("co", "dang co", "la gi", "gi", "nao"))
    has_person_shape = bool(
        re.search(r"\b(?:task|viec)\s+cua\s+(?!toi\b).+", normalized)
        or re.search(
            rf"^(?!(?:{'|'.join(first_person_subjects)})\b)\w+(?:\s+\w+){{0,2}}\s+(?:dang co|co)\s+(?:task|viec)",
            normalized,
        )
    )
    return has_task and asks and has_person_shape


def _is_assign_task_to_person(normalized: str) -> bool:
    return any(signal in normalized for signal in ("giao cho", "assign cho")) and (
        "task" in normalized or "viec" in normalized
    )


def _is_task_check_reminder(normalized: str) -> bool:
    has_time = any(signal in normalized for signal in ("mai", "ngay mai", "hom nay", "toi nay"))
    has_check = any(signal in normalized for signal in ("kiem tra", "check", "xem lai"))
    has_schedule = any(signal in normalized for signal in ("dat lich", "dat klich", "dat klichj", "nhac", "remind"))
    return has_time and has_check and ("task" in normalized or "viec" in normalized) and has_schedule


def _is_people_query(normalized: str) -> bool:
    return normalized in {"people", "persons", "danh sach nguoi", "nhung nguoi lien quan"} or (
        any(signal in normalized for signal in ("people", "nguoi", "person"))
        and any(signal in normalized for signal in ("danh sach", "list", "co ai", "lien quan"))
    )


def _is_commitment_query(normalized: str) -> bool:
    return any(
        signal in normalized
        for signal in (
            "commitments",
            "cam ket",
            "toi no ai",
            "ai no toi",
            "nguoi khac no toi",
            "what do i owe",
            "who owes me",
        )
    )


def _is_context_query(normalized: str) -> bool:
    return any(signal in normalized for signal in ("context", "ngu canh", "thong tin ve"))


def _is_meeting_prep_query(normalized: str) -> bool:
    return any(
        signal in normalized
        for signal in (
            "meeting prep",
            "prep meeting",
            "chuan bi hop",
            "chuan bi meeting",
            "truoc khi hop",
        )
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


def _is_task_due_update_followup(normalized: str) -> bool:
    return any(
        signal in normalized
        for signal in (
            "doi thanh",
            "doi lai thanh",
            "sua thanh",
            "cap nhat thanh",
            "chuyen thanh",
        )
    ) and any(time_signal in normalized for time_signal in ("hom nay", "toi nay", "mai", "ngay mai", "luc", "gio"))


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


def _is_profile_question(normalized: str) -> bool:
    exact = {
        "toi la ai",
        "ban biet toi la ai khong",
        "ban nho toi la ai khong",
        "toi dang lam nghe gi",
        "toi lam nghe gi",
        "toi dang lam cong viec gi",
        "toi lam cong viec gi",
        "nghe nghiep cua toi la gi",
        "vai tro nghe nghiep cua toi la gi",
        "what do i do for work",
        "what is my profession",
    }
    return normalized in exact


def _is_assistant_identity_question(normalized: str) -> bool:
    return normalized in {
        "ban la gi",
        "ban la ai",
        "ban la cai gi",
        "memocore la gi",
        "memo core la gi",
    }


def _is_ste_mindx_compare_query(normalized: str) -> bool:
    return "ste" in normalized and "mindx" in normalized and any(
        signal in normalized for signal in ("khac gi", "khac nhau", "phan biet", "so sanh")
    )


def _assistant_identity_answer() -> str:
    return (
        "Mình là MemoCore, trợ lý cá nhân của bạn. "
        "Mình giúp bạn ghi nhớ bối cảnh dài hạn, theo dõi task/project, nhắc việc, "
        "và trả lời dựa trên dữ liệu đã lưu. Khi dữ liệu chưa chắc, mình sẽ nói rõ là cần xác nhận thay vì trình bày như fact."
    )


def _ste_mindx_compare_answer() -> str:
    return (
        "STE và MindX là hai bối cảnh khác nhau của bạn:\n"
        "- MindX là tổ chức nơi bạn đang làm/vận hành, gắn với Teaching Operations, TEGL+, TOM và các hệ thống nội bộ.\n"
        "- STE là portfolio/danh mục sáng lập do bạn vận hành, gồm dữ liệu/BI, công nghệ, AI, giáo dục, đầu tư và các dự án khách hàng.\n"
        "- Người hoặc project có thể liên quan cả hai bên, nhưng khi lưu memory cần tách rõ: vai trò ở MindX, vai trò ở STE, và dữ liệu lịch sử.\n"
        "- Các ý tưởng đào tạo/Data/AI chưa xác nhận không nên dùng để định nghĩa sự khác nhau giữa STE và MindX."
    )


def _extract_possible_person_names(raw_text: str) -> list[str]:
    normalized = _normalize_text(raw_text)
    if re.search(r"^(?:toi|minh|tui|tao|em)\s+(?:dang co|co)\s+(?:task|viec)", normalized):
        return []
    candidates: list[str] = []
    patterns = (
        r"(?:giao cho|assign cho)\s+(.+?)\s+(?:task|viec)\b",
        r"task cua\s+(.+?)(?:\s+la|\s+co|\s+gi|\?|$)",
        r"viec cua\s+(.+?)(?:\s+la|\s+co|\s+gi|\?|$)",
        r"(.+?)\s+(?:dang co|co)\s+(?:task|viec)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            candidates.append(match.group(1).strip())
    tokens = normalized.split()
    for size in (3, 2, 1):
        for index in range(0, max(0, len(tokens) - size + 1)):
            phrase = " ".join(tokens[index : index + size])
            if phrase not in {"toi", "task", "viec", "kiem tra", "ngay mai", "mai"}:
                candidates.append(phrase)
    return candidates


def _extract_assigned_task_title(raw_text: str) -> str:
    patterns = (
        r"task\s+(?:là|la)\s+(.+)$",
        r"việc\s+(?:là|la)\s+(.+)$",
        r"giao cho .+?\s+(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, raw_text, flags=re.IGNORECASE)
        if match:
            title = match.group(1).strip(" .")
            title = re.sub(r"^(task|việc|viec)\s+(là|la)\s+", "", title, flags=re.IGNORECASE)
            return title.strip(" .")
    return ""


def _extract_check_task_title(raw_text: str) -> str:
    match = re.search(r"task\s+của\s+.+?\s+(?:là|la)\s+(.+?)(?:,|\.\s|\s+đặt|\s+dat|$)", raw_text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip(" .,")
    match = re.search(r"kiểm tra\s+(.+?)(?:,|\.\s|\s+đặt|\s+dat|$)", raw_text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip(" .,")
    return ""


def _is_knowledge_question(normalized: str) -> bool:
    signals = (
        "noi ve ",
        " la ai",
        " la gi",
        " la gid",
        " neu ai do hoi",
        "neu ai do hoi",
        "dang build",
        "dang xay",
        "dang phat trien",
        "follow",
        "theo doi viec",
        "phu trach ai",
        "ai phu trach",
    )
    return any(signal in normalized for signal in signals)


def _looks_like_question(normalized: str) -> bool:
    if not normalized:
        return False
    if normalized in {"lam gi day", "dang lam gi day", "lam cai gi day"}:
        return False
    if any(signal in normalized for signal in ("troi", "thoi tiet", "weather")):
        return False
    question_words = (
        "ai",
        "gi",
        "gid",
        "sao",
        "nhu the nao",
        "the nao",
        "vi sao",
        "tai sao",
        "bao gio",
        "khi nao",
        "o dau",
        "thu may",
        "what",
        "who",
        "when",
        "where",
        "why",
        "how",
    )
    return any(re.search(rf"\b{re.escape(word)}\b", normalized) for word in question_words)


def _is_followup_detail_request(normalized: str) -> bool:
    return normalized in {
        "cu the hon",
        "cu the hon di",
        "noi cu the hon",
        "noi ro hon",
        "ro hon di",
        "chi tiet hon",
        "chi tiet hon di",
        "chi",
        "y la sao",
        "con gi nua khong",
        "con gi nua ko",
        "con nua khong",
        "con nua ko",
        "con gi khac khong",
        "con gi khac ko",
        "het chua",
    }


def _is_more_remaining_followup(normalized: str) -> bool:
    return normalized in {
        "con gi nua khong",
        "con gi nua ko",
        "con nua khong",
        "con nua ko",
        "con gi khac khong",
        "con gi khac ko",
        "het chua",
    }


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


def _trailing_action_tag(text: str) -> str | None:
    match = re.search(
        r"(?:^|\s)#(linkedin|li|task|t|remind|r|mem|m)\s*[.,!?;:]*\s*$",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1).lower() if match else None


def _is_meaningful_statement(text: str) -> bool:
    normalized = _normalize_text(text)
    low_value_signals = (
        "troi",
        "thoi tiet",
        "weather",
        "vo van",
        "linh tinh",
        "noi nham",
        "test thu",
    )
    if any(signal in normalized for signal in low_value_signals):
        return False
    words = normalized.split()
    if len(words) <= 1:
        return False
    # Check common short/greeting words
    greetings = {"hi", "hello", "hey", "alo", "ok", "oke", "thanks", "cam on", "thank you", "chao", "bye"}
    if len(words) == 2 and any(w in greetings for w in words):
        return False
    return True


def _parse_task_rename(text: str) -> tuple[str, str, bool] | None:
    pattern = r"^(?:sửa|sua|đổi|doi|change|edit|rename)(?:\s+(?:cho\s+tôi|cho\s+toi|tên|ten|tiêu\s+đề|tieu\s+de))?\s+(?:task|tasks|công\s+việc|cong\s+viec|việc|viec)?\s+(.+?)\s+(?:thành|thanh|sang|to)\s+(.+)$"
    match = re.match(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    old_query = match.group(1).strip()
    new_raw = match.group(2).strip()

    show_tasks = False
    if re.search(r"(?:^|\s)/tasks\b", new_raw, re.IGNORECASE):
        show_tasks = True

    new_title = new_raw
    # Strip trailing slash commands
    new_title = re.sub(r"\s*/[a-zA-Z0-9_-]+$", "", new_title).strip()
    # Strip trailing hashtags
    new_title = re.sub(r"\s*#[a-zA-Z0-9_-]+$", "", new_title).strip()

    if not old_query or not new_title:
        return None
    return old_query, new_title, show_tasks
