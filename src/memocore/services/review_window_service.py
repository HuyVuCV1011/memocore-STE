from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import sqlite3
from pathlib import Path
from typing import Any

from memocore.domain.models import EventType, FeedbackSignal, FeedbackStatus


HIGH_TRUST_CATEGORIES = {"wrong_entity", "unintended_write", "broken_undo"}


@dataclass(frozen=True)
class ReviewWindowReport:
    status: str
    required_days: int
    observed_days: int
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
            f"{self.event_count} event(s), {self.unresolved_high_severity_count} unresolved high, "
            f"{self.wrong_entity_count} wrong-entity, {self.unintended_write_count} unintended-write"
        )

    def lines(self) -> list[str]:
        return [
            f"Status: {self.status}",
            f"Window: {self.window_start.date()} to {self.window_end.date()} UTC",
            f"Observed days: {self.observed_days}/{self.required_days}",
            f"Events observed: {self.event_count}",
            f"Corrections: {self.correction_count} total, {self.open_correction_count} open",
            f"Clarification failures: {self.clarification_failed_count}",
            f"Undo events: {self.undo_count}",
            f"Wrong-entity durable writes: {self.wrong_entity_count}",
            f"Unintended writes: {self.unintended_write_count}",
            f"High/critical trust events: {self.high_severity_count} total, {self.unresolved_high_severity_count} unresolved",
        ]


def review_window_report(
    database_path: Path,
    *,
    required_days: int = 14,
    now: datetime | None = None,
) -> ReviewWindowReport:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    window_start = now - timedelta(days=required_days)
    events = _load_events(database_path, since=window_start)
    observed_days = len({event["created_at"].date() for event in events})
    metrics = _metrics(events)
    status = _status(required_days, observed_days, metrics)
    return ReviewWindowReport(
        status=status,
        required_days=required_days,
        observed_days=observed_days,
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
        window_end=now,
    )


def _load_events(database_path: Path, *, since: datetime) -> list[dict[str, Any]]:
    if str(database_path) == ":memory:" or not database_path.exists():
        return []
    try:
        conn = sqlite3.connect(database_path)
        rows = conn.execute(
            """
            SELECT id, event_type, entity_type, entity_id, payload, created_at
            FROM event_logs
            WHERE created_at >= ?
            ORDER BY created_at ASC
            """,
            (since.isoformat(),),
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
        severity = str(payload.get("severity") or payload.get("details", {}).get("severity") or "")
        if signal == FeedbackSignal.CORRECTION.value:
            metrics["corrections"] += 1
            if status == FeedbackStatus.OPEN.value:
                metrics["open_corrections"] += 1
        if category == "wrong_entity":
            metrics["wrong_entity"] += 1
        if category == "unintended_write":
            metrics["unintended_write"] += 1
        if category in HIGH_TRUST_CATEGORIES or severity in {"high", "critical"}:
            metrics["high_severity"] += 1
            if event["id"] not in resolved_feedback_ids and status != FeedbackStatus.RESOLVED.value:
                metrics["unresolved_high_severity"] += 1
    return metrics


def _status(required_days: int, observed_days: int, metrics: Counter) -> str:
    if (
        metrics["unresolved_high_severity"]
        or metrics["wrong_entity"]
        or metrics["unintended_write"]
    ):
        return "failed"
    if observed_days < required_days:
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
