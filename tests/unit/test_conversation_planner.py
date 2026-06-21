from memocore.services.conversation_planner import ConversationPlanner


def test_plans_entity_overview_instead_of_project_list():
    plan = ConversationPlanner().plan("nói cho tôi biết về dự án MemoCore")

    assert plan is not None
    assert plan.intent == "query_context"
    assert plan.requires_entity is True


def test_plans_multiline_knowledge_update_as_multiple_statements():
    plan = ConversationPlanner().plan(
        "cập nhật thêm thông tin cho dự án này như sau nhé\n"
        "- đây là dự án xây dựng trợ lý cá nhân\n"
        "- trợ lý cá nhân nghĩa là có thể làm tất cả mọi công việc mà tôi giao trong tương lai\n"
        "- không cần code quá giỏi, nhưng cần đứng góc độ trợ lý thư ký cho tôi"
    )

    assert plan is not None
    assert plan.intent == "update_knowledge"
    assert plan.statements == (
        "đây là dự án xây dựng trợ lý cá nhân",
        "trợ lý cá nhân nghĩa là có thể làm tất cả mọi công việc mà tôi giao trong tương lai",
        "không cần code quá giỏi, nhưng cần đứng góc độ trợ lý thư ký cho tôi",
    )


def test_plans_recent_knowledge_batch_rollback_before_task_cancellation():
    plan = ConversationPlanner().plan(
        "xóa 3 thông tin đã cập nhật cho Văn Nghĩa Trần"
    )

    assert plan is not None
    assert plan.intent == "rollback_knowledge_update"
    assert plan.requested_count == 3
