import asyncio
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

import pytest

from memocore.domain.models import EventLog, EventType, FeedbackSignal
from memocore.services.event_service import EventService, valid_feedback_payload


async def test_event_creation_round_trip(repos):
    service = EventService(repos["events"])

    event = await service.append_event(
        EventType.NOTE_CAPTURED, "note", "note-1", {"raw": True}
    )
    events = await service.list_events_for_entity("note", "note-1")

    assert event.id
    assert events[0].payload == {"raw": True}


async def test_feedback_details_are_centrally_allowlisted_and_flat(repos):
    service = EventService(repos["events"])

    event = await service.record_feedback(
        FeedbackSignal.CORRECTION,
        "task",
        "task-1",
        source_chat_id="9001",
        source_message_id="501",
        details={
            "severity": "high",
            "category": "wrong_entity",
            "raw_text": "private user content",
            "source_chat_id": "9001",
            "source_message_id": "501",
            "unknown": "drop me",
            "issue_type": {"nested": "drop me"},
            "suggestion_event_id": "event-123",
        },
    )

    assert event.payload["details"] == {
        "severity": "high",
        "category": "wrong_entity",
        "operation_id": "event-123",
    }
    assert "private user content" not in str(event.payload)
    assert "9001" not in str(event.payload)
    assert "501" not in str(event.payload)
    assert "turn" not in event.payload
    assert event.payload["metadata_policy_version"] == 1
    assert event.payload["provenance"] == "telegram_owner_private"


async def test_feedback_sanitizer_normalizes_aliases_and_rejects_unsafe_values(repos):
    service = EventService(repos["events"])

    event = await service.record_feedback(
        FeedbackSignal.CORRECTION,
        "task",
        "task-1",
        details={
            "trust_category": " WRONG_ENTITY ",
            "severity": "CRITICAL",
            "reason_code": "routing.failure-1",
            "resolution_code": "x" * 65,
            "suggestion_event_id": "operation:123",
            "operation_id": {"nested": True},
            "category": True,
            "raw": "private",
        },
    )

    assert event.payload["details"] == {
        "category": "wrong_entity",
        "severity": "critical",
        "reason_code": "routing.failure-1",
        "operation_id": "operation:123",
    }


async def test_owner_observation_deduplicates_owner_local_day(repos):
    service = EventService(repos["events"])
    timezone = ZoneInfo("Asia/Bangkok")
    first_time = datetime(2026, 7, 16, 17, 30, tzinfo=UTC)

    first = await service.record_owner_observation(
        "message", observed_at=first_time, display_timezone=timezone
    )
    second = await service.record_owner_observation(
        "command", observed_at=first_time + timedelta(hours=3), display_timezone=timezone
    )
    stored = await service.list_events_for_entity("review_window_day", "2026-07-17")

    assert second.id == first.id
    assert len(stored) == 1
    assert first.entity_type == "review_window_day"
    assert first.entity_id == "2026-07-17"
    assert first.payload == {
        "schema_version": 1,
        "metadata_policy_version": 1,
        "provenance": "telegram_owner_private",
        "interaction_kind": "message",
        "observation_day": "2026-07-17",
    }


async def test_owner_observation_concurrent_writes_are_atomic(repos):
    service = EventService(repos["events"])
    observed_at = datetime(2026, 7, 17, 5, 0, tzinfo=UTC)

    results = await asyncio.gather(
        *(
            service.record_owner_observation(
                "message", observed_at=observed_at, display_timezone=UTC
            )
            for _ in range(8)
        )
    )
    stored = await service.list_events_for_entity("review_window_day", "2026-07-17")

    assert len({event.id for event in results}) == 1
    assert len(stored) == 1


async def test_concurrent_transactions_are_serialized_on_single_sqlite_connection(repos):
    database = repos["events"].database

    async def write_event(index: int) -> None:
        async with database.transaction():
            await EventService(repos["events"]).append_event(
                EventType.WORK_ITEM_CHANGED,
                "task",
                f"task-{index}",
                {"index": index},
                created_at=datetime(2026, 7, 17, 5, index, tzinfo=UTC),
            )
            await asyncio.sleep(0)

    await asyncio.gather(*(write_event(index) for index in range(8)))

    stored = await EventService(repos["events"]).list_recent(
        EventType.WORK_ITEM_CHANGED,
        limit=10,
    )
    assert len(stored) == 8


async def test_owner_observation_rejects_poisoned_deterministic_key(repos):
    service = EventService(repos["events"])
    observation_day = "2026-07-17"
    event_id = str(
        uuid5(NAMESPACE_URL, f"memocore:owner-observation:{observation_day}")
    )
    await repos["events"].create(
        EventLog(
            id=event_id,
            event_type=EventType.NOTE_CAPTURED,
            entity_type="note",
            entity_id="poisoned",
            payload={"observation_day": observation_day},
            created_at=datetime(2026, 7, 17, 5, 0, tzinfo=UTC),
        )
    )

    with pytest.raises(RuntimeError, match="key collision"):
        await service.record_owner_observation(
            "message",
            observed_at=datetime(2026, 7, 17, 6, 0, tzinfo=UTC),
            display_timezone=UTC,
        )

    stored = await service.list_events_for_entity(
        "review_window_day", observation_day
    )
    assert stored == []


async def test_feedback_partial_transport_pair_is_not_production_provenance(repos):
    service = EventService(repos["events"])

    event = await service.record_feedback(
        FeedbackSignal.CORRECTION,
        "task",
        "task-1",
        source_chat_id="owner-chat",
    )

    assert "provenance" not in event.payload
    assert "owner-chat" not in str(event.payload)


async def test_feedback_rejects_unsafe_top_level_metadata(repos):
    service = EventService(repos["events"])

    with pytest.raises(ValueError, match="action"):
        await service.record_feedback(
            FeedbackSignal.CORRECTION,
            "task",
            "task-1",
            action="raw action with spaces",
        )
    assert not valid_feedback_payload(
        {
            "schema_version": 1,
            "metadata_policy_version": 1,
            "signal": "correction",
            "status": "open",
            "artifact": {"type": "task", "id": "task-1"},
            "source_note_id": None,
            "unknown_raw": "must fail",
        }
    )
