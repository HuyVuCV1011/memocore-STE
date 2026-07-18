from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from memocore.domain.recurrence import (
    future_recurrence_occurrence,
    next_recurrence_occurrence,
)


def test_interval_day_recurrence_next_occurrence():
    current = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)

    assert next_recurrence_occurrence(current, "interval:2d") == current + timedelta(days=2)


def test_interval_week_recurrence_next_occurrence():
    current = datetime(2026, 7, 15, 9, 0, tzinfo=UTC)

    assert next_recurrence_occurrence(current, "interval:3w") == current + timedelta(weeks=3)


def test_interval_backlog_skips_to_future():
    current = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    missed, next_due = future_recurrence_occurrence(
        current,
        "interval:2d",
        datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
    )

    assert missed == 3
    assert next_due == datetime(2026, 7, 7, 9, 0, tzinfo=UTC)


def test_invalid_interval_recurrence_fails_closed():
    with pytest.raises(ValueError):
        next_recurrence_occurrence(datetime(2026, 7, 15, tzinfo=UTC), "interval:0d")
