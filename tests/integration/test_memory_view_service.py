from memocore.domain.models import (
    MemoryBucket,
    MemoryItem,
    MemoryKind,
    MemoryStatus,
    Note,
    Person,
)
from memocore.services.memory_view_service import MEMORY_PAGE_SIZE, MemoryViewService


async def test_memory_overview_is_compact_and_hides_correction_metadata(repos):
    note = await repos["notes"].create(Note(raw_text="memory source"))
    await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.PREFERENCE,
            content="Vũ thích câu trả lời ngắn gọn.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.ACTIVE,
        )
    )
    await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.CORRECTION,
            content="Imported correction metadata must stay hidden.",
            source_note_id=note.id,
            confidence=1,
            status=MemoryStatus.ACTIVE,
        )
    )
    await repos["people"].create(Person(display_name="Alex", relationship="đồng nghiệp"))
    await repos["projects"].find_or_create("STE")
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    response = await service.overview()

    rendered = response.model_dump_json()
    assert response.title == "Ghi nhớ của bạn"
    assert len(rendered) < 4096
    assert "Imported correction metadata" not in rendered
    assert {action.action_id for action in response.actions} == {
        "mem:t:review:0",
        "mem:t:stale:0",
        "mem:t:self:0",
        "mem:t:goals:0",
        "mem:t:people:0",
        "mem:t:projects:0",
        "mem:t:mindx:0",
        "mem:t:ste:0",
    }
    assert "Triage" in rendered
    assert "Map" in rendered


async def test_memory_topic_deduplicates_and_paginates(repos):
    note = await repos["notes"].create(Note(raw_text="memory source"))
    for index in range(MEMORY_PAGE_SIZE + 1):
        await repos["memory"].create(
            MemoryItem(
                bucket=MemoryBucket.PROJECT,
                kind=MemoryKind.PROJECT_STATE,
                content=f"STE capability {index}",
                source_note_id=note.id,
                confidence=0.9,
                status=MemoryStatus.ACTIVE,
            )
        )
    await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROJECT,
            kind=MemoryKind.PROJECT_STATE,
            content="STE capability 0",
            source_note_id=note.id,
            confidence=0.8,
            status=MemoryStatus.ACTIVE,
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    first = await service.topic("ste", 0)
    second = await service.topic("ste", 1)

    assert first is not None and second is not None
    assert len(first.sections[0].lines) == MEMORY_PAGE_SIZE * 3
    assert first.footer == "Trang 1/2"
    assert any(action.action_id == "mem:t:ste:1" for action in first.actions)
    all_lines = first.sections[0].lines + second.sections[0].lines
    item_lines = [line for line in all_lines if "STE capability" in line]
    assert len(item_lines) == MEMORY_PAGE_SIZE + 1
    assert len(set(item_lines)) == MEMORY_PAGE_SIZE + 1
    assert any("STE capability 0" in line for line in item_lines)
    assert any(action.action_id == "mem:o" for action in second.actions)


async def test_memory_review_exposes_metadata_and_confirmation(repos):
    note = await repos["notes"].create(Note(raw_text="memory source"))
    item = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Vũ đang thử một lịch làm việc mới.",
            source_note_id=note.id,
            confidence=0.6,
            status=MemoryStatus.CANDIDATE,
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    review = await service.topic("review", 0)

    assert review is not None
    assert any("tin cậy 60%" in line for line in review.sections[0].lines)
    assert any(action.action_id == f"mem:k:{item.id}" for action in review.actions)
    assert any(action.action_id == f"mem:r:{item.id}" for action in review.actions)
    assert any(action.action_id == f"mem:s:{item.id}" for action in review.actions)

    await service.confirm(item.id)
    confirmed = await repos["memory"].get_by_id(item.id)
    assert confirmed is not None
    assert str(confirmed.status) == "active"
    assert confirmed.last_confirmed_at is not None
