from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
import json
import sqlite3
from pathlib import Path
from typing import Any

from memocore.domain.models import EventType, FeedbackSignal, FeedbackStatus
from memocore.services.event_service import feedback_requires_regression


@dataclass(frozen=True)
class ReviewWindowReport:
    status: str
    required_days: int
    observed_days: int
    explicit_observed_days: int
    legacy_verified_days: int
    current_streak_days: int
    event_count: int
    correction_count: int
    open_correction_count: int
    clarification_failed_count: int
    undo_count: int
    wrong_entity_count: int
    unintended_write_count: int
    high_severity_count: int
    unresolved_high_severity_count: int
    window_start: datetime
    window_end: datetime

    @property
    def gate_passed(self) -> bool:
        return self.status == "passed"

    def summary(self) -> str:
        return (
            f"{self.status}: {self.observed_days}/{self.required_days} observed day(s), "
            f"streak={self.current_streak_days}, explicit={self.explicit_observed_days}, "
            f"legacy={self.legacy_verified_days}, "
            f"{self.event_count} event(s), "
            f"{self.unresolved_high_severity_count} unresolved regression-required, "
            f"{self.wrong_entity_count} wrong-entity, {self.unintended_write_count} unintended-write"
        )

    def lines(self) -> list[str]:
        return [
            f"Status: {self.status}",
            f"Window: {self.window_start.date()} to {self.window_end.date()} UTC",
            f"Observed days: {self.observed_days}/{self.required_days}",
            f"Current consecutive streak: {self.current_streak_days} day(s)",
            f"Explicit owner-interaction days: {self.explicit_observed_days}",
            f"Verified legacy Telegram-note days: {self.legacy_verified_days}",
            f"Events observed: {self.event_count}",
            f"Corrections: {self.correction_count} total, {self.open_correction_count} open",
            f"Clarification failures: {self.clarification_failed_count}",
            f"Undo events: {self.undo_count}",
            f"Wrong-entity durable writes: {self.wrong_entity_count}",
            f"Unintended writes: {self.unintended_write_count}",
            f"Regression-required feedback: {self.high_severity_count} total, "
            f"{self.unresolved_high_severity_count} unresolved",
        ]


def review_window_report(
    database_path: Path,
    *,
    required_days: int = 14,
    now: datetime | None = None,
    telegram_owner_id: int | None = None,
    display_timezone: tzinfo = UTC,
) -> ReviewWindowReport:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    local_today = now.astimezone(display_timezone).date()
    lookup_start = datetime.combine(
        local_today - timedelta(days=required_days),
        datetime.min.time(),
        tzinfo=display_timezone,
    ).astimezone(UTC)
    lookup_events = _load_events(database_path, since=lookup_start, until=now)
    lookup_explicit_days = {
        observation_day
        for event in lookup_events
        if (
            observation_day := _validated_observation_day(
                event, display_timezone=display_timezone
            )
        )
        is not None
    }
    lookup_legacy_days = _load_verified_legacy_days(
        database_path,
        since=lookup_start,
        until=now,
        telegram_owner_id=telegram_owner_id,
        display_timezone=display_timezone,
    )
    lookup_observed_days = lookup_explicit_days | lookup_legacy_days
    streak_end = local_today if local_today in lookup_observed_days else local_today - timedelta(days=1)
    interval_start_day = streak_end - timedelta(days=required_days - 1)
    interval_days = {
        interval_start_day + timedelta(days=offset) for offset in range(required_days)
    }
    window_start = datetime.combine(
        interval_start_day, datetime.min.time(), tzinfo=display_timezone
    ).astimezone(UTC)
    if streak_end == local_today:
        window_end = now
        events = [
            event
            for event in lookup_events
            if window_start <= event["created_at"] <= window_end
        ]
    else:
        window_end = datetime.combine(
            local_today, datetime.min.time(), tzinfo=display_timezone
        ).astimezone(UTC)
        events = [
            event
            for event in lookup_events
            if window_start <= event["created_at"] < window_end
        ]
    explicit_days = lookup_explicit_days & interval_days
    legacy_days = lookup_legacy_days & interval_days
    all_observed_days = explicit_days | legacy_days
    observed_days = len(all_observed_days)
    current_streak_days = _consecutive_streak_days(
        lookup_observed_days, streak_end=streak_end
    )
    metrics = _metrics(events)
    status = _status(required_days, current_streak_days, metrics)
    return ReviewWindowReport(
        status=status,
        required_days=required_days,
        observed_days=observed_days,
        explicit_observed_days=len(explicit_days),
        legacy_verified_days=len(legacy_days),
        current_streak_days=current_streak_days,
        event_count=len(events),
        correction_count=metrics["corrections"],
        open_correction_count=metrics["open_corrections"],
        clarification_failed_count=metrics["clarification_failed"],
        undo_count=metrics["undo"],
        wrong_entity_count=metrics["wrong_entity"],
        unintended_write_count=metrics["unintended_write"],
        high_severity_count=metrics["high_severity"],
        unresolved_high_severity_count=metrics["unresolved_high_severity"],
        window_start=window_start,
        window_end=window_end,
    )


def _load_events(
    database_path: Path, *, since: datetime, until: datetime
) -> list[dict[str, Any]]:
    if str(database_path) == ":memory:" or not database_path.exists():
        return []
    try:
        conn = sqlite3.connect(database_path)
        rows = conn.execute(
            """
            SELECT id, event_type, entity_type, entity_id, payload, created_at
            FROM event_logs
            WHERE created_at >= ? AND created_at <= ?
            ORDER BY created_at ASC
            """,
            (since.isoformat(), until.isoformat()),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    return [
        {
            "id": row[0],
            "event_type": row[1],
            "entity_type": row[2],
            "entity_id": row[3],
            "payload": _payload(row[4]),
            "created_at": _parse_created_at(row[5]),
        }
        for row in rows
    ]


def _load_verified_legacy_days(
    database_path: Path,
    *,
    since: datetime,
    until: datetime,
    telegram_owner_id: int | None,
    display_timezone: tzinfo,
) -> set[date]:
    """Read legacy Telegram evidence without mutating or backfilling the database."""
    if telegram_owner_id is None or str(database_path) == ":memory:" or not database_path.exists():
        return set()
    try:
        conn = sqlite3.connect(database_path)
        rows = conn.execute(
            """
            SELECT created_at
            FROM notes
            WHERE source = 'telegram'
              AND source_chat_id = ?
              AND source_message_id IS NOT NULL
              AND TRIM(source_message_id) != ''
              AND created_at >= ? AND created_at <= ?
            """,
            (str(telegram_owner_id), since.isoformat(), until.isoformat()),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return set()
    return {_parse_created_at(row[0]).astimezone(display_timezone).date() for row in rows}


def _validated_observation_day(
    event: dict[str, Any], *, display_timezone: tzinfo
) -> date | None:
    if (
        event.get("event_type") != EventType.TELEGRAM_OWNER_INTERACTION_OBSERVED.value
        or event.get("entity_type") != "review_window_day"
    ):
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    observation_day = payload.get("observation_day")
    if (
        payload.get("schema_version") != 1
        or payload.get("metadata_policy_version") != 1
        or payload.get("provenance") != "telegram_owner_private"
        or payload.get("interaction_kind") not in {"command", "message", "callback"}
        or not isinstance(observation_day, str)
        or event.get("entity_id") != observation_day
    ):
        return None
    try:
        parsed_day = date.fromisoformat(observation_day)
    except ValueError:
        return None
    created_at = event.get("created_at")
    if not isinstance(created_at, datetime):
        return None
    return parsed_day if created_at.astimezone(display_timezone).date() == parsed_day else None


def _consecutive_streak_days(observed_days: set[date], *, streak_end: date) -> int:
    cursor = streak_end
    if cursor not in observed_days:
        return 0
    streak = 0
    while cursor in observed_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _metrics(events: list[dict[str, Any]]) -> Counter:
    metrics: Counter = Counter()
    resolved_feedback_ids = {
        event["payload"].get("feedback_event_id")
        for event in events
        if event["event_type"] == EventType.USER_FEEDBACK_RESOLVED.value
    }
    for event in events:
        event_type = event["event_type"]
        payload = event["payload"]
        if event_type == EventType.CLARIFICATION_FAILED.value:
            metrics["clarification_failed"] += 1
        if event_type == EventType.WORK_ITEM_UNDONE.value:
            metrics["undo"] += 1
        if event_type != EventType.USER_FEEDBACK_RECORDED.value:
            continue
        signal = payload.get("signal")
        status = payload.get("status")
        category = _trust_category(payload)
        if signal == FeedbackSignal.CORRECTION.value:
            metrics["corrections"] += 1
            if status == FeedbackStatus.OPEN.value:
                metrics["open_corrections"] += 1
        if category == "wrong_entity":
            metrics["wrong_entity"] += 1
        if category == "unintended_write":
            metrics["unintended_write"] += 1
        if feedback_requires_regression(payload):
            metrics["high_severity"] += 1
            if event["id"] not in resolved_feedback_ids and status != FeedbackStatus.RESOLVED.value:
                metrics["unresolved_high_severity"] += 1
    return metrics


def _status(required_days: int, current_streak_days: int, metrics: Counter) -> str:
    if (
        metrics["unresolved_high_severity"]
        or metrics["wrong_entity"]
        or metrics["unintended_write"]
    ):
        return "failed"
    if current_streak_days < required_days:
        return "collecting"
    return "passed"


def _payload(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _parse_created_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _trust_category(payload: dict[str, Any]) -> str:
    details = payload.get("details")
    if not isinstance(details, dict):
        details = {}
    category = (
        payload.get("trust_category")
        or payload.get("category")
        or details.get("trust_category")
        or details.get("category")
        or details.get("issue_type")
    )
    return str(category or "")
