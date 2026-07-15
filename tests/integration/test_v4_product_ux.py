from datetime import UTC, datetime, timedelta

from memocore.domain.models import (
    ClarificationRequest,
    EventType,
    FeedbackSignal,
    Meeting,
    MemoryBucket,
    MemoryItem,
    MemoryKind,
    Note,
    Person,
    ProjectStatus,
    ProjectType,
    Task,
    TaskStatus,
)
from memocore.services.event_service import EventService
from memocore.services.review_service import ReviewService
from memocore.services.secretary_service import SecretaryService


def _secretary(repos) -> SecretaryService:
    return SecretaryService(
        repos["tasks"],
        repos["reminders"],
        repos["followups"],
        repos["projects"],
        repos["memory"],
        meeting_repo=repos["meetings"],
        person_repo=repos["people"],
        commitment_repo=repos["commitments"],
        activity_link_repo=repos["activity_links"],
    )


async def test_people_view_is_paginated_and_never_leaks_internal_relationship_codes(repos):
    for index in range(7):
        await repos["people"].create(
            Person(
                display_name=f"Người {index + 1}",
                relationship="mindx_te_nghia",
            )
        )

    first = await _secretary(repos).people_view(0)
    second = await _secretary(repos).people_view(1)
    first_text = "\n".join(first.sections[0].lines)

    assert "mindx_te_nghia" not in first_text
    assert "nhóm TE tại MindX" in first_text
    assert any(action.label == "Sau ›" for action in first.actions)
    assert "Người 7" in "\n".join(second.sections[0].lines)


async def test_person_context_hides_evidence_and_translates_relationship(repos):
    person = await repos["people"].create(
        Person(
            display_name="Nguyễn Hoàng Khôi Nguyên",
            aliases=["Khôi Nguyên"],
            relationship="mindx_tom_direct_and_ste_collaborator",
            notes=(
                "MindX: leader team HO / Teaching Development Leader under Vu's TOM role. "
                "STE: major execution collaborator. Keep contexts separate."
            ),
        )
    )
    context = await _secretary(repos).person_context_by_id(person.id)

    assert "nhân sự trực tiếp trong nhánh TOM" in context
    assert "cộng tác viên thực thi quan trọng" in context
    assert "mindx_tom_direct" not in context
    assert "Evidence:" not in context
    assert "source:" not in context
    assert "tin cậy:" not in context


async def test_agenda_surfaces_conflicts_and_a_useful_next_item(repos):
    note_a = await repos["notes"].create(Note(raw_text="Task A"))
    note_b = await repos["notes"].create(Note(raw_text="Meeting B"))
    start = datetime(2030, 1, 2, 10, 0, tzinfo=UTC)
    await repos["tasks"].create(
        Task(
            title="Chấm bài APM10",
            due_at=start,
            duration_minutes=90,
            source_note_id=note_a.id,
        )
    )
    await repos["meetings"].create(
        Meeting(
            title="Review giáo trình",
            starts_at=start + timedelta(minutes=30),
            ends_at=start + timedelta(minutes=90),
            source_note_id=note_b.id,
        )
    )
    service = _secretary(repos)

    agenda = await service.agenda_for_date(start.date(), "Ngày kiểm thử")
    empty_day = await service.agenda_for_date(start.date() - timedelta(days=1), "Ngày trống")

    assert "⚠️ Xung đột lịch" in agenda
    assert "Chấm bài APM10" in agenda
    assert "Review giáo trình" in agenda
    assert "Tiếp theo" in empty_day
    assert "Chấm bài APM10" in empty_day


async def test_review_center_combines_uncertain_state_and_quality_signals(repos):
    note = await repos["notes"].create(Note(raw_text="review data"))
    memory = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Thông tin cần xác nhận",
            source_note_id=note.id,
        )
    )
    await repos["tasks"].create(
        Task(title="Task chưa có hạn", source_note_id=note.id)
    )
    project_without_next_action = await repos["projects"].find_or_create(
        "Project chưa có next action"
    )
    await repos["projects"].update_taxonomy(
        project_without_next_action.id,
        ProjectType.INDEPENDENT_PROJECT,
        ProjectStatus.ACTIVE,
        None,
    )
    await repos["clarifications"].create(
        ClarificationRequest(
            source_chat_id="review-chat",
            entity_type="task_selection_done",
            entity_id="task-a,task-b",
            field_name="status|done",
            question="Anh muốn hoàn thành task nào?",
        )
    )
    events = EventService(repos["events"])
    await events.append_event(
        EventType.MEMORY_DUPLICATE_SUGGESTED,
        "memory_item",
        memory.id,
    )
    await events.append_event(
        EventType.BACKUP_FAILED,
        "database",
        "memocore.db",
        {"error": "disk full"},
    )
    feedback = await events.record_feedback(
        FeedbackSignal.CORRECTION,
        "memory_item",
        memory.id,
        source_chat_id="review-chat",
        source_message_id="message-1",
        source_note_id=note.id,
        action="reject_misclassified_memory",
    )
    service = ReviewService(
        repos["memory"],
        repos["tasks"],
        repos["clarifications"],
        events,
        repos["projects"],
    )

    overview = await service.overview()
    pending = await service.clarifications()
    system = await service.system()
    project_health = await service.project_health()

    assert "1 ghi nhớ" in overview.summary
    assert "1 câu hỏi" in overview.summary
    assert "1 phản hồi" in overview.summary
    assert "1 cảnh báo hệ thống" in overview.summary
    assert "1 task chưa có hạn" in overview.sections[0].lines
    assert "1 project cần chọn next action trước" in overview.sections[0].lines
    assert "0 project khác trong backlog hygiene" in overview.sections[0].lines
    assert "Gợi ý trùng: 1" in overview.sections[1].lines
    assert "Project health backlog: 1" in overview.sections[1].lines
    assert "Cảnh báo hệ thống: 1" in overview.sections[1].lines
    assert any("1 sửa sai" in line for line in overview.sections[1].lines)
    assert "Anh muốn hoàn thành task nào?" in pending.sections[0].lines
    assert "1 lỗi backup" in system.summary
    assert "disk full" not in "\n".join(system.sections[0].lines)
    assert "1 project cần quyết định trước" in project_health.summary
    assert any(
        "Project chưa có next action" in line
        for line in project_health.sections[0].lines
    )

    quality = await service.feedback()
    assert "1 mục đang mở" in quality.summary
    assert quality.actions[0].action_id == f"nav:rf:{feedback.id}"

    resolved = await service.resolve_feedback(feedback.id)
    assert resolved is not None
    assert "0 mục đang mở" in resolved.summary


async def test_review_project_health_uses_descendant_tasks_and_skips_portfolio_noise(repos):
    note = await repos["notes"].create(Note(raw_text="portfolio tree"))
    portfolio = await repos["projects"].find_or_create("STE")
    await repos["projects"].update_taxonomy(
        portfolio.id,
        ProjectType.PORTFOLIO,
        ProjectStatus.ACTIVE,
        None,
    )
    child = await repos["projects"].find_or_create("STEDATA")
    await repos["projects"].update_taxonomy(
        child.id,
        ProjectType.PRODUCT,
        ProjectStatus.ACTIVE,
        portfolio.id,
    )
    quiet = await repos["projects"].find_or_create("Quiet Client")
    await repos["projects"].update_taxonomy(
        quiet.id,
        ProjectType.CLIENT_PROJECT,
        ProjectStatus.ACTIVE,
        None,
    )
    await repos["tasks"].create(
        Task(title="Ship STEDATA dashboard", source_note_id=note.id, project_id=child.id)
    )
    service = ReviewService(
        repos["memory"],
        repos["tasks"],
        repos["clarifications"],
        EventService(repos["events"]),
        repos["projects"],
    )

    health = await service.project_health()
    text = "\n".join(health.sections[0].lines)

    assert "STE" not in text
    assert "STEDATA" not in text
    assert "Quiet Client" in text


async def test_review_project_health_focuses_actionable_leaf_projects(repos):
    parent = await repos["projects"].find_or_create("MemoCore")
    await repos["projects"].update_taxonomy(
        parent.id,
        ProjectType.PRODUCT,
        ProjectStatus.ACTIVE,
        None,
    )
    child = await repos["projects"].find_or_create("MemoCore Telegram UX")
    await repos["projects"].update_taxonomy(
        child.id,
        ProjectType.INITIATIVE,
        ProjectStatus.ACTIVE,
        parent.id,
    )
    unknown = await repos["projects"].find_or_create("Unclassified context")
    await repos["projects"].update_taxonomy(
        unknown.id,
        ProjectType.PRODUCT,
        ProjectStatus.ACTIVE,
        None,
    )
    await repos["projects"]._execute(
        "UPDATE projects SET project_type = NULL WHERE id = ?",
        (unknown.id,),
    )
    service = ReviewService(
        repos["memory"],
        repos["tasks"],
        repos["clarifications"],
        EventService(repos["events"]),
        repos["projects"],
    )

    health = await service.project_health()
    text = "\n".join(health.sections[0].lines)

    assert "MemoCore Telegram UX" in text
    assert "MemoCore ·" not in text
    assert "Unclassified context" not in text
    assert "1 project cần quyết định trước" in health.summary


async def test_weekly_review_project_health_uses_actionable_leaf_projects(repos):
    note = await repos["notes"].create(Note(raw_text="weekly project health"))
    parent = await repos["projects"].find_or_create("MemoCore Weekly")
    await repos["projects"].update_taxonomy(
        parent.id,
        ProjectType.PRODUCT,
        ProjectStatus.ACTIVE,
        None,
    )
    child = await repos["projects"].find_or_create("MemoCore Weekly Child")
    await repos["projects"].update_taxonomy(
        child.id,
        ProjectType.INITIATIVE,
        ProjectStatus.ACTIVE,
        parent.id,
    )
    covered = await repos["projects"].find_or_create("Covered Client")
    await repos["projects"].update_taxonomy(
        covered.id,
        ProjectType.CLIENT_PROJECT,
        ProjectStatus.ACTIVE,
        None,
    )
    await repos["tasks"].create(
        Task(title="Ship covered work", source_note_id=note.id, project_id=covered.id)
    )

    weekly = await _secretary(repos).weekly_review(datetime(2026, 7, 15, 10, 0, tzinfo=UTC))
    lines = weekly.splitlines()

    assert "MemoCore Weekly Child" in weekly
    assert "- MemoCore Weekly" not in lines
    assert "Covered Client" not in weekly


async def test_review_project_health_groups_large_backlog(repos):
    for index in range(7):
        project = await repos["projects"].find_or_create(f"Backlog project {index + 1}")
        await repos["projects"].update_taxonomy(
            project.id,
            ProjectType.INDEPENDENT_PROJECT,
            ProjectStatus.ACTIVE,
            None,
        )
    service = ReviewService(
        repos["memory"],
        repos["tasks"],
        repos["clarifications"],
        EventService(repos["events"]),
        repos["projects"],
    )

    health = await service.project_health()
    text = "\n".join(health.sections[0].lines)

    assert "7 project cần quyết định trước" in health.summary
    assert text.count("Backlog project") == 5
    assert "Còn 2 project cần quyết định khác" in text


async def test_review_surfaces_recent_undoable_operations(repos):
    events = EventService(repos["events"])
    changed = await events.append_event(
        EventType.WORK_ITEM_CHANGED,
        "task",
        "task-1",
        {"action": "reschedule"},
        created_at=datetime(2026, 7, 15, 20, 0, tzinfo=UTC),
    )
    undone = await events.append_event(
        EventType.DAILY_CLOSEOUT_APPLIED,
        "clarification_request",
        "closeout-1",
        {"due_at": datetime(2026, 7, 16, 9, 0, tzinfo=UTC).isoformat(), "items": {}},
        created_at=datetime(2026, 7, 15, 19, 0, tzinfo=UTC),
    )
    await events.append_event(
        EventType.WORK_ITEM_UNDONE,
        "work_event",
        undone.id,
        {"restored_count": 0},
    )
    service = ReviewService(
        repos["memory"],
        repos["tasks"],
        repos["clarifications"],
        events,
        repos["projects"],
    )

    overview = await service.overview()
    recent = await service.recent_operations()

    assert "Gần đây có thể hoàn tác: 1" in overview.sections[1].lines
    assert any(action.action_id == "nav:review:recent" for action in overview.actions)
    assert recent.summary == "1 thao tác còn trong vùng an toàn để undo."
    assert "Cập nhật công việc" in recent.sections[0].lines[0]
    assert recent.actions[0].action_id == f"work:u:e:{changed.id}"


async def test_work_views_share_priority_logic_and_keep_waiting_out_of_next_actions(repos):
    now = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    note = await repos["notes"].create(Note(raw_text="work state"))
    await repos["tasks"].create(
        Task(
            title="Follow up Alex",
            source_note_id=note.id,
            status=TaskStatus.WAITING,
            due_at=now - timedelta(hours=1),
        )
    )
    await repos["tasks"].create(
        Task(
            title="Finish BI report",
            source_note_id=note.id,
            due_at=now - timedelta(hours=2),
            priority="high",
        )
    )
    await repos["tasks"].create(
        Task(
            title="Tập gym",
            source_note_id=note.id,
            due_at=now + timedelta(hours=3),
            recurrence_rule="daily",
        )
    )
    service = _secretary(repos)

    dashboard = await service.work_dashboard(now)
    briefing = await service.daily_briefing(now)
    next_action_block = briefing.split("Nên làm tiếp", 1)[1]
    routine_block = briefing.split("Routine hôm nay", 1)[1].split("Nên làm tiếp", 1)[0]

    assert "Score:" not in dashboard
    assert "Top priorities" not in dashboard
    assert "Finish BI report" in next_action_block
    assert "Tập gym" not in next_action_block
    assert "Tập gym" in routine_block
    assert "Follow up Alex" not in next_action_block
    assert "Việc đang chờ" in briefing


async def test_briefing_does_not_repeat_attention_tasks_as_next_actions(repos):
    now = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    note = await repos["notes"].create(Note(raw_text="briefing dedupe"))
    await repos["tasks"].create(
        Task(
            title="Gửi báo cáo BI",
            source_note_id=note.id,
            due_at=now - timedelta(hours=2),
            priority="high",
        )
    )
    await repos["tasks"].create(
        Task(
            title="Tập gym",
            source_note_id=note.id,
            due_at=now - timedelta(hours=1),
            recurrence_rule="daily",
        )
    )
    service = _secretary(repos)

    briefing = await service.daily_briefing(now)
    attention_block = briefing.split("Điểm cần chú ý", 1)[1].split("Routine hôm nay", 1)[0]
    next_action_block = briefing.split("Nên làm tiếp", 1)[1]

    assert "Gửi báo cáo BI" not in attention_block
    assert "Gửi báo cáo BI" in next_action_block
    assert "Tập gym" in briefing.split("Routine hôm nay", 1)[1].split("Nên làm tiếp", 1)[0]
    assert "Tập gym" not in next_action_block


async def test_weekly_review_hides_raw_priority_scores(repos):
    note = await repos["notes"].create(Note(raw_text="weekly no score"))
    await repos["tasks"].create(
        Task(
            title="Prepare trust report",
            source_note_id=note.id,
            due_at=datetime.now(UTC) - timedelta(days=1),
            priority="high",
        )
    )

    weekly = await _secretary(repos).weekly_review()

    assert "Score:" not in weekly
    assert "Prepare trust report" in weekly
