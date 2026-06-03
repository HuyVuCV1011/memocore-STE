from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from memocore.domain.models import ClarificationStatus, ReminderStatus
from memocore.domain.schemas import CaptureRequest
from memocore.services.clarification_service import ClarificationService, parse_clarification_datetime
from tests.conftest import FakeProvider
from tests.fixtures.extraction_responses import MISSING_REMINDER_TIME


def test_parse_clarification_datetime_tomorrow():
    now = datetime(2026, 6, 3, 10, 0, tzinfo=UTC)

    parsed = parse_clarification_datetime("tomorrow 9am", now=now)

    assert parsed == datetime(2026, 6, 4, 9, 0, tzinfo=UTC)


def test_parse_clarification_datetime_uses_explicit_timezone():
    vietnam = ZoneInfo("Asia/Ho_Chi_Minh")
    now = datetime(2026, 6, 3, 10, 0, tzinfo=vietnam)

    parsed = parse_clarification_datetime("tomorrow 9am", now=now, default_timezone=vietnam)

    assert parsed == datetime(2026, 6, 4, 2, 0, tzinfo=UTC)


def test_parse_clarification_datetime_relative_duration_before_clock_time():
    now = datetime(2026, 6, 3, 10, 0, tzinfo=UTC)

    parsed = parse_clarification_datetime("remind me tomorrow in 2 hours", now=now)

    assert parsed == datetime(2026, 6, 3, 12, 0, tzinfo=UTC)


async def test_capture_requests_clarification_for_missing_reminder_time(
    capture_service, fake_provider, repos
):
    fake_provider.response = MISSING_REMINDER_TIME

    response = await capture_service.capture(
        CaptureRequest(
            raw_text="Remind me to call John",
            source_chat_id="123",
            source_message_id="456",
        )
    )
    pending = await repos["clarifications"].find_pending_for_chat("123")
    reminders = await repos["reminders"].list_by_note(response.note_id)

    assert response.clarification_question == 'When should I remind you about "Call John"?'
    assert pending.status == ClarificationStatus.PENDING
    assert reminders[0].status == ReminderStatus.CANDIDATE
    assert reminders[0].remind_at is None


async def test_answer_pending_clarification_schedules_reminder(capture_service, fake_provider, repos):
    fake_provider.response = MISSING_REMINDER_TIME
    response = await capture_service.capture(
        CaptureRequest(raw_text="Remind me to call John", source_chat_id="123")
    )
    reminder = (await repos["reminders"].list_by_note(response.note_id))[0]
    service = capture_service.clarification_service

    result = await service.answer_pending("123", "tomorrow 9am")
    updated = await repos["reminders"].get_by_id(reminder.id)
    pending = await repos["clarifications"].find_pending_for_chat("123")

    assert isinstance(service, ClarificationService)
    assert result.handled is True
    assert "Reminder set" in result.message
    assert updated.status == ReminderStatus.SCHEDULED
    assert updated.remind_at is not None
    assert pending is None
