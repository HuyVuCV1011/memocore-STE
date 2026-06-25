from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import inspect

from memocore.services.intent_classifier_service import IntentClassifierService


SAFE_MODEL_INTENTS = {
    "query_today",
    "query_tomorrow",
    "query_memory",
    "query_profile",
    "query_assistant_identity",
    "query_ste_mindx_compare",
    "query_person_tasks",
    "query_tasks",
    "query_task_recurrence",
    "query_reminders",
    "query_projects",
    "query_people",
    "query_commitments",
    "query_context",
    "query_meeting_prep",
    "update_knowledge",
    "rollback_knowledge_update",
    "casual_or_noop",
    "clarification_answer",
    "needs_clarification",
    "assign_task_to_person",
    "create_task_check_reminder",
    "cancel_task",
    "update_task_priority",
    "update_task_recurrence",
}


@dataclass(frozen=True)
class RoutingDecision:
    intent: str
    confidence: float = 1.0
    ambiguity_detected: bool = False
    clarification_question: str | None = None
    target_entity_hints: str | None = None


class ConversationRouter:
    def __init__(self, classifier: IntentClassifierService | None = None):
        self.classifier = classifier

    async def route(
        self,
        raw_text: str,
        *,
        planned_intent: str | None,
        bare_entity_reference: bool,
        deterministic_route: Callable[[str], str | None],
        fallback_route: Callable[[str], str],
        conversation_context: str = "",
    ) -> RoutingDecision:
        intent = planned_intent or deterministic_route(raw_text)
        if intent is None and bare_entity_reference:
            intent = "query_context"
        if intent is not None:
            return RoutingDecision(intent=intent)
        if self.classifier is None:
            return RoutingDecision(intent=fallback_route(raw_text))
        try:
            parameters = inspect.signature(self.classifier.classify).parameters
            if "context" in parameters:
                classification = await self.classifier.classify(
                    raw_text, context=conversation_context
                )
            else:
                classification = await self.classifier.classify(raw_text)
        except Exception:
            fallback = fallback_route(raw_text)
            return RoutingDecision(
                intent=fallback if fallback in SAFE_MODEL_INTENTS else "needs_clarification"
            )
        classified_intent = classification.intent
        if (
            classified_intent not in SAFE_MODEL_INTENTS
            and (classification.confidence < 0.6 or classification.ambiguity_detected)
        ):
            classified_intent = "needs_clarification"
        return RoutingDecision(
            intent=classified_intent,
            confidence=classification.confidence,
            ambiguity_detected=classification.ambiguity_detected,
            clarification_question=classification.clarification_question,
            target_entity_hints=classification.target_entity_hints,
        )
