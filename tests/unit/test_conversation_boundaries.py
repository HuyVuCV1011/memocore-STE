from memocore.services.conversation_composer import ConversationComposer
from memocore.services.conversation_executor import ConversationExecutor
from memocore.services.conversation_router import ConversationRouter


async def test_router_prefers_planned_intent_over_legacy_rules():
    decision = await ConversationRouter().route(
        "cập nhật dự án này",
        planned_intent="update_knowledge",
        bare_entity_reference=False,
        deterministic_route=lambda _: "query_projects",
        fallback_route=lambda _: "needs_clarification",
    )

    assert decision.intent == "update_knowledge"


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
