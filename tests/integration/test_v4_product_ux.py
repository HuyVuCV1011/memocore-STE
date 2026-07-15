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
    assert "1 câu hỏi đang chờ" in overview.summary
    assert "1 task chưa có hạn" in overview.summary
    assert "project thiếu next action" in overview.summary
    assert "1 phản hồi chưa xử lý" in overview.summary
    assert "1 cảnh báo hệ thống" in overview.summary
    assert "Gợi ý trùng: 1" in overview.sections[0].lines
    assert any(
        line.startswith("Project thiếu next action: ")
        for line in overview.sections[0].lines
    )
    assert "Cảnh báo hệ thống: 1" in overview.sections[0].lines
    assert any("1 sửa sai" in line for line in overview.sections[0].lines)
    assert "Anh muốn hoàn thành task nào?" in pending.sections[0].lines
    assert "1 lỗi backup" in system.summary
    assert "disk full" not in "\n".join(system.sections[0].lines)
    assert "project active cần rà lại" in project_health.summary
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
