from memocore.services.conversation_planner import ConversationPlanner
from memocore.services.conversation_frame import ConversationFrame


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


def test_plans_recent_artifact_merge_from_conversation_frame():
    frame = ConversationFrame(
        source_chat_id="chat",
        last_result_entity_ids=("task-a", "task-b"),
        active_task_ids=frozenset({"task-a", "task-b"}),
    )

    plan = ConversationPlanner().plan(
        "hai task vừa tạo là một việc chung, gộp lại", frame
    )

    assert plan is not None
    assert plan.intent == "merge_tasks"
    assert plan.goal == "correct_previous_task_split"
    assert plan.target_entity_ids == ("task-a", "task-b")


def test_future_completion_is_planned_as_scheduling_not_completion():
    plan = ConversationPlanner().plan(
        "đặt lịch tối mai hoàn thành giáo trình MindX"
    )

    assert plan is not None
    assert plan.intent == "capture_task"
    assert plan.goal == "schedule_future_work"


def test_generic_undo_targets_previous_operation_not_knowledge_rollback():
    frame = ConversationFrame(
        source_chat_id="chat",
        last_intent="merge_tasks",
        last_result_entity_ids=("merged-task",),
        active_task_ids=frozenset({"merged-task"}),
    )

    plan = ConversationPlanner().plan("hoàn tác thay đổi vừa rồi", frame)

    assert plan is not None
    assert plan.intent == "undo_last_action"
    assert plan.target_entity_ids == ("merged-task",)
