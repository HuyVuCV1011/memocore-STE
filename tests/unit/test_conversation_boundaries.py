from memocore.services.conversation_composer import ConversationComposer
from memocore.services.conversation_executor import ConversationExecutor
from memocore.services.conversation_router import ConversationRouter
from memocore.services.clarification_workflow import ClarificationWorkflow
from memocore.services.memory_lifecycle_executor import MemoryLifecycleExecutor
from memocore.services.query_executor import QueryExecutor
from memocore.services.task_mutation_executor import TaskMutationExecutor
from memocore.domain.schemas import IntentClassification


async def test_router_prefers_planned_intent_over_legacy_rules():
    decision = await ConversationRouter().route(
        "cập nhật dự án này",
        planned_intent="update_knowledge",
        bare_entity_reference=False,
        deterministic_route=lambda _: "query_projects",
        fallback_route=lambda _: "needs_clarification",
    )

    assert decision.intent == "update_knowledge"


async def test_router_supplies_bounded_conversation_context_to_classifier():
    class ContextClassifier:
        def __init__(self):
            self.context = None

        async def classify(self, raw_text: str, *, context: str = ""):
            self.context = context
            return IntentClassification(intent="casual_or_noop", confidence=0.9)

    classifier = ContextClassifier()
    decision = await ConversationRouter(classifier).route(
        "cái vừa rồi",
        planned_intent=None,
        bare_entity_reference=False,
        deterministic_route=lambda _: None,
        fallback_route=lambda _: "needs_clarification",
        conversation_context="User: tạo hai task\nAssistant: đã tạo",
    )

    assert decision.intent == "casual_or_noop"
    assert classifier.context == "User: tạo hai task\nAssistant: đã tạo"


async def test_executor_only_dispatches_registered_intents():
    executor = ConversationExecutor()

    async def handled():
        return "ok"

    assert await executor.dispatch("known", {"known": handled}) == "ok"
    assert await executor.dispatch("unknown", {"known": handled}) is None


def test_composer_owns_user_facing_knowledge_prompts():
    composer = ConversationComposer()

    assert "project, người hoặc tổ chức" in composer.missing_knowledge_target()
    assert "MemoCore" in composer.missing_knowledge_payload("MemoCore")


async def test_specialized_executors_reject_out_of_scope_intents():
    class Secretary:
        pass

    assert await QueryExecutor(Secretary()).execute("capture_task") is None
    assert await TaskMutationExecutor().execute("query_tasks", {}) is None
    assert await MemoryLifecycleExecutor().execute("query_memory", {}) is None


def test_clarification_workflow_owns_unresolved_and_no_pending_replies():
    workflow = ClarificationWorkflow(ConversationComposer())

    assert "chưa rõ" in workflow.unresolved("needs_clarification").reply
    assert "chờ" in workflow.answer_without_pending(
        "clarification_answer", vietnamese=True
    ).reply
