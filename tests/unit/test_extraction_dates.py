from datetime import date

from memocore.services.task_extraction_service import _next_weekday


def test_next_monday_is_future_when_current_day_is_monday():
    assert _next_weekday(date(2026, 6, 1), weekday=0) == date(2026, 6, 8)


def test_next_monday_from_midweek():
    assert _next_weekday(date(2026, 6, 3), weekday=0) == date(2026, 6, 8)
