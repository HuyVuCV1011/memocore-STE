from __future__ import annotations

from datetime import datetime, timedelta
import re


def next_recurrence_occurrence(
    occurrence_at: datetime,
    recurrence_rule: str,
) -> datetime:
    if recurrence_rule == "daily":
        return occurrence_at + timedelta(days=1)
    if recurrence_rule == "weekly" or recurrence_rule.startswith("weekly:"):
        return occurrence_at + timedelta(weeks=1)
    interval = parse_interval_recurrence(recurrence_rule)
    if interval is not None:
        unit, count = interval
        return occurrence_at + (
            timedelta(days=count) if unit == "d" else timedelta(weeks=count)
        )
    raise ValueError(f"Unsupported task recurrence rule: {recurrence_rule}")


def future_recurrence_occurrence(
    occurrence_at: datetime,
    recurrence_rule: str,
    now: datetime,
    *,
    max_occurrences: int = 10_000,
) -> tuple[int, datetime]:
    missed_count = 0
    candidate = occurrence_at
    while candidate <= now:
        missed_count += 1
        if missed_count > max_occurrences:
            raise ValueError("Recurrence backlog exceeds safety limit")
        candidate = next_recurrence_occurrence(candidate, recurrence_rule)
    return missed_count, candidate


def parse_interval_recurrence(recurrence_rule: str) -> tuple[str, int] | None:
    match = re.fullmatch(r"interval:(\d+)([dw])", recurrence_rule)
    if match is None:
        return None
    count = int(match.group(1))
    if count < 1:
        return None
    return match.group(2), count
