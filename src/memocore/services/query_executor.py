from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from memocore.services.conversation_executor import ExecutorResult
from memocore.services.secretary_service import SecretaryService


AsyncText = Callable[[], Awaitable[str]]


class QueryExecutor:
    """Owns read-only intent dispatch. Specialized query builders remain injected during migration."""

    QUERY_INTENTS = {
        "query_today", "query_tomorrow", "query_assistant_identity",
        "query_ste_mindx_compare", "query_person_tasks", "query_tasks",
        "query_tasks_due", "query_task_recurrence", "query_reminders",
        "query_projects", "query_people", "query_commitments", "query_context",
        "query_meeting_prep", "query_memory", "query_profile",
    }

    def __init__(self, secretary_service: SecretaryService):
        self.secretary_service = secretary_service

    async def execute(
        self,
        intent: str,
        *,
        project_name: str | None = None,
        project_scope: str | None = None,
        memory_bucket: str | None = None,
        context_query: str | None = None,
        now_utc: datetime | None = None,
        callbacks: dict[str, AsyncText] | None = None,
        static_replies: dict[str, str] | None = None,
    ) -> ExecutorResult | None:
        if intent not in self.QUERY_INTENTS:
            return None
        callbacks = callbacks or {}
        static_replies = static_replies or {}
        if intent in static_replies:
            return ExecutorResult(intent, static_replies[intent])
        if intent in callbacks:
            return ExecutorResult(intent, await callbacks[intent]())
        if intent == "query_today":
            reply = await self.secretary_service.today(now_utc)
        elif intent == "query_tomorrow":
            reply = await self.secretary_service.tomorrow(now_utc)
        elif intent in {"query_tasks", "query_tasks_due"}:
            if project_name:
                return ExecutorResult(
                    "query_projects",
                    await self.secretary_service.project_tasks(project_name),
                )
            reply = await self.secretary_service.tasks()
        elif intent == "query_reminders":
            reply = await self.secretary_service.reminders()
        elif intent == "query_projects":
            reply = (
                await self.secretary_service.project_tasks(project_name)
                if project_name
                else await self.secretary_service.projects(scope=project_scope)
            )
        elif intent == "query_people":
            reply = await self.secretary_service.people()
        elif intent == "query_commitments":
            reply = await self.secretary_service.commitments()
        elif intent == "query_context":
            if not context_query:
                reply = "Anh muốn xem context nào? Nói tên person hoặc project giúp em nha."
            else:
                reply = await self.secretary_service.context(context_query)
        elif intent == "query_meeting_prep":
            if not context_query:
                reply = "Anh muốn chuẩn bị meeting nào? Nói tên person hoặc project giúp em nha."
            else:
                reply = await self.secretary_service.meeting_prep(context_query)
        elif intent == "query_memory":
            reply = await self.secretary_service.memories(bucket=memory_bucket)
        else:
            return None
        return ExecutorResult(intent, reply)
