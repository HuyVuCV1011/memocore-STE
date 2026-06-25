from __future__ import annotations

from memocore.services.conversation_composer import ConversationComposer
from memocore.services.conversation_executor import ExecutorResult


class ClarificationWorkflow:
    def __init__(self, composer: ConversationComposer):
        self.composer = composer

    def unresolved(
        self, intent: str, question: str | None = None, *, vietnamese: bool = True
    ) -> ExecutorResult | None:
        if intent != "needs_clarification":
            return None
        return ExecutorResult(
            intent,
            question
            or (
                self.composer.generic_clarification()
                if vietnamese
                else "I'm not sure what you mean. Could you please specify?"
            ),
        )

    def answer_without_pending(self, intent: str, *, vietnamese: bool) -> ExecutorResult | None:
        if intent != "clarification_answer":
            return None
        return ExecutorResult(
            intent,
            "Em chưa có câu hỏi nào đang chờ câu trả lời."
            if vietnamese
            else "I do not have a pending clarification right now.",
        )
