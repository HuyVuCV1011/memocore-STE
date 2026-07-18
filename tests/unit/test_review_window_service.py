from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from memocore.domain.models import EventType, FeedbackSignal, FeedbackStatus, Note
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
        await events.record_owner_observation(
            "message",
            observed_at=now - timedelta(days=offset),
            display_timezone=UTC,
        )

    report = review_window_report(tmp_database.db_path, required_days=3, now=now)

    assert report.status == "passed"
    assert report.gate_passed is True
    assert report.observed_days == 3
    assert report.event_count == 3


async def test_review_window_does_not_count_arbitrary_or_scheduled_events(repos, tmp_database):
    events = EventService(repos["events"])
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    for event_type in (EventType.NOTE_CAPTURED, EventType.BRIEFING_SENT, EventType.NUDGE_SENT):
        await events.append_event(event_type, "system", event_type.value, created_at=now)

    report = review_window_report(tmp_database.db_path, required_days=1, now=now)

    assert report.status == "collecting"
    assert report.observed_days == 0
    assert report.explicit_observed_days == 0


async def test_review_window_rejects_malformed_owner_observation(repos, tmp_database):
    events = EventService(repos["events"])
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    await events.append_event(
        EventType.TELEGRAM_OWNER_INTERACTION_OBSERVED,
        "telegram_owner",
        "owner",
        {"interaction_kind": "message", "observation_day": "2026-07-15"},
        created_at=now,
    )

    report = review_window_report(tmp_database.db_path, required_days=1, now=now)

    assert report.explicit_observed_days == 0
    assert report.status == "collecting"


async def test_review_window_requires_streak_ending_today_or_yesterday(repos, tmp_database):
    events = EventService(repos["events"])
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    for offset in (0, 2, 3):
        await events.record_owner_observation(
            "message",
            observed_at=now - timedelta(days=offset),
            display_timezone=UTC,
        )

    report = review_window_report(tmp_database.db_path, required_days=3, now=now)

    assert report.observed_days == 2
    assert report.current_streak_days == 1
    assert report.status == "collecting"


async def test_review_window_accepts_consecutive_streak_ending_yesterday(repos, tmp_database):
    events = EventService(repos["events"])
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    for offset in (1, 2, 3):
        await events.record_owner_observation(
            "message",
            observed_at=now - timedelta(days=offset),
            display_timezone=UTC,
        )

    report = review_window_report(tmp_database.db_path, required_days=3, now=now)

    assert report.current_streak_days == 3
    assert report.status == "passed"


async def test_review_window_uses_owner_local_day_across_utc_midnight(repos, tmp_database):
    events = EventService(repos["events"])
    timezone = ZoneInfo("Asia/Bangkok")
    observed_at = datetime(2026, 7, 16, 17, 30, tzinfo=UTC)
    await events.record_owner_observation(
        "message", observed_at=observed_at, display_timezone=timezone
    )

    report = review_window_report(
        tmp_database.db_path,
        required_days=1,
        now=observed_at + timedelta(minutes=30),
        display_timezone=timezone,
    )

    assert report.current_streak_days == 1
    assert report.explicit_observed_days == 1
    assert report.status == "passed"


async def test_review_window_uses_exact_required_day_interval_boundaries(repos, tmp_database):
    events = EventService(repos["events"])
    now = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
    await events.record_owner_observation(
        "message", observed_at=now - timedelta(hours=1), display_timezone=UTC
    )
    exact_start = datetime(2026, 7, 15, 0, 0, tzinfo=UTC)
    await events.append_event(
        EventType.WORK_ITEM_UNDONE,
        "work_event",
        "at-start",
        created_at=exact_start,
    )
    await events.append_event(
        EventType.WORK_ITEM_UNDONE,
        "work_event",
        "before-start",
        created_at=exact_start - timedelta(microseconds=1),
    )
    await events.append_event(
        EventType.CLARIFICATION_FAILED,
        "clarification",
        "at-end",
        created_at=now,
    )

    report = review_window_report(tmp_database.db_path, required_days=3, now=now)

    assert report.window_start == exact_start
    assert report.window_end == now
    assert report.undo_count == 1
    assert report.clarification_failed_count == 1


async def test_review_window_counts_verified_legacy_notes_without_backfill(repos, tmp_database):
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    await repos["notes"].create(
        Note(
            source="telegram",
            source_chat_id="9001",
            source_message_id="501",
            raw_text="private text",
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
    )
    await repos["notes"].create(
        Note(
            source="telegram",
            source_chat_id="other-owner",
            source_message_id="502",
            raw_text="not owner evidence",
            created_at=now,
            updated_at=now,
        )
    )

    without_owner = review_window_report(tmp_database.db_path, required_days=1, now=now)
    report = review_window_report(
        tmp_database.db_path,
        required_days=1,
        now=now,
        telegram_owner_id=9001,
    )
    stored_events = await EventService(repos["events"]).list_recent(limit=10)

    assert without_owner.legacy_verified_days == 0
    assert report.legacy_verified_days == 1
    assert report.explicit_observed_days == 0
    assert report.current_streak_days == 1
    assert report.status == "passed"
    assert stored_events == []


async def test_review_window_fails_on_wrong_entity_feedback(repos, tmp_database):
    events = EventService(repos["events"])
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    await events.record_owner_observation(
        "message", observed_at=now, display_timezone=UTC
    )
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
