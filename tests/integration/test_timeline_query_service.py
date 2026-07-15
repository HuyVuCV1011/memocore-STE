from datetime import UTC, datetime

from memocore.domain.knowledge import Decision
from memocore.domain.models import EventType, MemoryBucket, MemoryItem, MemoryKind, Note, Task
from memocore.services.event_service import EventService
from memocore.services.timeline_query_service import TimelineQueryService


def _service(repos) -> TimelineQueryService:
    return TimelineQueryService(
        repos["notes"],
        repos["tasks"],
        repos["reminders"],
        repos["projects"],
        repos["people"],
        repos["meetings"],
        repos["followups"],
        repos["commitments"],
        repos["memory"],
        repos["events"],
        repos["decisions"],
    )


async def test_unified_search_returns_cross_domain_timeline_without_backend_ids(repos):
    note = await repos["notes"].create(
        Note(
            raw_text="MemoCore V4 cần backup/restore và timeline rõ nguồn.",
            summary="MemoCore V4 recovery and timeline scope",
            source_chat_id="9001",
            source_message_id="msg-1",
            created_at=datetime(2030, 1, 2, 9, 0, tzinfo=UTC),
        )
    )
    await repos["tasks"].create(
        Task(
            title="Hoàn tất MemoCore timeline",
            source_note_id=note.id,
            created_at=datetime(2030, 1, 2, 9, 5, tzinfo=UTC),
            updated_at=datetime(2030, 1, 2, 9, 5, tzinfo=UTC),
        )
    )
    await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROJECT,
            kind=MemoryKind.PROJECT_STATE,
            content="MemoCore cần search/timeline hợp nhất.",
            source_note_id=note.id,
            created_at=datetime(2030, 1, 2, 9, 10, tzinfo=UTC),
            updated_at=datetime(2030, 1, 2, 9, 10, tzinfo=UTC),
        )
    )

    answer = await _service(repos).answer("MemoCore timeline")

    assert "Hoàn tất MemoCore timeline" in answer
    assert "MemoCore cần search/timeline hợp nhất" in answer
    assert "tin nhắn Telegram" in answer
    assert note.id not in answer
    assert "9001" not in answer
    assert "msg-1" not in answer


async def test_origin_query_explains_source_and_operation_chain(repos):
    note = await repos["notes"].create(
        Note(
            raw_text="Tạo task chuẩn bị báo cáo BI.",
            summary="Task source",
            created_at=datetime(2030, 1, 3, 8, 0, tzinfo=UTC),
        )
    )
    task = await repos["tasks"].create(
        Task(
            title="Chuẩn bị báo cáo BI",
            source_note_id=note.id,
            created_at=datetime(2030, 1, 3, 8, 1, tzinfo=UTC),
            updated_at=datetime(2030, 1, 3, 8, 1, tzinfo=UTC),
        )
    )
    await EventService(repos["events"]).append_event(
        EventType.TASK_CANDIDATE_CREATED,
        "task",
        task.id,
        {"source_note_id": note.id},
        created_at=datetime(2030, 1, 3, 8, 2, tzinfo=UTC),
    )

    answer = await _service(repos).why("báo cáo BI")

    assert "Vì sao có" in answer
    assert "Chuẩn bị báo cáo BI" in answer
    assert "Nguồn gốc" in answer
    assert "tin nhắn Telegram" in answer
    assert "task được tạo" in answer
    assert task.id not in answer
    assert note.id not in answer


async def test_decision_timeline_returns_source_linked_decisions(repos):
    note = await repos["notes"].create(
        Note(
            raw_text="Quyết định MemoCore ưu tiên backup trước calendar.",
            created_at=datetime(2030, 1, 4, 10, 0, tzinfo=UTC),
        )
    )
    await repos["decisions"].create(
        Decision(
            title="Ưu tiên backup trước calendar",
            summary="Trust before expansion",
            source_note_id=note.id,
            decided_at=datetime(2030, 1, 4, 10, 1, tzinfo=UTC),
        )
    )

    answer = await _service(repos).decisions("quyết định về MemoCore backup")

    assert "Quyết định liên quan" in answer
    assert "Ưu tiên backup trước calendar" in answer
    assert "Trust before expansion" in answer
    assert "tin nhắn Telegram" in answer
    assert note.id not in answer
