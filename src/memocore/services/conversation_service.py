from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
import re
import unicodedata

from memocore.adapters.storage.repositories import (
    ChatContextRepository,
    ClarificationRequestRepository,
    NoteRepository,
    TaskListContextRepository,
    TaskRepository,
)
from memocore.domain.models import (
    ChatContext,
    ClarificationRequest,
    EventType,
    FeedbackSignal,
    Note,
    Reminder,
    ReminderStatus,
    Task,
    TaskStatus,
)
from memocore.domain.schemas import CaptureRequest, CaptureResponse
from memocore.services.capture_service import CaptureService
from memocore.services.clarification_service import parse_clarification_datetime
from memocore.services.event_service import EventService
from memocore.services.memory_service import MemoryService
from memocore.services.secretary_service import SecretaryService
from memocore.services.intent_classifier_service import IntentClassifierService
from memocore.services.knowledge_query_service import KnowledgeQueryService
from memocore.services.reference_resolver import (
    ReferenceResolver,
    ResolvedReference,
)
from memocore.services.conversation_planner import (
    ConversationPlan,
    ConversationPlanner,
    is_daily_schedule_query as _is_daily_schedule_query,
    is_future_task_capture as _is_future_task_capture,
    is_bare_entity_reference,
    parse_task_merge_request as _parse_task_merge_request,
)
from memocore.services.conversation_frame import (
    ConversationFrame,
    ConversationFrameBuilder,
)
from memocore.services.conversation_router import ConversationRouter
from memocore.services.conversation_executor import ConversationExecutor
from memocore.services.conversation_composer import ConversationComposer
from memocore.services.task_operation_service import (
    TaskOperationResult,
    TaskOperationService,
)
from memocore.services.task_batch import (
    TaskBatchSnapshot,
    batch_preview_text,
    encode_batch_field,
)
from memocore.services.task_reference_resolver import (
    ResolvedTaskSelection,
    TaskReferenceResolver,
    TaskSelectionMode,
    TaskSelectionSource,
)
from memocore.services.query_executor import QueryExecutor
from memocore.services.timeline_query_service import TimelineQueryService
from memocore.services.task_mutation_executor import TaskMutationExecutor
from memocore.services.memory_lifecycle_executor import MemoryLifecycleExecutor
from memocore.services.clarification_workflow import ClarificationWorkflow
from memocore.services.commitment_lifecycle_service import CommitmentLifecycleService


@dataclass(frozen=True)
class ConversationResult:
    intent: str
    reply: str
    captured: bool = False
    reply_markup: Any = None
    result_entity_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnContext:
    now_utc: datetime
    display_timezone: tzinfo

    @property
    def local_now(self) -> datetime:
        return self.now_utc.astimezone(self.display_timezone)


@dataclass(frozen=True)
class RenameOutcome:
    reply: str
    reply_markup: Any = None
    task_id: str | None = None


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
        task_list_context_repo: TaskListContextRepository | None = None,
        reference_resolver: ReferenceResolver | None = None,
        task_operation_service: TaskOperationService | None = None,
        conversation_planner: ConversationPlanner | None = None,
        conversation_router: ConversationRouter | None = None,
        conversation_executor: ConversationExecutor | None = None,
        conversation_composer: ConversationComposer | None = None,
        query_executor: QueryExecutor | None = None,
        task_mutation_executor: TaskMutationExecutor | None = None,
        memory_lifecycle_executor: MemoryLifecycleExecutor | None = None,
        clarification_workflow: ClarificationWorkflow | None = None,
        conversation_frame_builder: ConversationFrameBuilder | None = None,
        task_reference_resolver: TaskReferenceResolver | None = None,
        timeline_query_service: TimelineQueryService | None = None,
        commitment_lifecycle_service: CommitmentLifecycleService | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.capture_service = capture_service
        self.secretary_service = secretary_service
        self.note_repo = note_repo
        self.task_repo = task_repo
        self.memory_service = memory_service
        self.event_service = event_service
        self.now_provider = now_provider or (
            task_reference_resolver.now_provider
            if task_reference_resolver is not None
            else lambda: datetime.now(UTC)
        )
        self.intent_classifier_service = intent_classifier_service
        self.knowledge_query_service = knowledge_query_service
        self.task_list_context_repo = task_list_context_repo or TaskListContextRepository(
            task_repo.database
        )
        self.reference_resolver = reference_resolver
        self.task_operation_service = task_operation_service or TaskOperationService(
            task_repo,
            event_service,
            getattr(capture_service, "activity_reconciliation_service", None),
        )
        self.task_reference_resolver = task_reference_resolver or TaskReferenceResolver(
            task_repo,
            self.task_list_context_repo,
            display_timezone=secretary_service.display_timezone,
            now_provider=self.now_provider,
        )
        self.conversation_planner = conversation_planner or ConversationPlanner()
        self.conversation_router = conversation_router or ConversationRouter(
            intent_classifier_service
        )
        self.conversation_executor = conversation_executor or ConversationExecutor()
        self.conversation_composer = conversation_composer or ConversationComposer()
        self.query_executor = query_executor or QueryExecutor(
            secretary_service, timeline_query_service
        )
        self.commitment_lifecycle_service = commitment_lifecycle_service
        self.task_mutation_executor = task_mutation_executor or TaskMutationExecutor()
        self.memory_lifecycle_executor = (
            memory_lifecycle_executor or MemoryLifecycleExecutor()
        )
        self.clarification_workflow = clarification_workflow or ClarificationWorkflow(
            self.conversation_composer
        )
        context_repo = (
            reference_resolver.context_repo
            if reference_resolver is not None
            else ChatContextRepository(task_repo.database)
        )
        clarification_repo = (
            capture_service.clarification_service.clarification_repo
            if capture_service.clarification_service is not None
            else ClarificationRequestRepository(task_repo.database)
        )
        self.conversation_frame_builder = (
            conversation_frame_builder
            or ConversationFrameBuilder(
                context_repo,
                clarification_repo,
                self.task_list_context_repo,
                task_repo,
            )
        )

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
            if command == "search":
                return "query_search"
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

        if _is_cancel_task(normalized):
            return "cancel_task"

        if _parse_task_merge_request(text) is not None:
            return "merge_tasks"

        if _is_future_task_capture(normalized):
            return "capture_task"

        if _is_task_due_update(normalized) or _is_task_due_update_followup(normalized):
            return "update_task_due"

        if _is_task_priority_update(normalized):
            return "update_task_priority"

        if _is_task_recurrence_update(normalized):
            return "update_task_recurrence"

        if _is_reminder_snooze(normalized):
            return "snooze_reminder"

        if _is_task_recurrence_query(normalized):
            return "query_task_recurrence"

        if _is_daily_schedule_query(normalized):
            return "query_task_recurrence"

        if _is_origin_query(normalized):
            return "query_origin"

        if _is_decision_timeline_query(normalized):
            return "query_decisions"

        if _is_timeline_query(normalized):
            return "query_timeline"

        if _is_search_query(normalized):
            return "query_search"

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

        if _is_commitment_query(normalized):
            return "query_commitments"

        if _is_people_query(normalized):
            return "query_people"

        if _is_reminder_query(normalized):
            return "query_reminders"

        if _is_knowledge_question(normalized) or _looks_like_question(normalized):
            return "query_context"
            
        return None

    async def handle_text(self, request: CaptureRequest) -> ConversationResult:
        now_utc = self.now_provider().astimezone(UTC)
        turn = TurnContext(now_utc, self.secretary_service.display_timezone)
        frame = await self.conversation_frame_builder.build(request.source_chat_id)
        plan = self.conversation_planner.plan(request.raw_text, frame)
        result = await self._handle_text_core(request, frame, plan, turn)
        await self._record_conversation_outcome(
            request,
            frame,
            plan,
            result,
            now_utc=turn.now_utc,
        )
        return result

    async def record_external_outcome(
        self,
        request: CaptureRequest,
        *,
        intent: str,
        reply: str,
    ) -> None:
        """Record a turn resolved by an adapter-level workflow such as clarification."""
        frame = await self.conversation_frame_builder.build(request.source_chat_id)
        await self._record_conversation_outcome(
            request,
            frame,
            ConversationPlan(
                intent=intent,
                goal="resolve_pending_clarification",
                reason="Adapter-level clarification answer resolved a pending operation.",
            ),
            ConversationResult(intent=intent, reply=reply),
        )

    async def _handle_text_core(
        self,
        request: CaptureRequest,
        frame: ConversationFrame,
        plan: ConversationPlan | None,
        turn: TurnContext,
    ) -> ConversationResult:
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
                "/li": "Anh muốn lưu ghi chú gì? Hãy nhập nội dung sau lệnh. Ví dụ: `/li 5 bài học quản lý`",
                "/linkedin": "Anh muốn lưu ghi chú gì? Hãy nhập nội dung sau lệnh. Ví dụ: `/linkedin 5 bài học quản lý`",
                "/task": "Anh muốn tạo công việc gì? Hãy nhập nội dung sau lệnh. Ví dụ: `/task Hoàn thành slide STE`",
                "/t": "Anh muốn tạo công việc gì? Hãy nhập nội dung sau lệnh. Ví dụ: `/t Hoàn thành slide STE`",
                "/mem": "Anh muốn lưu ký ức gì? Hãy nhập nội dung sau lệnh. Ví dụ: `/mem Khởi nghiệp STE từ 2024`",
                "/m": "Anh muốn lưu ký ức gì? Hãy nhập nội dung sau lệnh. Ví dụ: `/m Khởi nghiệp STE từ 2024`"
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

        reference = (
            await self.reference_resolver.resolve(
                request.source_chat_id, request.raw_text
            )
            if self.reference_resolver is not None
            else ResolvedReference()
        )
        if self.commitment_lifecycle_service is not None:
            lifecycle = await self.commitment_lifecycle_service.handle_text(
                request.raw_text,
                source_chat_id=request.source_chat_id,
                source_message_id=request.source_message_id,
                now=turn.now_utc,
            )
            if lifecycle.handled:
                return ConversationResult(
                    intent="close_open_loop",
                    reply=lifecycle.reply,
                    result_entity_ids=lifecycle.entity_ids,
                )
        # Check for task rename request before any other routing/classification
        rename_info = _parse_task_rename(request.raw_text)
        if rename_info:
            old_query, new_title, show_tasks = rename_info
            note = await self._record_interaction(request, "rename_task")
            outcome = await self._rename_task(
                old_query,
                new_title,
                note.id,
                request,
                show_tasks,
                turn.now_utc,
            )
            return ConversationResult(
                intent="rename_task",
                reply=outcome.reply,
                reply_markup=outcome.reply_markup,
                result_entity_ids=(outcome.task_id,) if outcome.task_id else (),
            )

        decision = await self.conversation_router.route(
            request.raw_text,
            planned_intent=plan.intent if plan is not None else None,
            bare_entity_reference=is_bare_entity_reference(
                request.raw_text, reference.entity_name
            ),
            deterministic_route=self._deterministic_route,
            fallback_route=classify_intent,
            conversation_context=frame.prompt_context(),
        )
        intent = decision.intent
        ambiguity_detected = decision.ambiguity_detected
        clarification_question = decision.clarification_question
        target_entity_hints = decision.target_entity_hints

        # Layer 3: Action Executor & Writer
        if intent == "capture_previous_as_task":
            response = await self._capture_previous_message(request)
            return ConversationResult(
                intent=intent,
                reply=format_capture_response(response),
                captured=True,
            )

        scoped_result = await self.memory_lifecycle_executor.execute(
            intent,
            {
                "update_knowledge": lambda: self._execute_knowledge_update(
                    request, reference, plan
                ),
                "rollback_knowledge_update": lambda: self._execute_knowledge_rollback(
                    request, plan
                ),
            },
        )
        if scoped_result is not None:
            return ConversationResult(
                intent=scoped_result.intent,
                reply=scoped_result.reply,
                captured=scoped_result.captured,
                reply_markup=scoped_result.reply_markup,
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
            if _is_casual(_normalize_text(request.raw_text)):
                return ConversationResult(
                    intent=intent,
                    reply=_localized(request.raw_text, "Em nghe rồi.", "Got it."),
                )
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
                    reply="Em thấy nội dung này có thể hữu ích nhưng chưa rõ anh muốn lưu vào đâu. Anh muốn lưu làm gì?",
                    reply_markup=keyboard,
                )
            else:
                return ConversationResult(
                    intent=intent,
                    reply=_localized(request.raw_text, "Em nghe rồi.", "Got it."),
                )

        if (
            intent == "query_context"
            and reference.entity_id is None
            and _is_followup_detail_request(_normalize_text(request.raw_text))
        ):
            expanded = await self._expand_followup_query(request)
            if expanded:
                request = CaptureRequest(
                    source=request.source,
                    source_message_id=request.source_message_id,
                    source_chat_id=request.source_chat_id,
                    raw_text=expanded,
                )


        clarification_result = self.clarification_workflow.unresolved(
            intent,
            clarification_question,
            vietnamese=_looks_vietnamese(request.raw_text),
        )
        if clarification_result is not None:
            return ConversationResult(
                intent=clarification_result.intent, reply=clarification_result.reply
            )

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
                reply = await self._knowledge_answer(request.raw_text, reference)
                await self._remember_reference(request, intent, reference)
                return ConversationResult(intent=intent, reply=reply)
            except Exception:
                pass

        query_execution = await self.query_executor.execute(
            intent,
            now_utc=turn.now_utc,
            project_name=_extract_project_name(request.raw_text),
            project_scope=_extract_project_scope(request.raw_text),
            memory_bucket=_memory_bucket(request.raw_text),
            context_query=_extract_context_query(request.raw_text),
            static_replies={
                "query_assistant_identity": _assistant_identity_answer(),
                "query_ste_mindx_compare": _ste_mindx_compare_answer(),
            },
            callbacks={
                "query_person_tasks": lambda: self._answer_person_tasks(request.raw_text),
                "query_task_recurrence": lambda: self._answer_task_recurrence(
                    request.raw_text,
                    now_utc=turn.now_utc,
                ),
                "query_profile": lambda: self._answer_profile_question(request.raw_text),
            },
        )
        if query_execution is not None:
            return ConversationResult(
                intent=query_execution.intent,
                reply=query_execution.reply,
                captured=query_execution.captured,
                reply_markup=query_execution.reply_markup,
            )

        task_execution = await self.task_mutation_executor.execute(
            intent,
            {
                "mark_task_done": lambda: self._mark_task_done(
                    request.raw_text,
                    note.id,
                    request,
                    target_entity_hints,
                    ambiguity_detected,
                    turn.now_utc,
                ),
                "update_task_priority": lambda: self._update_task_priority(
                    request.raw_text, note.id, request, turn.now_utc
                ),
                "update_task_recurrence": lambda: self._update_task_recurrence(
                    request.raw_text, note.id, request, turn.now_utc
                ),
                "assign_task_to_person": lambda: self._assign_task_to_person(
                    request.raw_text, note.id
                ),
                "create_task_check_reminder": lambda: self._create_task_check_reminder(
                    request.raw_text, note.id, turn.now_utc
                ),
                "delete_all_tasks": lambda: self._delete_all_tasks(
                    request.raw_text, note.id
                ),
                "cancel_task": lambda: self._cancel_task(
                    request.raw_text, note.id, request, turn.now_utc
                ),
                "merge_tasks": lambda: self._merge_tasks(
                    request.raw_text, note.id, plan, turn.now_utc
                ),
                "undo_last_action": lambda: self._undo_last_action(plan),
                "update_task": lambda: self._update_task_due(
                    request.raw_text,
                    note.id,
                    request,
                    target_entity_hints,
                    ambiguity_detected,
                    turn.now_utc,
                ),
                "update_task_due": lambda: self._update_task_due(
                    request.raw_text,
                    note.id,
                    request,
                    target_entity_hints,
                    ambiguity_detected,
                    turn.now_utc,
                ),
                "snooze_reminder": lambda: self._snooze_reminder(
                    request.raw_text,
                    request,
                    turn.now_utc,
                ),
            },
        )
        if task_execution is not None:
            return ConversationResult(
                intent=task_execution.intent,
                reply=task_execution.reply,
                captured=task_execution.captured,
                reply_markup=task_execution.reply_markup,
            )

        memory_execution = await self.memory_lifecycle_executor.execute(
            intent,
            {
                "memory_delete": lambda: self._delete_memory(request.raw_text),
                "correction_feedback": lambda: self._correct_recent_object(
                    request.raw_text, note.id, request
                ),
                "memory_correction": lambda: self._correct_recent_object(
                    request.raw_text, note.id, request
                ),
            },
        )
        if memory_execution is not None:
            return ConversationResult(
                intent=memory_execution.intent,
                reply=memory_execution.reply,
                captured=memory_execution.captured,
                reply_markup=memory_execution.reply_markup,
            )

        clarification_answer = self.clarification_workflow.answer_without_pending(
            intent, vietnamese=_looks_vietnamese(request.raw_text)
        )
        if clarification_answer is not None:
            return ConversationResult(
                intent=clarification_answer.intent, reply=clarification_answer.reply
            )

        return ConversationResult(
            intent=intent,
            reply=self.conversation_composer.unhandled(
                vietnamese=_looks_vietnamese(request.raw_text)
            ),
        )

    async def _record_conversation_outcome(
        self,
        request: CaptureRequest,
        frame: ConversationFrame,
        plan: ConversationPlan | None,
        result: ConversationResult,
        *,
        now_utc: datetime | None = None,
    ) -> None:
        chat_id = request.source_chat_id
        if not chat_id:
            return

        active_after = await self.task_repo.list_active()
        active_after_ids = {task.id for task in active_after}
        created_task_ids = sorted(active_after_ids - set(frame.active_task_ids))
        result_entity_ids = list(result.result_entity_ids) or created_task_ids or (
            list(plan.target_entity_ids) if plan is not None else []
        )

        context_repo = self.conversation_frame_builder.context_repo
        current = await context_repo.get(chat_id)
        now = now_utc or self.now_provider().astimezone(UTC)
        await context_repo.save(
            ChatContext(
                source_chat_id=chat_id,
                focused_entity_type=(
                    current.focused_entity_type if current else frame.focused_entity_type
                ),
                focused_entity_id=(
                    current.focused_entity_id if current else frame.focused_entity_id
                ),
                last_intent=result.intent,
                last_result_entity_ids=result_entity_ids,
                updated_at=now,
                expires_at=current.expires_at if current else None,
            )
        )
        plan_payload = _conversation_plan_payload(plan)
        await context_repo.append_turn(
            source_chat_id=chat_id,
            source_message_id=request.source_message_id,
            raw_text=request.raw_text,
            intent=result.intent,
            focused_entity_type=(
                current.focused_entity_type if current else frame.focused_entity_type
            ),
            focused_entity_id=(
                current.focused_entity_id if current else frame.focused_entity_id
            ),
            result_entity_ids=result_entity_ids,
            assistant_reply=result.reply,
            plan=plan_payload,
        )
        if plan is not None and result.intent in {
            "capture_task",
            "capture_reminder",
            "capture_memory",
            "update_knowledge",
            "rollback_knowledge_update",
            "mark_task_done",
            "cancel_task",
            "merge_tasks",
            "undo_last_action",
            "update_task_due",
            "update_task_priority",
            "update_task_recurrence",
        }:
            await self.event_service.append_event(
                EventType.CONVERSATION_PLAN_EXECUTED,
                "conversation_turn",
                request.source_message_id or f"{chat_id}:{int(now.timestamp())}",
                {
                    **plan_payload,
                    "result_intent": result.intent,
                    "result_entity_ids": result_entity_ids,
                },
            )

    async def _remember_reference(
        self,
        request: CaptureRequest,
        intent: str,
        reference: ResolvedReference,
        result_entity_ids: list[str] | None = None,
    ) -> None:
        if self.reference_resolver is None:
            return
        await self.reference_resolver.remember(
            request.source_chat_id,
            intent=intent,
            reference=reference,
            raw_text=request.raw_text,
            source_message_id=request.source_message_id,
            result_entity_ids=result_entity_ids,
        )

    async def _execute_knowledge_update(
        self,
        request: CaptureRequest,
        reference: ResolvedReference,
        plan: ConversationPlan | None,
    ) -> ConversationResult:
        if reference.entity_type not in {"project", "person", "organization"} or not reference.entity_id:
            return ConversationResult(
                intent="update_knowledge",
                reply=self.conversation_composer.missing_knowledge_target(),
            )
        statements = list(plan.statements) if plan is not None else []
        if not statements:
            return ConversationResult(
                intent="update_knowledge",
                reply=self.conversation_composer.missing_knowledge_payload(
                    reference.entity_name
                ),
            )
        response = await self.capture_service.capture_scoped_knowledge(
            request,
            entity_type=reference.entity_type,
            entity_id=reference.entity_id,
            entity_name=reference.entity_name or reference.entity_type,
            statements=statements,
        )
        await self._remember_reference(request, "update_knowledge", reference)
        return ConversationResult(
            intent="update_knowledge",
            reply=response.summary,
            captured=True,
        )

    async def _execute_knowledge_rollback(
        self, request: CaptureRequest, plan: ConversationPlan | None
    ) -> ConversationResult:
        response = await self.capture_service.rollback_recent_knowledge_update(
            request,
            requested_count=(plan.requested_count if plan is not None else None),
        )
        return ConversationResult(
            intent="rollback_knowledge_update",
            reply=response.summary,
            captured=response.memories_deleted > 0,
        )

    async def _knowledge_answer(
        self, raw_text: str, reference: ResolvedReference
    ) -> str:
        try:
            return await self.knowledge_query_service.answer(
                raw_text,
                entity_type=reference.entity_type,
                entity_id=reference.entity_id,
                entity_name=reference.entity_name,
            )
        except TypeError:
            return await self.knowledge_query_service.answer(raw_text)

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
        now_utc: datetime | None = None,
    ) -> RenameOutcome:
        selection = await self._resolve_task_reference(
            old_query,
            request.source_chat_id,
            intent="rename_task",
            title_hint=old_query,
            now=now_utc,
        )
        if selection.mode == TaskSelectionMode.NONE:
            return RenameOutcome(
                _localized(
                    request.raw_text,
                    f"Em không tìm thấy task đang mở nào khớp với '{old_query}'.",
                    f"I couldn't find any open task matching '{old_query}'.",
                )
            )

        chat_id = request.source_chat_id or "system"
        msg_id = request.source_message_id
        if selection.mode in {
            TaskSelectionMode.AMBIGUOUS,
            TaskSelectionMode.MULTIPLE,
        }:
            matches = list(selection.tasks)
            titles = "\n".join(f"{index}. {task.title}" for index, task in enumerate(matches[:5], 1))
            question = _localized(
                request.raw_text,
                f"Em thấy vài task có thể khớp. Anh muốn đổi tên task nào?\n{titles}",
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
            return RenameOutcome(
                question,
                _task_selection_keyboard(matches[:5]),
            )

        task = selection.tasks[0]
        operation = await self.task_operation_service.rename(
            task.id,
            new_title,
            source_note_id=note_id,
            transition="renamed_from_conversation",
        )

        reply = (
            "Đã sửa tiêu đề task:\n"
            f"Từ: “{task.title}”\n"
            f"Thành: “{new_title}”"
        )
        if operation.linked_artifacts_updated:
            reply += (
                f"\nEm cũng đã đồng bộ {operation.linked_artifacts_updated} lịch "
                "cùng hoạt động và cập nhật lại liên kết người/dự án."
            )

        if show_tasks:
            tasks_list = await self.secretary_service.tasks()
            reply += f"\n\n{tasks_list}"

        markup = _undo_keyboard(operation.event_id) if operation.event_id else None
        return RenameOutcome(reply, markup, task.id)

    async def _answer_profile_question(self, raw_text: str) -> str:
        memories = await self.memory_service.memory_repo.list_active()
        profile_memories = [item.content for item in memories if str(item.bucket) == "profile"]
        if profile_memories:
            return (
                "Anh đang có hai bối cảnh công việc chính:\n"
                "- Tại MindX, anh làm quản lý và vận hành trong mảng Vận hành Giảng dạy, "
                "với hai vai trò TEGL+ và TOM.\n"
                "- Tại STE, anh là người sáng lập và trực tiếp vận hành, tập trung vào dữ liệu/BI, "
                "xây dựng hệ thống, sản phẩm công nghệ, AI và giáo dục.\n"
                "Ngoài ra, anh còn tham gia giảng dạy và huấn luyện. Vì vậy, công việc của anh "
                "không gói gọn trong một nghề duy nhất; mô tả sát nhất là nhà quản lý vận hành, "
                "người xây dựng hệ thống và người sáng lập STE."
            )
        return "Em chưa có đủ memory để xác định nghề nghiệp hoặc vai trò hiện tại của anh."

    async def _answer_person_tasks(self, raw_text: str) -> str:
        people = await self._find_people_from_text(raw_text)
        if len(people) > 1:
            return _ambiguous_people_reply(people)
        person = people[0] if people else None
        if person is None:
            return "Anh muốn xem task của ai? Nói tên người đó giúp em nha."
        tasks = await self.task_repo.list_active_by_person(person.id)
        if not tasks:
            return f"Hiện em chưa thấy task đang mở nào được giao cho {person.display_name}."
        lines = [f"Task đang mở của {person.display_name}"]
        for index, task in enumerate(tasks, 1):
            due = _format_local_datetime(task.due_at, self.secretary_service.display_timezone) if task.due_at else "chưa có hạn"
            lines.append(f"{index}. {task.title} - Hạn: {due}")
        return "\n".join(lines)

    async def _assign_task_to_person(self, raw_text: str, note_id: str) -> str:
        people = await self._find_people_from_text(raw_text)
        if len(people) > 1:
            return _ambiguous_people_reply(people)
        person = people[0] if people else None
        title = _extract_assigned_task_title(raw_text)
        if person is None:
            return "Em hiểu là anh muốn giao task, nhưng chưa xác định được người nhận. Anh nói rõ tên người đó giúp em nha."
        if not title:
            return f"Anh muốn giao task gì cho {person.display_name}?"
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
        return f"Em đã tạo task giao cho {person.display_name}: {task.title}."

    async def _create_task_check_reminder(
        self,
        raw_text: str,
        note_id: str,
        now_utc: datetime | None = None,
    ) -> str:
        people = await self._find_people_from_text(raw_text)
        if len(people) > 1:
            return _ambiguous_people_reply(people)
        person = people[0] if people else None
        local_now = (
            now_utc.astimezone(self.secretary_service.display_timezone)
            if now_utc is not None
            else self.now_provider().astimezone(
                self.secretary_service.display_timezone
            )
        )
        due_at = parse_clarification_datetime(
            raw_text,
            now=local_now,
            default_timezone=self.secretary_service.display_timezone,
        )
        if due_at is None:
            return "Anh muốn em nhắc kiểm tra task này lúc nào? Ví dụ: 'mai 9h'."
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
        return f"Em đã đặt lịch nhắc anh kiểm tra task{person_text} vào {local}."

    async def _find_people_from_text(self, raw_text: str):
        if self.secretary_service.person_repo is None:
            return []
        found = {}
        names = _extract_possible_person_names(raw_text)
        for name in names:
            for person in await self.secretary_service.person_repo.find_matches(name):
                found[person.id] = person
        return list(found.values())

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

    async def _delete_all_tasks(self, raw_text: str, note_id: str) -> str:
        if not _is_delete_all_tasks(_normalize_text(raw_text)):
            return _localized(
                raw_text,
                "Anh muốn hủy toàn bộ task đang mở phải không? Hãy nói rõ: 'xoá toàn bộ task đang có'.",
                "Do you want to cancel all open tasks? Please say: 'clear all open tasks'.",
            )
        active_tasks = await self.task_repo.list_active()
        for task in active_tasks:
            await self.task_operation_service.cancel(
                task.id,
                source_note_id=note_id,
            )
        return _localized(
            raw_text,
            f"Đã hủy {len(active_tasks)} task đang mở.",
            f"Cancelled {len(active_tasks)} open task(s).",
        )

    async def remember_task_list(
        self,
        source_chat_id: str | None,
        text: str,
        source_view: str = "rendered",
        now_utc: datetime | None = None,
    ) -> None:
        if not source_chat_id:
            return
        explicit_ids = await self.secretary_service.ordered_task_ids_for_view(
            source_view,
            now=now_utc or self.now_provider().astimezone(UTC),
        )
        if explicit_ids:
            await self.task_list_context_repo.save(
                source_chat_id,
                explicit_ids,
                source_view,
                now=now_utc or self.now_provider().astimezone(UTC),
            )
            return
        active = await self.task_repo.list_active()
        by_title = {_normalize_text(task.title): task.id for task in active}
        ordered: list[str] = []
        for line in text.splitlines():
            match = re.match(r"^\s*\d+\.\s+(.+?)(?:\s+[—·]|$)", line)
            if not match:
                continue
            rendered_title = re.sub(
                r"^[^\wÀ-ỹ]+", "", match.group(1).strip(), flags=re.UNICODE
            )
            task_id = by_title.get(_normalize_text(rendered_title))
            if task_id and task_id not in ordered:
                ordered.append(task_id)
        if ordered:
            await self.task_list_context_repo.save(
                source_chat_id,
                ordered,
                source_view,
                now=now_utc or self.now_provider().astimezone(UTC),
            )

    async def _resolve_task_reference(
        self,
        raw_text: str,
        source_chat_id: str | None,
        *,
        intent: str,
        title_hint: str | None = None,
        force_confirmation: bool = False,
        now: datetime | None = None,
    ) -> ResolvedTaskSelection:
        selection = await self.task_reference_resolver.resolve(
            raw_text,
            source_chat_id,
            title_hint=title_hint,
            force_confirmation=force_confirmation,
            now=now,
        )
        await self.event_service.append_event(
            EventType.TASK_REFERENCE_RESOLVED,
            "task_reference",
            selection.task_ids[0] if selection.task_ids else "unresolved",
            {
                "intent": intent,
                "source": selection.source.value if selection.source else None,
                "source_view": selection.source_view,
                "mode": selection.mode.value,
                "candidate_count": selection.candidate_count,
                "selected_count": len(selection.tasks),
                "requires_confirmation": selection.requires_confirmation,
                "context_age_seconds": selection.context_age_seconds,
                "resolution_reason": selection.resolution_reason,
            },
            created_at=now,
        )
        return selection

    def is_explicit_new_action(self, text: str) -> bool:
        intent = self._deterministic_route(text)
        return intent in {
            "query_today",
            "query_tomorrow",
            "query_tasks",
            "query_task_recurrence",
            "query_reminders",
            "query_projects",
            "query_people",
            "query_commitments",
            "query_context",
            "query_meeting_prep",
            "cancel_task",
            "merge_tasks",
            "delete_all_tasks",
            "mark_task_done",
            "update_task_due",
            "update_task_priority",
            "update_task_recurrence",
            "capture_task",
            "capture_reminder",
            "capture_memory",
        } or text.strip().startswith("/")

    async def _answer_task_recurrence(
        self,
        raw_text: str,
        *,
        now_utc: datetime | None = None,
    ) -> str:
        tasks = await self.task_repo.list_active()
        normalized = _normalize_text(raw_text)
        if _is_daily_schedule_query(normalized):
            recurring = [task for task in tasks if task.recurrence_rule]
            if not recurring:
                return "Anh chưa có lịch định kỳ nào đang mở."
            lines = ["Lịch định kỳ của anh"]
            for index, task in enumerate(recurring, 1):
                schedule = _format_task_schedule(
                    task, self.secretary_service.display_timezone
                )
                lines.append(
                    f"{index}. {task.title} · {_recurrence_label(task.recurrence_rule)} · {schedule}"
                )
            return "\n".join(lines)
        if _is_recurrence_time_explanation_query(normalized):
            recurring = [
                task for task in tasks if task.recurrence_rule and task.due_at is not None
            ]
            if len(recurring) == 1:
                task = recurring[0]
                utc_due = task.due_at.astimezone(UTC).strftime("%H:%M")
                local_due = task.due_at.astimezone(
                    self.secretary_service.display_timezone
                ).strftime("%H:%M %d/%m/%Y")
                return (
                    f"Dạ, {utc_due} là giờ UTC được lưu nội bộ. Theo múi giờ của anh, "
                    f"kỳ kế tiếp của “{task.title}” vẫn là {local_due}. "
                    "Phản hồi trước đã hiển thị giờ UTC mà chưa đổi sang giờ địa phương."
                )

        query = _task_recurrence_query(raw_text)
        matches = _ranked_task_matches(query, tasks)
        if not matches:
            recent_done = await self.task_repo.list_done_since(
                (now_utc or self.now_provider().astimezone(UTC))
                - timedelta(days=30)
            )
            matches = _ranked_task_matches(query, recent_done)
        if not matches:
            return "Em chưa tìm thấy task anh đang hỏi."
        if len(matches) > 1:
            names = "\n".join(
                f"{index}. {task.title}" for index, task in enumerate(matches[:5], 1)
            )
            return f"Em thấy vài task cùng khớp. Anh chọn task giúp em nha:\n{names}"
        task = matches[0]
        if task.recurrence_rule:
            return (
                f"Dạ, “{task.title}” là task {_recurrence_label(task.recurrence_rule)}. "
                "Khi hoàn thành kỳ hiện tại, em sẽ tạo kỳ kế tiếp."
            )
        return f"“{task.title}” hiện không phải task định kỳ."

    async def _cancel_task(
        self,
        raw_text: str,
        note_id: str,
        request: CaptureRequest,
        now_utc: datetime | None = None,
    ) -> str:
        selection = await self._resolve_task_reference(
            raw_text,
            request.source_chat_id,
            intent="cancel_task",
            title_hint=_task_cancel_query(raw_text),
            now=now_utc,
        )
        if selection.mode == TaskSelectionMode.NONE:
            return (
                "Em chưa tìm thấy task cần bỏ. Anh gửi đúng tên task hoặc dùng "
                "“bỏ task 2” ngay sau danh sách nha."
            )
        if selection.mode in {
            TaskSelectionMode.AMBIGUOUS,
            TaskSelectionMode.MULTIPLE,
        }:
            matches = list(selection.tasks)
            titles = "\n".join(
                f"{index}. {item.title}" for index, item in enumerate(matches[:5], 1)
            )
            question = f"Em thấy vài task cùng khớp. Anh muốn bỏ task nào?\n{titles}"
            if self.capture_service.clarification_service:
                await self._create_clarification_request(
                    ClarificationRequest(
                        source_chat_id=request.source_chat_id or "system",
                        source_message_id=request.source_message_id,
                        entity_type="task_selection_cancel",
                        entity_id=",".join(item.id for item in matches[:5]),
                        field_name="status|cancelled",
                        question=question,
                    )
                )
            return question
        task = selection.tasks[0]
        await self.task_operation_service.cancel(
            task.id,
            source_note_id=note_id,
        )
        return f"Đã bỏ task: {task.title}."

    async def _merge_tasks(
        self,
        raw_text: str,
        note_id: str,
        plan: ConversationPlan | None = None,
        now_utc: datetime | None = None,
    ) -> str:
        active = await self.task_repo.list_active()
        selected: list[Task] = []
        if plan is not None and len(plan.target_entity_ids) == 2:
            by_id = {task.id: task for task in active}
            selected = [
                by_id[task_id]
                for task_id in plan.target_entity_ids
                if task_id in by_id
            ]
        if not selected:
            parts = _parse_task_merge_request(raw_text)
            if parts is None:
                return (
                    "Em hiểu anh muốn gộp task nhưng chưa xác định đúng hai task. "
                    "Anh nói tên hai task hoặc dùng số trong danh sách vừa xem giúp em nha."
                )
            for part in parts:
                selection = await self._resolve_task_reference(
                    part,
                    None,
                    intent="merge_tasks",
                    title_hint=part,
                    now=now_utc,
                )
                matches = [
                    task for task in selection.tasks if task not in selected
                ]
                if (
                    selection.mode != TaskSelectionMode.SINGLE
                    or len(matches) != 1
                ):
                    return (
                        f"Em chưa xác định duy nhất task khớp với “{part}”. "
                        "Anh dùng đúng tên hai task giúp em nha."
                    )
                selected.append(matches[0])
        if len(selected) != 2:
            return "Em cần đúng hai task khác nhau để gộp."

        due_values = [task.due_at for task in selected if task.due_at is not None]
        due_at = min(due_values) if due_values else None
        same_person = selected[0].person_id == selected[1].person_id
        same_project = selected[0].project_id == selected[1].project_id
        same_recurrence = selected[0].recurrence_rule == selected[1].recurrence_rule
        duration_values = [task.duration_minutes for task in selected if task.duration_minutes]
        merged = Task(
            title=f"{selected[0].title} và {selected[1].title}",
            description="\n".join(
                value for value in (selected[0].description, selected[1].description) if value
            ),
            status=TaskStatus.OPEN,
            priority=(
                "high" if any(task.priority == "high" for task in selected)
                else selected[0].priority
            ),
            due_at=due_at,
            duration_minutes=max(duration_values) if duration_values else None,
            project_id=selected[0].project_id if same_project else None,
            person_id=selected[0].person_id if same_person else None,
            source_note_id=note_id,
            confidence=min(task.confidence for task in selected),
            recurrence_rule=selected[0].recurrence_rule if same_recurrence else None,
        )
        if merged.recurrence_rule:
            merged.recurrence_series_id = merged.id
            merged.recurrence_occurrence_at = merged.due_at
        async with self.task_repo.database.transaction():
            await self.task_repo.create(merged)
            for task in selected:
                await self.task_operation_service.cancel(
                    task.id,
                    source_note_id=note_id,
                )
            reconciler = self.task_operation_service.activity_reconciliation_service
            if reconciler is not None:
                await reconciler.transfer_task_links(
                    [task.id for task in selected], merged.id
                )
            await self.event_service.append_event(
                EventType.WORK_ITEM_CHANGED,
                "task",
                merged.id,
                {
                    "action": "merge_tasks",
                    "source_note_id": note_id,
                    "merged_task_ids": [task.id for task in selected],
                    "before": [
                        {
                            "id": task.id,
                            "title": task.title,
                            "status": str(task.status),
                            "due_at": task.due_at.isoformat() if task.due_at else None,
                        }
                        for task in selected
                    ],
                },
            )
        return self.conversation_composer.tasks_merged(
            merged, self.secretary_service.display_timezone
        )

    async def _undo_last_action(self, plan: ConversationPlan | None) -> str:
        target_ids = list(plan.target_entity_ids) if plan is not None else []
        if len(target_ids) != 1:
            return (
                "Em chưa xác định được thay đổi ngay trước đó để hoàn tác. "
                "Anh nói rõ task hoặc dùng nút ↩ Hoàn tác ngay sau thao tác giúp em nha."
            )
        merged_task_id = target_ids[0]
        events = await self.event_service.list_events_for_entity(
            "task", merged_task_id
        )
        rename_event = next(
            (
                event
                for event in reversed(events)
                if event.event_type == EventType.WORK_ITEM_CHANGED
                and event.payload.get("action") == "rename_task"
            ),
            None,
        )
        if rename_event is not None and not await self.event_service.was_undone(
            rename_event.id
        ):
            restored = await self.task_operation_service.undo_event(rename_event.id)
            if restored.task is not None:
                return f"Dạ, em đã hoàn tác. Task trở lại thành “{restored.task.title}”."
        merge_event = next(
            (
                event
                for event in reversed(events)
                if event.event_type == EventType.WORK_ITEM_CHANGED
                and event.payload.get("action") == "merge_tasks"
            ),
            None,
        )
        if merge_event is None:
            return (
                "Thay đổi vừa rồi chưa có dữ liệu hoàn tác an toàn. "
                "Em chưa tự ý sửa ngược để tránh làm sai task."
            )
        if await self.event_service.was_undone(merge_event.id):
            return "Thay đổi này đã được hoàn tác trước đó."

        previous = merge_event.payload.get("before", [])
        if len(previous) != 2:
            return "Em thiếu snapshot của hai task cũ nên chưa thể hoàn tác an toàn."
        async with self.task_repo.database.transaction():
            for snapshot in previous:
                await self.task_repo.update_status(
                    snapshot["id"], snapshot.get("status") or TaskStatus.OPEN.value
                )
            await self.task_repo.delete(merged_task_id)
            await self.event_service.append_event(
                EventType.WORK_ITEM_UNDONE,
                "work_event",
                merge_event.id,
                {
                    "restored_task_ids": [item["id"] for item in previous],
                    "deleted_merged_task_id": merged_task_id,
                },
            )
        return "Dạ, em đã hoàn tác việc gộp và khôi phục hai task cũ."

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
                "Em chưa thấy nội dung trước đó để tạo task mới. Anh gửi lại nội dung task giúp em nha.",
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
        now_utc: datetime | None = None,
    ) -> str | tuple[str, Any]:
        query = target_entity_hints if target_entity_hints else _task_completion_query(raw_text)
        if not query:
            query = _task_completion_query(raw_text)
        selection = await self._resolve_task_reference(
            raw_text,
            request.source_chat_id,
            intent="mark_task_done",
            title_hint=query or None,
            force_confirmation=ambiguity_detected,
            now=now_utc,
        )
        if selection.mode == TaskSelectionMode.NONE:
            return _localized(
                raw_text,
                "Em chưa tìm thấy task khớp để đánh dấu xong. Anh nói rõ tên task giúp em nha?",
                "I could not find a matching open task. Which task should I mark done?",
            )
        chat_id = request.source_chat_id or "system"
        msg_id = request.source_message_id
        if selection.mode == TaskSelectionMode.AMBIGUOUS:
            matches = list(selection.tasks)
            titles = "\n".join(
                f"{index}. {task.title}" for index, task in enumerate(matches[:5], 1)
            )
            question = _localized(
                raw_text,
                f"Em thấy vài task có thể khớp. Anh muốn đánh dấu task nào?\n{titles}",
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
            return question, _task_selection_keyboard(matches[:5])

        if selection.mode == TaskSelectionMode.MULTIPLE:
            if selection.requires_confirmation:
                question = (
                    f"Anh xác nhận đánh dấu xong {len(selection.tasks)} task này nhé?\n"
                    + batch_preview_text(
                        list(selection.tasks),
                        self.secretary_service.display_timezone,
                    )
                )
                if self.capture_service.clarification_service:
                    snapshots = [
                        TaskBatchSnapshot.from_task(task)
                        for task in selection.tasks
                    ]
                    await self._create_clarification_request(
                        ClarificationRequest(
                            source_chat_id=chat_id,
                            source_message_id=msg_id,
                            entity_type="task_bulk_done",
                            entity_id=",".join(selection.task_ids),
                            field_name=encode_batch_field(snapshots),
                            question=question,
                        )
                    )
                return question, _batch_confirmation_keyboard()
            batch = await self.task_operation_service.complete_many(
                selection.task_ids,
                transition=f"completed_from_{selection.source.value}",
                source_note_id=note_id,
                now=now_utc,
            )
            reply = _batch_completion_reply(
                batch.results,
                self.secretary_service.display_timezone,
                now=now_utc,
            )
            backlog_prompt = await self._recurrence_backlog_prompt(
                request,
                list(batch.results),
            )
            if backlog_prompt is not None:
                question, _keyboard = backlog_prompt
                return (
                    f"{reply}\n{question}",
                    _batch_result_keyboard(
                        batch.batch_event_id,
                        include_backlog=True,
                    ),
                )
            if batch.batch_event_id is not None:
                return reply, _undo_keyboard(batch.batch_event_id)
            return reply

        task = selection.tasks[0]
        if selection.requires_confirmation:
            question = _localized(
                raw_text,
                f"Anh muốn đánh dấu xong task '{task.title}' phải không?",
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
            return question, _confirmation_keyboard()

        operation = await self.task_operation_service.complete(
            task.id,
            transition=(
                "completed_from_number_reference"
                if selection.source == TaskSelectionSource.NUMBER
                else "completed_from_conversation"
            ),
            source_note_id=note_id,
            now=now_utc,
        )
        reply = _completion_reply(
            operation.task,
            operation.next_task,
            self.secretary_service.display_timezone,
            now=now_utc,
        )
        backlog_prompt = await self._recurrence_backlog_prompt(
            request,
            [operation],
        )
        if backlog_prompt is not None:
            question, keyboard = backlog_prompt
            return f"{reply}\n{question}", keyboard
        return reply

    async def _recurrence_backlog_prompt(
        self,
        request: CaptureRequest,
        results: list[TaskOperationResult],
    ) -> tuple[str, Any] | None:
        clarification_service = self.capture_service.clarification_service
        if request.source_chat_id is None or clarification_service is None:
            return None
        backlogs = [
            result.recurrence_backlog
            for result in results
            if result.recurrence_backlog is not None
        ]
        pending = await clarification_service.request_recurrence_backlog(
            request.source_chat_id,
            backlogs,
            source_message_id=request.source_message_id,
        )
        if pending is None:
            return None
        return (
            pending.question,
            _clarification_keyboard(["Giữ từng kỳ", "Bỏ qua kỳ đã lỡ"]),
        )

    async def _update_task_due(
        self,
        raw_text: str,
        note_id: str,
        request: CaptureRequest,
        target_entity_hints: str | None = None,
        ambiguity_detected: bool = False,
        now_utc: datetime | None = None,
    ) -> str | tuple[str, Any]:
        due_at = _extract_task_due_at(
            raw_text,
            self.secretary_service.display_timezone,
            now=now_utc,
        )
        duration_minutes = _extract_duration_minutes(raw_text)
        if due_at is None:
            recent_task = await self._recent_task_for_due_update(
                request,
                target_entity_hints,
                now_utc=now_utc,
            )
            if recent_task is not None and self.capture_service.clarification_service:
                question = _localized(
                    raw_text,
                    f"Anh muốn đổi hạn task '{recent_task.title}' sang lúc nào? Nói kiểu 'hôm nay 19h' giúp em nha.",
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
                "Em hiểu là anh muốn đổi hạn task, nhưng chưa đọc được giờ/hạn mới. Anh nói lại kiểu 'hôm nay 17h' giúp em nha?",
                "I understand you want to change a task deadline, but I could not read the new time.",
            )
        query = target_entity_hints if target_entity_hints else _task_due_update_query(raw_text)
        if not query:
            query = _task_due_update_query(raw_text)
        selection = await self._resolve_task_reference(
            raw_text,
            request.source_chat_id,
            intent="update_task_due",
            title_hint=query or None,
            force_confirmation=ambiguity_detected,
            now=now_utc,
        )
        if selection.mode == TaskSelectionMode.NONE:
            return _localized(
                raw_text,
                "Em chưa tìm thấy task khớp để đổi hạn. Anh nói rõ tên task cần sửa giúp em nha?",
                "I could not find a matching open task. Which task should I update?",
            )
        chat_id = request.source_chat_id or "system"
        msg_id = request.source_message_id
        formatted_due = _format_local_datetime(due_at, self.secretary_service.display_timezone)
        if selection.mode in {
            TaskSelectionMode.AMBIGUOUS,
            TaskSelectionMode.MULTIPLE,
        }:
            matches = list(selection.tasks)
            titles = "\n".join(f"{index}. {task.title}" for index, task in enumerate(matches[:5], 1))
            question = _localized(
                raw_text,
                f"Em thấy vài task có thể khớp. Anh muốn đổi hạn task nào?\n{titles}",
                f"I found a few possible tasks. Which one should I update?\n{titles}",
            )
            if self.capture_service.clarification_service:
                clar_req = ClarificationRequest(
                    source_chat_id=chat_id,
                    source_message_id=msg_id,
                    entity_type="task_selection_due_update",
                    entity_id=",".join(task.id for task in matches[:5]),
                    field_name=(
                        f"due_at|{due_at.isoformat()}"
                        + (f"|duration={duration_minutes}" if duration_minutes else "")
                    ),
                    question=question,
                )
                await self._create_clarification_request(clar_req)
            return question, _task_selection_keyboard(matches[:5])

        task = selection.tasks[0]
        requested_recurrence_rule = _requested_recurrence_rule(raw_text)
        recurrence_rule = requested_recurrence_rule or task.recurrence_rule
        if recurrence_rule is not None:
            existing = task.recurrence_rule is not None
            question = f"Anh muốn áp dụng thế nào cho ‘{task.title}’?"
            options = (
                ["Chỉ kỳ này", "Kỳ này và các kỳ sau", "Hủy"]
                if existing
                else ["Chỉ lần này", "Lặp hằng ngày" if recurrence_rule == "daily" else "Lặp hằng tuần", "Hủy"]
            )
            await self._create_clarification_request(
                ClarificationRequest(
                    source_chat_id=chat_id,
                    source_message_id=msg_id,
                    entity_type="task_recurrence_scope",
                    entity_id=task.id,
                    field_name=(
                        f"due_at|{due_at.isoformat()}|{recurrence_rule}"
                        + (f"|duration={duration_minutes}" if duration_minutes else "")
                    ),
                    question=question,
                )
            )
            return question, _clarification_keyboard(options)
        if selection.requires_confirmation:
            question = _localized(
                raw_text,
                f"Anh muốn đổi hạn task '{task.title}' thành {formatted_due} phải không?",
                f"Do you want to update the deadline for task '{task.title}' to {formatted_due}?",
            )
            if self.capture_service.clarification_service:
                clar_req = ClarificationRequest(
                    source_chat_id=chat_id,
                    source_message_id=msg_id,
                    entity_type="task",
                    entity_id=task.id,
                    field_name=(
                        f"due_at|{due_at.isoformat()}"
                        + (f"|duration={duration_minutes}" if duration_minutes else "")
                    ),
                    question=question,
                )
                await self._create_clarification_request(clar_req)
            return question, _confirmation_keyboard()

        async with self.task_repo.database.transaction():
            await self.task_repo.update_due_at(task.id, due_at)
            if duration_minutes is not None:
                await self.task_repo.update_duration(task.id, duration_minutes)
            await self.event_service.append_event(
                EventType.NOTE_PROCESSED,
                "task",
                task.id,
                {
                    "source_note_id": note_id,
                    "conversation_intent": "update_task_due",
                    "due_at": due_at.isoformat(),
                    "duration_minutes": duration_minutes,
                },
            )
        return _localized(
            raw_text,
            (
                f"Đã đổi lịch: {task.title} -> {formatted_due}, "
                f"kéo dài {duration_minutes} phút"
                if duration_minutes
                else f"Đã đổi hạn: {task.title} -> {formatted_due}"
            ),
            f"Updated deadline: {task.title} -> {formatted_due}",
        )

    async def _snooze_reminder(
        self,
        raw_text: str,
        request: CaptureRequest,
        now_utc: datetime | None = None,
    ) -> str:
        remind_at = _extract_snooze_at(
            raw_text,
            self.secretary_service.display_timezone,
            now=now_utc,
        )
        if remind_at is None:
            return _localized(
                raw_text,
                "Em hiểu là anh muốn nhắc lại, nhưng chưa đọc được mốc mới. Anh nói kiểu 'chiều mai', 'mai 9h' hoặc '2 tiếng sau' giúp em nha.",
                "I understand you want to snooze a reminder, but I could not read the new time.",
            )
        reminder = await self._resolve_reminder_for_snooze(raw_text)
        if reminder is None:
            return _localized(
                raw_text,
                "Em chưa tìm thấy reminder khớp để nhắc lại. Anh nói rõ tên reminder cần dời giúp em nha.",
                "I could not find a matching reminder to snooze.",
            )
        before = {
            "title": reminder.title,
            "status": str(reminder.status),
            "remind_at": reminder.remind_at.isoformat() if reminder.remind_at else None,
        }
        await self.secretary_service.reminder_repo.update_schedule(
            reminder.id,
            remind_at,
            ReminderStatus.SCHEDULED,
        )
        after_reminder = await self.secretary_service.reminder_repo.get_by_id(reminder.id)
        after = {
            "title": after_reminder.title if after_reminder else reminder.title,
            "status": str(after_reminder.status if after_reminder else ReminderStatus.SCHEDULED),
            "remind_at": (
                after_reminder.remind_at.isoformat()
                if after_reminder and after_reminder.remind_at
                else remind_at.isoformat()
            ),
        }
        await self.event_service.append_event(
            EventType.WORK_ITEM_CHANGED,
            "reminder",
            reminder.id,
            {
                "action": "snooze_reminder",
                "before": before,
                "after": after,
                "source_chat_id": request.source_chat_id,
                "source_message_id": request.source_message_id,
            },
            created_at=now_utc,
        )
        formatted = _format_local_datetime(remind_at, self.secretary_service.display_timezone)
        return _localized(
            raw_text,
            f"Đã dời reminder '{reminder.title}' sang {formatted}.",
            f"Snoozed reminder '{reminder.title}' to {formatted}.",
        )

    async def _resolve_reminder_for_snooze(self, raw_text: str) -> Reminder | None:
        query = _reminder_snooze_query(raw_text)
        reminders = [
            reminder
            for reminder in await self.secretary_service.reminder_repo.list_all()
            if str(reminder.status) in {
                ReminderStatus.CANDIDATE.value,
                ReminderStatus.SCHEDULED.value,
                ReminderStatus.SENT.value,
                "candidate",
                "scheduled",
                "sent",
            }
        ]
        if not reminders:
            return None
        if query:
            matches = [
                reminder
                for reminder in reminders
                if _is_strong_match(query, reminder.title)
                or _token_overlap_score(query, reminder.title) >= 0.5
            ]
            if matches:
                matches.sort(
                    key=lambda item: (
                        item.remind_at or item.updated_at,
                        item.updated_at,
                    ),
                    reverse=True,
                )
                return matches[0]
        if _has_contextual_snooze_reference(_normalize_text(raw_text)):
            reminders.sort(
                key=lambda item: (
                    item.remind_at or item.updated_at,
                    item.updated_at,
                ),
                reverse=True,
            )
            return reminders[0]
        return None

    async def _record_task_completion(
        self,
        task: Task | None,
        next_task: Task | None,
        created: bool,
        note_id: str,
    ) -> None:
        if task is None:
            return
        await self.event_service.append_event(
            EventType.TASK_DONE,
            "task",
            task.id,
            {
                "source_note_id": note_id,
                "transition": "completed_from_conversation",
                "recurrence_rule": task.recurrence_rule,
                "next_task_id": next_task.id if next_task else None,
            },
        )
        if next_task is not None and created:
            await self.event_service.append_event(
                EventType.TASK_RECURRENCE_SCHEDULED,
                "task",
                next_task.id,
                {
                    "previous_task_id": task.id,
                    "recurrence_rule": task.recurrence_rule,
                    "due_at": next_task.due_at.isoformat() if next_task.due_at else None,
                },
            )

    async def _update_task_priority(
        self,
        raw_text: str,
        note_id: str,
        request: CaptureRequest,
        now_utc: datetime | None = None,
    ) -> str:
        selection = await self._resolve_task_reference(
            raw_text,
            request.source_chat_id,
            intent="update_task_priority",
            title_hint=_task_priority_query(raw_text),
            now=now_utc,
        )
        task = (
            selection.tasks[0]
            if selection.mode == TaskSelectionMode.SINGLE
            else None
        )
        priority = _requested_priority(raw_text)
        if task is None or priority is None:
            return "Em chưa xác định được task hoặc mức ưu tiên. Anh nói kiểu “đổi priority task 2 thành cao” nha."
        await self.task_repo.update_priority(task.id, priority)
        await self.event_service.append_event(
            EventType.WORK_ITEM_CHANGED,
            "task",
            task.id,
            {"source_note_id": note_id, "field": "priority", "new": priority},
        )
        return f"Dạ, em đã đổi ưu tiên của “{task.title}” thành {_priority_label(priority)}."

    async def _update_task_recurrence(
        self,
        raw_text: str,
        note_id: str,
        request: CaptureRequest,
        now_utc: datetime | None = None,
    ) -> str:
        query = _task_recurrence_update_query(raw_text)
        selection = await self._resolve_task_reference(
            raw_text,
            request.source_chat_id,
            intent="update_task_recurrence",
            title_hint=query,
            now=now_utc,
        )
        task = (
            selection.tasks[0]
            if selection.mode == TaskSelectionMode.SINGLE
            else None
        )
        rule = _requested_recurrence_rule(raw_text)
        if task is None or rule is None:
            return "Em chưa xác định được task hoặc chu kỳ lặp. Anh nói kiểu “cho task 2 lặp hằng tuần” nha."
        await self.task_repo.update_recurrence(task.id, rule)
        await self.event_service.append_event(
            EventType.WORK_ITEM_CHANGED,
            "task",
            task.id,
            {"source_note_id": note_id, "field": "recurrence_rule", "new": rule},
        )
        return f"Dạ, em đã đặt “{task.title}” lặp {_recurrence_label(rule)}."

    async def _recent_task_for_due_update(
        self,
        request: CaptureRequest,
        target_entity_hints: str | None,
        *,
        now_utc: datetime | None = None,
    ) -> Task | None:
        query = target_entity_hints or _task_due_update_query(request.raw_text)
        if query:
            selection = await self._resolve_task_reference(
                request.raw_text,
                request.source_chat_id,
                intent="update_task_due",
                title_hint=query,
                now=now_utc,
            )
            if selection.mode == TaskSelectionMode.SINGLE:
                return selection.tasks[0]
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
            "Anh muốn em xoá memory nào? Nói thêm vài từ trong nội dung đó giúp em nha.",
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
                await self.task_operation_service.cancel(
                    task.id,
                    source_note_id=note_id,
                )
                await self.event_service.append_event(
                    EventType.NOTE_PROCESSED,
                    "note",
                    note_id,
                    {"conversation_intent": "memory_correction", "cancelled_task_id": task.id},
                )
                await self._record_correction_feedback(
                    "task",
                    task.id,
                    note_id,
                    request,
                    "cancel_misclassified_task",
                )
                return _localized(
                    raw_text,
                    f"Đã hủy task gần nhất: {task.title}",
                    f"Cancelled the recent task: {task.title}",
                )
            if len(tasks) > 1:
                titles = "\n".join(f"{index}. {task.title}" for index, task in enumerate(tasks, 1))
                question = _localized(
                    raw_text,
                    f"Em thấy vài task gần đây. Cái nào không phải task?\n{titles}",
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
                await self._record_correction_feedback(
                    "memory_item",
                    mem.id,
                    note_id,
                    request,
                    "reject_misclassified_memory",
                )
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
                await self._record_correction_feedback(
                    "task",
                    task.id,
                    note_id,
                    request,
                    "undo_recent_task_capture",
                )
                return _localized(
                    raw_text,
                    f"Đã hủy task gần nhất: {task.title}",
                    f"Cancelled the recent task: {task.title}",
                )
            elif target == "memory":
                await self.memory_service.reject(mem.id)
                await self._record_correction_feedback(
                    "memory_item",
                    mem.id,
                    note_id,
                    request,
                    "undo_recent_memory_capture",
                )
                return _localized(
                    raw_text,
                    f"Đã bỏ memory gần nhất: {mem.content}",
                    f"Rejected the recent memory: {mem.content}",
                )

        return _localized(
            raw_text,
            "Em chưa đủ ngữ cảnh để sửa đúng mục. Anh nói rõ task hoặc memory nào cần sửa nha?",
            "I need a little more context. Which task or memory should I correct?",
        )

    async def _record_correction_feedback(
        self,
        artifact_type: str,
        artifact_id: str,
        note_id: str,
        request: CaptureRequest,
        action: str,
    ) -> None:
        await self.event_service.record_feedback(
            FeedbackSignal.CORRECTION,
            artifact_type,
            artifact_id,
            source_chat_id=request.source_chat_id,
            source_message_id=request.source_message_id,
            source_note_id=note_id,
            action=action,
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


def _is_cancel_task(normalized: str) -> bool:
    if _is_delete_all_tasks(normalized) or _is_memory_delete(normalized):
        return False
    has_cancel_signal = any(
        signal in normalized
        for signal in ("xoa", "huy", "bo task", "bo viec", "delete", "cancel", "remove")
    )
    has_task_reference = any(
        signal in normalized for signal in ("task", "viec", "cong viec")
    ) or re.search(r"\b(?:task\s*)?\d+\b", normalized) is not None
    return has_cancel_signal and has_task_reference


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
    if _is_cancel_task(normalized):
        return "cancel_task"
    if _parse_task_merge_request(raw_text) is not None:
        return "merge_tasks"
    if _is_future_task_capture(normalized):
        return "capture_task"
    if _is_task_due_update(normalized):
        return "update_task_due"
    if _is_task_priority_update(normalized):
        return "update_task_priority"
    if _is_task_recurrence_update(normalized):
        return "update_task_recurrence"
    if _is_reminder_snooze(normalized):
        return "snooze_reminder"
    if _is_task_recurrence_query(normalized):
        return "query_task_recurrence"
    if _is_daily_schedule_query(normalized):
        return "query_task_recurrence"
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
    if _is_origin_query(normalized):
        return "query_origin"
    if _is_decision_timeline_query(normalized):
        return "query_decisions"
    if _is_timeline_query(normalized):
        return "query_timeline"
    if _is_search_query(normalized):
        return "query_search"
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
    action_keywords = (
        "xong", "done", "finished", "completed", "mua", "lam", "doi", "sua",
        "cap nhat", "xoa", "forget", "delete",
    )
    if any(keyword in normalized for keyword in action_keywords):
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
        text = f"Em đã ghi nhận: {response.summary}\nĐã tạo/cập nhật: {', '.join(created_parts)}."
    else:
        text = f"Em đã lưu ghi chú: {response.summary}"
    if response.memories_deleted:
        text += f"\nĐã xoá {response.memories_deleted} memory khớp."
    if response.errors:
        text += "\nCó phần em chưa trích xuất chắc chắn, nhưng ghi chú gốc đã được lưu."
    if response.clarification_question:
        text += f"\n{response.clarification_question}"
    if response.duplicate_suggestions:
        text += "\n\nGợi ý kiểm tra trùng:\n- " + "\n- ".join(response.duplicate_suggestions)
    if response.entity_suggestion_ids:
        text += "\nEm nhận thấy một biệt danh/liên kết mới và cần anh xác nhận."
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
        "danh dau",
        "mark",
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
        "la",
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
        r"\b(?:lai|gio|han|chot|deadline|due|due date|han chot|thanh|la|luc|vao|cho|task|viec|hom nay|toi nay|today|hang ngay|hang tuan|moi ngay|moi tuan)\b",
    )
    for pattern in cleanup_patterns:
        query = re.sub(pattern, " ", query)
    return " ".join(query.split())


def _reminder_snooze_query(raw_text: str) -> str:
    query = _normalize_text(raw_text)
    cleanup_patterns = (
        r"\b\d{1,3}\s*(?:phut|tieng|gio|ngay|minute|minutes|hour|hours|day|days)\s*(?:sau)?\b",
        r"\b\d{1,2}(?:h\d{0,2}|:\d{2})?\s*(?:am|pm)?\b",
        r"\b(?:nhac|nhac lai|remind|reminder|snooze|doi|doi lich|doi gio|dời|defer|again)\b",
        r"\b(?:lai|cho|giup|em|anh|vao|luc|sang|chieu|toi|trua|hom nay|ngay mai|mai|today|tomorrow|nay)\b",
    )
    for pattern in cleanup_patterns:
        query = re.sub(pattern, " ", query)
    return " ".join(query.split())


def _extract_snooze_at(
    raw_text: str,
    display_timezone,
    *,
    now: datetime | None = None,
) -> datetime | None:
    normalized = _normalize_text(raw_text)
    text = raw_text
    has_clock = _parse_clock_time(normalized) is not None
    has_relative = re.search(
        r"\b\d{1,3}\s*(?:phut|tieng|gio|ngay|minute|minutes|hour|hours|day|days)\s*(?:sau)?\b",
        normalized,
    ) is not None
    if not has_clock and not has_relative:
        if any(marker in normalized for marker in ("chieu", "afternoon")):
            text = f"{raw_text} 15h"
        elif any(marker in normalized for marker in ("toi", "evening", "tonight")):
            text = f"{raw_text} 19h"
        elif any(marker in normalized for marker in ("trua", "noon")):
            text = f"{raw_text} 12h"
        elif any(marker in normalized for marker in ("sang", "morning")):
            text = f"{raw_text} 9h"
    local_now = (
        now.astimezone(display_timezone)
        if now is not None
        else datetime.now(display_timezone)
    )
    return parse_clarification_datetime(
        text,
        now=local_now,
        default_timezone=display_timezone,
    )


def _task_cancel_query(raw_text: str) -> str:
    query = _normalize_text(raw_text)
    query = re.sub(
        r"\b(?:xoa|huy|bo|delete|cancel|remove|task|viec|cong viec|di|giup em|giup)\b",
        " ",
        query,
    )
    query = re.sub(r"^\s*\d+\s*", " ", query)
    return " ".join(query.split())


def _task_priority_query(raw_text: str) -> str:
    query = _normalize_text(raw_text)
    query = re.sub(
        r"\b(?:doi|sua|cap nhat|chinh|uu tien|priority|task|viec|thanh|cao|thap|high|low|medium|trung binh|vua)\b",
        " ",
        query,
    )
    return " ".join(query.split())


def _task_recurrence_update_query(raw_text: str) -> str:
    query = _normalize_text(raw_text)
    query = re.sub(
        r"\b(?:cho|dat|doi|sua|cap nhat|task|viec|lap|dinh ky|recurring|"
        r"hang ngay|hang tuan|moi ngay|moi tuan|daily|weekly|every|ngay|tuan|day|days|week|weeks)\b",
        " ",
        query,
    )
    return " ".join(query.split())


def _task_number_reference(raw_text: str) -> int | None:
    normalized = _normalize_text(raw_text)
    match = re.search(
        r"\b(?:(?:task|viec|so)\s*(\d+)|cai\s+thu\s+(\d+))\b",
        normalized,
    )
    if match:
        return int(match.group(1) or match.group(2))
    if any(
        signal in normalized
        for signal in (
            "xoa",
            "huy",
            "bo",
            "delete",
            "cancel",
            "hoan thanh",
            "xong",
            "doi",
            "sua",
            "priority",
            "uu tien",
            "lap",
        )
    ):
        match = re.search(r"\b(\d+)\b", normalized)
        if match:
            return int(match.group(1))
    return None


def _requested_recurrence_rule(raw_text: str) -> str | None:
    normalized = _normalize_text(raw_text)
    interval = re.search(
        r"\b(?:moi|every)\s+(\d+)\s+(ngay|day|days|tuan|week|weeks)\b",
        normalized,
    )
    if interval:
        count = int(interval.group(1))
        if count >= 1:
            unit = interval.group(2)
            return f"interval:{count}{'w' if unit in {'tuan', 'week', 'weeks'} else 'd'}"
    if any(value in normalized for value in ("hang ngay", "moi ngay", "daily")):
        return "daily"
    if any(value in normalized for value in ("hang tuan", "moi tuan", "weekly")):
        return "weekly"
    return None


def _requested_priority(raw_text: str) -> str | None:
    normalized = _normalize_text(raw_text)
    if any(value in normalized for value in ("uu tien", "priority")) and re.search(
        r"\b(?:cao|high)\b", normalized
    ):
        return "high"
    if any(value in normalized for value in ("uu tien", "priority")) and re.search(
        r"\b(?:thap|low)\b", normalized
    ):
        return "low"
    if any(value in normalized for value in ("uu tien", "priority")) and re.search(
        r"\b(?:vua|trung binh|medium)\b", normalized
    ):
        return "medium"
    return None


def _is_task_priority_update(normalized: str) -> bool:
    return (
        any(value in normalized for value in ("priority", "uu tien"))
        and (
            _task_number_reference(normalized) is not None
            or "task" in normalized
            or "viec" in normalized
            or any(
                action in normalized
                for action in ("doi", "sua", "cap nhat", "chinh")
            )
        )
        and _requested_priority(normalized) is not None
    )


def _is_task_recurrence_update(normalized: str) -> bool:
    has_imperative = any(
        normalized.startswith(action)
        for action in ("cho ", "dat ", "doi ", "sua ", "cap nhat ")
    )
    return (
        (_task_number_reference(normalized) is not None or has_imperative)
        and _requested_recurrence_rule(normalized) is not None
        and _parse_clock_time(normalized) is None
    )


def _is_task_recurrence_query(normalized: str) -> bool:
    if _is_recurrence_time_explanation_query(normalized):
        return True
    has_recurrence = any(
        value in normalized
        for value in ("task dinh ky", "viec dinh ky", "recurring task", "lap hang")
    )
    has_question = any(
        value in normalized
        for value in (
            "co phai",
            "khong phai",
            "phai khong",
            "a",
            "ha",
            "is this",
            "is my",
        )
    )
    return has_recurrence and has_question


def _is_recurrence_time_explanation_query(normalized: str) -> bool:
    has_recurrence = any(
        value in normalized
        for value in ("dinh ky", "hang ngay", "hang tuan", "recurring")
    )
    has_clock = re.search(r"\b\d{1,2}(?:h\s*|\s+)\d{2}\b", normalized) is not None
    asks_explanation = any(
        value in normalized
        for value in ("tai sao", "vi sao", "sao lai", "nho la", "why", "remember")
    )
    return has_recurrence and has_clock and asks_explanation


def _task_recurrence_query(raw_text: str) -> str:
    query = _normalize_text(raw_text)
    query = re.sub(
        r"\b(?:task|viec|cua toi|co phai|khong phai|phai khong|dinh ky|recurring|a|ha)\b",
        " ",
        query,
    )
    return " ".join(query.split())


def _clarification_keyboard(options: list[str]):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"clar:scope:{index}",
                )
            ]
            for index, label in enumerate(options, 1)
        ]
    )


def _task_selection_keyboard(tasks: list[Task]):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    labels = [
        f"{index} · {task.title[:42]}"
        for index, task in enumerate(tasks, 1)
    ]
    rows = [
        [InlineKeyboardButton(label, callback_data=f"clar:scope:{index}")]
        for index, label in enumerate(labels, 1)
    ]
    rows.append([InlineKeyboardButton("Hủy", callback_data="clar:scope:no")])
    return InlineKeyboardMarkup(rows)


def _confirmation_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Xác nhận", callback_data="clar:scope:yes"),
            InlineKeyboardButton("Hủy", callback_data="clar:scope:no"),
        ]]
    )


def _batch_confirmation_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Xác nhận",
                    callback_data="clar:scope:yes",
                ),
                InlineKeyboardButton(
                    "Chọn lại",
                    callback_data="clar:scope:edit",
                ),
                InlineKeyboardButton(
                    "Hủy",
                    callback_data="clar:scope:no",
                ),
            ]
        ]
    )


def _undo_keyboard(event_id: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("↩ Hoàn tác", callback_data=f"work:u:e:{event_id}")]]
    )


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
    return InlineKeyboardMarkup(rows)


def _completion_reply(
    task: Task | None,
    next_task: Task | None,
    display_timezone: tzinfo = UTC,
    *,
    now: datetime | None = None,
) -> str:
    if task is None:
        return "Em chưa tìm thấy task cần hoàn thành."
    if next_task is None:
        return f"Dạ. Đã đánh dấu xong: {task.title}."
    next_due = (
        next_task.due_at.astimezone(display_timezone).strftime("%H:%M %d/%m/%Y")
        if next_task.due_at
        else "chưa có hạn"
    )
    reply = (
        f"Dạ, em đã hoàn thành kỳ hiện tại của “{task.title}” và tạo kỳ kế tiếp "
        f"vào {next_due}."
    )
    if next_task.due_at and next_task.due_at <= (now or datetime.now(UTC)):
        reply += " Kỳ kế tiếp này hiện cũng đã quá hạn."
    return reply


def _batch_completion_reply(
    results: Sequence[TaskOperationResult],
    display_timezone: tzinfo = UTC,
    *,
    now: datetime | None = None,
) -> str:
    completed = [result.task for result in results if result.task is not None]
    if not completed:
        return "Em chưa tìm thấy task cần hoàn thành."
    lines = [f"Dạ. Đã đánh dấu xong {len(completed)} task:"]
    lines.extend(f"{index}. {task.title}" for index, task in enumerate(completed, 1))
    recurring = [
        result.next_task
        for result in results
        if result.next_task is not None
    ]
    if recurring:
        lines.append(f"Đã tạo {len(recurring)} kỳ kế tiếp cho các task định kỳ.")
        current = now or datetime.now(UTC)
        overdue_count = sum(
            1
            for task in recurring
            if task.due_at is not None and task.due_at <= current
        )
        if overdue_count:
            lines.append(f"Có {overdue_count} kỳ kế tiếp hiện đã quá hạn.")
    return "\n".join(lines)


def _priority_label(value: str) -> str:
    return {"high": "cao", "medium": "vừa", "low": "thấp"}.get(value, value)


def _recurrence_label(value: str) -> str:
    if value in {"daily", "weekly"}:
        return {"daily": "hằng ngày", "weekly": "hằng tuần"}[value]
    if value.startswith("weekly:"):
        return "hằng tuần"
    match = re.fullmatch(r"interval:(\d+)([dw])", value)
    if match:
        unit = "ngày" if match.group(2) == "d" else "tuần"
        return f"mỗi {int(match.group(1))} {unit}"
    return value


def _extract_task_due_at(
    raw_text: str,
    display_timezone,
    *,
    now: datetime | None = None,
) -> datetime | None:
    normalized = _normalize_text(raw_text)
    has_clock = _parse_clock_time(normalized) is not None
    has_relative = re.search(
        r"\b\d{1,3}\s*(?:phut|tieng|gio|ngay|minute|minutes|hour|hours|day|days)\s*(?:sau)?\b",
        normalized,
    ) is not None
    if not has_clock and not has_relative:
        return None
    cleaned = re.sub(
        r"\b(?:(?:task|việc|viec|số|so)\s*\d+|cái\s+thứ\s+\d+|cai\s+thu\s+\d+)\b",
        " ",
        raw_text,
        flags=re.IGNORECASE,
    )
    local_now = (
        now.astimezone(display_timezone)
        if now is not None
        else datetime.now(display_timezone)
    )
    parsed = parse_clarification_datetime(
        cleaned,
        now=local_now,
        default_timezone=display_timezone,
    )
    if parsed is not None:
        return parsed.astimezone(UTC)

    parsed_time = _parse_clock_time_raw(cleaned)
    if parsed_time is None:
        return None
    return datetime.combine(
        local_now.date(),
        parsed_time,
        tzinfo=display_timezone,
    ).astimezone(UTC)


def _extract_duration_minutes(raw_text: str) -> int | None:
    normalized = _normalize_text(raw_text)
    matches = list(
        re.finditer(
            r"\b(\d{1,2})(?:h|:)(\d{0,2})\b\s*(sang|chieu|toi|am|pm)?",
            normalized,
        )
    )
    if len(matches) < 2 or not any(
        marker in normalized for marker in (" tu ", " den ", "–", "-")
    ):
        return None

    def minutes(match) -> int | None:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        period = match.group(3) or ""
        if period in {"chieu", "toi", "pm"} and hour < 12:
            hour += 12
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    start = minutes(matches[0])
    end = minutes(matches[1])
    if start is None or end is None:
        return None
    if end <= start:
        end += 24 * 60
    duration = end - start
    return duration if 0 < duration <= 24 * 60 else None


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


def _is_reminder_snooze(normalized: str) -> bool:
    if not any(
        signal in normalized
        for signal in (
            "nhac lai",
            "doi lich nhac",
            "doi gio nhac",
            "dời lich nhac",
            "snooze",
            "remind me again",
            "remind again",
        )
    ):
        return False
    return any(
        signal in normalized
        for signal in (
            "mai",
            "hom nay",
            "toi nay",
            "sang",
            "chieu",
            "toi",
            "trua",
            "phut sau",
            "tieng sau",
            "gio sau",
            "ngay sau",
            "tomorrow",
            "today",
            "hour",
            "minute",
            "day",
        )
    )


def _has_contextual_snooze_reference(normalized: str) -> bool:
    query = _reminder_snooze_query(normalized)
    return not query or query in {"no", "nay", "do", "cai nay", "reminder nay"}


def _is_search_query(normalized: str) -> bool:
    return any(
        signal in normalized
        for signal in (
            "tim kiem",
            "tim lai",
            "search",
            "cho toi xem moi thu lien quan",
            "tat ca lien quan",
            "lien quan den",
        )
    )


def _is_timeline_query(normalized: str) -> bool:
    return any(
        signal in normalized
        for signal in (
            "timeline",
            "lich su",
            "da thay doi",
            "thay doi deadline",
            "lan gan nhat",
            "tuan truoc",
            "thang nay",
            "thang truoc",
            "da noi gi ve",
        )
    )


def _is_origin_query(normalized: str) -> bool:
    return any(
        signal in normalized
        for signal in (
            "vi sao",
            "tai sao",
            "why was",
            "duoc tao vi",
            "nguon goc",
            "task nay duoc tao",
            "cai nay duoc tao",
        )
    )


def _is_decision_timeline_query(normalized: str) -> bool:
    return "quyet dinh" in normalized and any(
        signal in normalized
        for signal in ("gi ve", "nao ve", "da quyet", "decision", "decisions")
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
    if _is_future_task_capture(normalized):
        return False
    return bool(
        re.search(r"\bda\s+.+\s+xong\b", normalized)
        or re.search(r"\b(?:danh dau|mark)\s+.+\s+(?:la\s+)?xong\b", normalized)
        or re.search(r"\b(?:toi\s+)?(?:da|vua)\s+hoan\s+thanh\b", normalized)
        or "da xong" in normalized
        or re.search(r"^xong\s+(?:het|tat ca|toan bo)\s+(?:task|viec)\b", normalized)
        or re.search(r"^hoan thanh\s+(?:cai|task|viec|so)\b", normalized)
        or re.search(r"\b(?:i\s+)?(?:have\s+)?(?:finished|completed)\b", normalized)
        or normalized in {"xong", "xong roi", "done"}
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
            "thoi han",
            "han chot",
            "deadline",
            "due date",
        )
    )
    has_numbered_task_update = (
        any(signal in normalized for signal in ("doi task", "doi viec", "sua task", "sua viec"))
        and _task_number_reference(normalized) is not None
    )
    return (has_update_signal or has_numbered_task_update) and _parse_clock_time(normalized) is not None


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
    match = re.search(
        r"\b(\d{1,2})(?:(?:[:h](\d{0,2}))|(?:\s+(\d{1,2})))\s*(am|pm)?\b",
        value,
    )
    if not match:
        match = re.search(r"\b(\d{1,2})\s*(am|pm)\b", value)
        if match:
            hour = int(match.group(1))
            meridiem = match.group(2)
            if meridiem == "pm" and hour < 12:
                hour += 12
            if meridiem == "am" and hour == 12:
                hour = 0
            return time(hour=hour) if hour <= 23 else None
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or match.group(3) or "0")
    meridiem = match.group(4)
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour=hour, minute=minute)


def _format_task_schedule(task: Task, display_timezone: tzinfo) -> str:
    if task.due_at is None:
        return "chưa có giờ"
    start = task.due_at.astimezone(display_timezone)
    if task.duration_minutes:
        end = start + timedelta(minutes=task.duration_minutes)
        return f"{start:%H:%M}–{end:%H:%M}"
    return f"{start:%H:%M}"


def _parse_clock_time_raw(value: str) -> time | None:
    match = re.search(r"\b(\d{1,2})(?:[:h](\d{0,2}))?\s*(am|pm)?\b", value, re.I)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = (match.group(3) or "").lower()
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


def _token_overlap_score(query: str, title: str) -> float:
    q_tokens = _clean_task_name_query(query)
    t_tokens = _clean_task_name_query(title)
    if not q_tokens or not t_tokens:
        return 0.0
    overlap = q_tokens & t_tokens
    return len(overlap) / max(1, min(len(q_tokens), len(t_tokens)))


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
    return vi


def _conversation_plan_payload(plan: ConversationPlan | None) -> dict[str, Any]:
    if plan is None:
        return {}
    return {
        "intent": plan.intent,
        "goal": plan.goal,
        "statements": list(plan.statements),
        "requires_entity": plan.requires_entity,
        "requested_count": plan.requested_count,
        "target_entity_ids": list(plan.target_entity_ids),
        "payload": plan.payload,
        "confidence": plan.confidence,
        "reason": plan.reason,
    }


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
        "Em là MemoCore, trợ lý cá nhân của anh. "
        "Em giúp anh ghi nhớ bối cảnh dài hạn, theo dõi task/project, nhắc việc, "
        "và trả lời dựa trên dữ liệu đã lưu. Khi dữ liệu chưa chắc, em sẽ nói rõ là cần xác nhận thay vì trình bày như fact."
    )


def _ambiguous_people_reply(people) -> str:
    names = "\n".join(
        f"{index}. {person.display_name}" for index, person in enumerate(people, 1)
    )
    return f"Em thấy nhiều người cùng khớp. Anh chọn tên đầy đủ giúp em:\n{names}"


def _ste_mindx_compare_answer() -> str:
    return (
        "STE và MindX là hai bối cảnh khác nhau của anh:\n"
        "- MindX là tổ chức nơi anh đang làm/vận hành, gắn với Teaching Operations, TEGL+, TOM và các hệ thống nội bộ.\n"
        "- STE là portfolio/danh mục sáng lập do anh vận hành, gồm dữ liệu/BI, công nghệ, AI, giáo dục, đầu tư và các dự án khách hàng.\n"
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
    normalized_new = _normalize_text(new_raw)
    if (
        _parse_clock_time(normalized_new) is not None
        or _requested_recurrence_rule(normalized_new) is not None
        or _requested_priority(text) is not None
    ):
        return None

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
