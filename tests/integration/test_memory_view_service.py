from datetime import UTC, datetime, timedelta

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
    assert response.title == "Ghi nhớ của anh"
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
    assert len(first.sections[0].lines) == MEMORY_PAGE_SIZE
    assert first.footer == "Trang 1/2"
    assert any(action.action_id == "mem:t:ste:1" for action in first.actions)
    all_lines = first.sections[0].lines + second.sections[0].lines
    item_lines = [line for line in all_lines if "STE capability" in line]
    assert len(item_lines) == MEMORY_PAGE_SIZE + 1
    assert len(set(item_lines)) == MEMORY_PAGE_SIZE + 1
    assert any("STE capability 0" in line for line in item_lines)
    assert not any("tin cậy" in line for line in all_lines)
    assert not any("id:" in line for line in all_lines)
    assert not any("source:" in line for line in all_lines)
    assert any(action.action_id == "mem:o" for action in second.actions)


async def test_memory_review_explains_reason_without_backend_metadata(repos):
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
    assert any("ghi nhớ mới chưa được xác nhận" in line for line in review.sections[0].lines)
    assert not any("tin cậy" in line for line in review.sections[0].lines)
    assert not any("id:" in line for line in review.sections[0].lines)
    assert not any("profile/" in line for line in review.sections[0].lines)
    assert any(action.action_id == f"mem:k:{item.id}:review:0" for action in review.actions)
    assert any(action.action_id == f"mem:r:{item.id}:review:0" for action in review.actions)
    assert any(action.action_id == f"mem:s:{item.id}:review:0" for action in review.actions)

    await service.confirm(item.id)
    confirmed = await repos["memory"].get_by_id(item.id)
    assert confirmed is not None
    assert str(confirmed.status) == "active"
    assert confirmed.last_confirmed_at is not None

    refreshed = await service.topic("review", 0)
    assert refreshed is not None
    assert item.content not in "\n".join(refreshed.sections[0].lines)


async def test_candidate_appears_in_review_not_in_stale(repos):
    note = await repos["notes"].create(Note(raw_text="source"))
    candidate = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Memory candidate test.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.CANDIDATE,
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    review = await service.topic("review", 0)
    stale = await service.topic("stale", 0)

    assert review is not None
    assert stale is not None
    assert any(candidate.content in line for line in review.sections[0].lines)
    assert not any(candidate.content in line for line in stale.sections[0].lines)


async def test_expired_candidate_remains_in_review_with_warning(repos):
    note = await repos["notes"].create(Note(raw_text="source"))
    candidate = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROJECT,
            kind=MemoryKind.PROJECT_STATE,
            content="Expired candidate awaiting decision.",
            source_note_id=note.id,
            confidence=0.7,
            status=MemoryStatus.CANDIDATE,
            valid_until=datetime.now(UTC) - timedelta(days=1),
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    review = await service.topic("review", 0)
    stale = await service.topic("stale", 0)

    assert review is not None and stale is not None
    assert any(candidate.content in line for line in review.sections[0].lines)
    assert any("hết hiệu lực trước khi được duyệt" in line for line in review.sections[0].lines)
    assert not any(candidate.content in line for line in stale.sections[0].lines)


async def test_active_legacy_without_confirm_appears_in_review_not_in_stale(repos):
    note = await repos["notes"].create(Note(raw_text="source"))
    legacy = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Legacy memory never confirmed.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.ACTIVE,
            last_confirmed_at=None,
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    review = await service.topic("review", 0)
    stale = await service.topic("stale", 0)

    assert review is not None
    assert stale is not None
    assert any(legacy.content in line for line in review.sections[0].lines)
    assert not any(legacy.content in line for line in stale.sections[0].lines)


async def test_confirm_candidate_makes_it_disappear_from_review(repos):
    note = await repos["notes"].create(Note(raw_text="source"))
    item = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Candidate to confirm.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.CANDIDATE,
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    review_before = await service.topic("review", 0)
    assert review_before is not None
    assert any(item.content in line for line in review_before.sections[0].lines)

    await service.confirm(item.id)

    review_after = await service.topic("review", 0)
    assert review_after is not None
    assert not any(item.content in line for line in review_after.sections[0].lines)


async def test_confirm_low_confidence_candidate_preserves_confidence(repos):
    note = await repos["notes"].create(Note(raw_text="source"))
    item = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Low confidence candidate.",
            source_note_id=note.id,
            confidence=0.6,
            status=MemoryStatus.CANDIDATE,
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    await service.confirm(item.id)

    confirmed = await repos["memory"].get_by_id(item.id)
    assert confirmed is not None
    assert confirmed.confidence == 0.6
    assert str(confirmed.status) == "active"
    assert confirmed.last_confirmed_at is not None

    review = await service.topic("review", 0)
    assert review is not None
    assert not any(item.content in line for line in review.sections[0].lines)


async def test_active_confirmed_recently_not_in_review_or_stale(repos):
    note = await repos["notes"].create(Note(raw_text="source"))
    item = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Recently confirmed memory.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.ACTIVE,
            last_confirmed_at=datetime.now(UTC),
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    review = await service.topic("review", 0)
    stale = await service.topic("stale", 0)

    assert review is not None
    assert stale is not None
    assert not any(item.content in line for line in review.sections[0].lines)
    assert not any(item.content in line for line in stale.sections[0].lines)


async def test_active_confirmed_over_120_days_appears_in_stale(repos):
    note = await repos["notes"].create(Note(raw_text="source"))
    old_date = datetime.now(UTC) - timedelta(days=150)
    item = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Old confirmed memory.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.ACTIVE,
            last_confirmed_at=old_date,
            updated_at=old_date,
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    review = await service.topic("review", 0)
    stale = await service.topic("stale", 0)

    assert review is not None
    assert stale is not None
    assert not any(item.content in line for line in review.sections[0].lines)
    assert any(item.content in line for line in stale.sections[0].lines)


async def test_expired_active_appears_in_stale_not_in_review(repos):
    note = await repos["notes"].create(Note(raw_text="source"))
    expired_date = datetime.now(UTC) - timedelta(days=10)
    item = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROJECT,
            kind=MemoryKind.PROJECT_STATE,
            content="Expired project state.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.ACTIVE,
            last_confirmed_at=datetime.now(UTC),
            valid_until=expired_date,
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    review = await service.topic("review", 0)
    stale = await service.topic("stale", 0)

    assert review is not None
    assert stale is not None
    assert not any(item.content in line for line in review.sections[0].lines)
    assert any(item.content in line for line in stale.sections[0].lines)


async def test_review_and_stale_are_not_completely_overlapping(repos):
    note = await repos["notes"].create(Note(raw_text="source"))
    candidate = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Unique candidate.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.CANDIDATE,
        )
    )
    confirmed_recent = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Confirmed recently.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.ACTIVE,
            last_confirmed_at=datetime.now(UTC),
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    review = await service.topic("review", 0)
    stale = await service.topic("stale", 0)

    assert review is not None
    assert stale is not None

    review_items = [line for line in review.sections[0].lines if "Unique candidate" in line]
    stale_items = [line for line in stale.sections[0].lines if "Unique candidate" in line]
    assert len(review_items) == 1
    assert len(stale_items) == 0

    review_recent = [line for line in review.sections[0].lines if "Confirmed recently" in line]
    stale_recent = [line for line in stale.sections[0].lines if "Confirmed recently" in line]
    assert len(review_recent) == 0
    assert len(stale_recent) == 0


async def test_overview_counts_match_topic_lists(repos):
    note = await repos["notes"].create(Note(raw_text="source"))
    candidate = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Overview candidate.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.CANDIDATE,
        )
    )
    legacy = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Overview legacy.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.ACTIVE,
            last_confirmed_at=None,
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    overview = await service.overview()
    review_topic = await service.topic("review", 0)

    assert overview is not None
    assert review_topic is not None

    review_count_in_overview = int(
        [line for line in overview.sections[0].lines if "Review inbox" in line][0]
        .split(":")[1]
        .strip()
    )
    assert review_count_in_overview == 2
    assert review_topic.summary.startswith("2 mục")


async def test_review_shows_correct_reason_for_candidate(repos):
    note = await repos["notes"].create(Note(raw_text="source"))
    item = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Candidate reason test.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.CANDIDATE,
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    review = await service.topic("review", 0)

    assert review is not None
    assert any("ghi nhớ mới chưa được xác nhận" in line for line in review.sections[0].lines)


async def test_review_shows_correct_reason_for_legacy(repos):
    note = await repos["notes"].create(Note(raw_text="source"))
    item = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Legacy reason test.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.ACTIVE,
            last_confirmed_at=None,
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    review = await service.topic("review", 0)

    assert review is not None
    assert any("ghi nhớ cũ chưa từng được anh xác nhận" in line for line in review.sections[0].lines)


async def test_stale_shows_correct_reason_for_expired(repos):
    note = await repos["notes"].create(Note(raw_text="source"))
    expired_date = datetime.now(UTC) - timedelta(days=5)
    item = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROJECT,
            kind=MemoryKind.PROJECT_STATE,
            content="Expired stale test.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.ACTIVE,
            last_confirmed_at=datetime.now(UTC),
            valid_until=expired_date,
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    stale = await service.topic("stale", 0)

    assert stale is not None
    assert any("hết hiệu lực" in line for line in stale.sections[0].lines)


async def test_stale_shows_correct_reason_for_old_update(repos):
    note = await repos["notes"].create(Note(raw_text="source"))
    old_date = datetime.now(UTC) - timedelta(days=150)
    item = await repos["memory"].create(
        MemoryItem(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.FACT,
            content="Old update stale test.",
            source_note_id=note.id,
            confidence=0.9,
            status=MemoryStatus.ACTIVE,
            last_confirmed_at=old_date,
            updated_at=old_date,
        )
    )
    service = MemoryViewService(repos["memory"], repos["projects"], repos["people"])

    stale = await service.topic("stale", 0)

    assert stale is not None
    assert any("120 ngày" in line for line in stale.sections[0].lines)
