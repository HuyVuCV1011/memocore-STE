from datetime import UTC, datetime, timedelta

from memocore.domain.models import EventType, FeedbackSignal, FeedbackStatus
from memocore.services.event_service import EventService
from memocore.services.review_window_service import review_window_report


async def test_review_window_collects_until_required_days(tmp_database):
    report = review_window_report(
        tmp_database.db_path,
        required_days=14,
        now=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )

    assert report.status == "collecting"
    assert report.gate_passed is False
    assert report.observed_days == 0


async def test_review_window_passes_after_clean_observation_days(repos, tmp_database):
    events = EventService(repos["events"])
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    for offset in range(3):
        await events.append_event(
            EventType.NOTE_CAPTURED,
            "note",
            f"note-{offset}",
            created_at=now - timedelta(days=offset),
        )

    report = review_window_report(tmp_database.db_path, required_days=3, now=now)

    assert report.status == "passed"
    assert report.gate_passed is True
    assert report.observed_days == 3
    assert report.event_count == 3


async def test_review_window_fails_on_wrong_entity_feedback(repos, tmp_database):
    events = EventService(repos["events"])
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    await events.append_event(
        EventType.USER_FEEDBACK_RECORDED,
        "task",
        "task-1",
        {
            "schema_version": 1,
            "signal": FeedbackSignal.CORRECTION.value,
            "status": FeedbackStatus.OPEN.value,
            "details": {"category": "wrong_entity", "severity": "high"},
        },
        created_at=now,
    )

    report = review_window_report(tmp_database.db_path, required_days=1, now=now)

    assert report.status == "failed"
    assert report.wrong_entity_count == 1
    assert report.high_severity_count == 1
    assert report.unresolved_high_severity_count == 1
