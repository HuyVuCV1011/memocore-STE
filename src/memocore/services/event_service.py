from datetime import UTC, datetime, tzinfo
import re
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from memocore.adapters.storage.repositories import EventLogRepository
from memocore.domain.models import (
    EventLog,
    EventType,
    FeedbackSignal,
    FeedbackStatus,
)


FEEDBACK_CATEGORIES = frozenset(
    {
        "wrong_intent",
        "wrong_entity",
        "unintended_write",
        "clarification_failed",
        "broken_undo",
        "presentation",
        "ranking",
        "routing",
        "other",
    }
)
FEEDBACK_SEVERITIES = frozenset({"low", "medium", "high", "critical"})
HIGH_TRUST_CATEGORIES = frozenset({"wrong_entity", "unintended_write", "broken_undo"})
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_FEEDBACK_REQUIRED_KEYS = frozenset(
    {"schema_version", "metadata_policy_version", "signal", "status", "artifact", "source_note_id"}
)
_FEEDBACK_OPTIONAL_KEYS = frozenset({"action", "details", "provenance"})


def sanitize_feedback_details(details: dict | None) -> dict:
    """Keep only documented, scalar quality metadata.

    Feedback is durable product evidence, so raw user content, transport IDs, nested
    objects, and future unreviewed keys must not be copied into its payload.
    """
    if not isinstance(details, dict):
        return {}
    safe: dict[str, str] = {}
    normalized_category = next(
        (
            normalized
            for key in ("category", "trust_category", "issue_type")
            if (normalized := _normalized_enum(details.get(key), FEEDBACK_CATEGORIES))
        ),
        None,
    )
    if normalized_category:
        safe["category"] = normalized_category
    severity = _normalized_enum(details.get("severity"), FEEDBACK_SEVERITIES)
    if severity:
        safe["severity"] = severity
    for key in ("reason_code", "resolution_code"):
        token = _safe_token(details.get(key), max_length=64, lowercase=True)
        if token:
            safe[key] = token
    token = next(
        (
            candidate
            for key in ("operation_id", "suggestion_event_id")
            if (candidate := _safe_token(details.get(key), max_length=128))
        ),
        None,
    )
    if token:
        safe["operation_id"] = token
    return safe


def feedback_requires_regression(payload: dict) -> bool:
    details = payload.get("details")
    details = details if isinstance(details, dict) else {}
    severity = str(payload.get("severity") or details.get("severity") or "").lower()
    category = str(
        payload.get("category")
        or payload.get("trust_category")
        or details.get("category")
        or details.get("trust_category")
        or details.get("issue_type")
        or ""
    ).lower()
    return severity in {"high", "critical"} or category in HIGH_TRUST_CATEGORIES


def valid_feedback_payload(payload: object, *, require_production: bool = False) -> bool:
    if not isinstance(payload, dict):
        return False
    keys = set(payload)
    if not _FEEDBACK_REQUIRED_KEYS.issubset(keys):
        return False
    if not keys.issubset(_FEEDBACK_REQUIRED_KEYS | _FEEDBACK_OPTIONAL_KEYS):
        return False
    artifact = payload.get("artifact")
    details = payload.get("details")
    source_note_id = payload.get("source_note_id")
    action = payload.get("action")
    provenance = payload.get("provenance")
    return (
        payload.get("schema_version") == 1
        and payload.get("metadata_policy_version") == 1
        and payload.get("signal") in {signal.value for signal in FeedbackSignal}
        and payload.get("status") in {status.value for status in FeedbackStatus}
        and isinstance(artifact, dict)
        and set(artifact) == {"type", "id"}
        and _safe_token(artifact.get("type"), max_length=64) is not None
        and _safe_token(artifact.get("id"), max_length=128) is not None
        and (
            source_note_id is None
            or _safe_token(source_note_id, max_length=128) is not None
        )
        and (action is None or _safe_token(action, max_length=64) is not None)
        and (
            details is None
            or (isinstance(details, dict) and sanitize_feedback_details(details) == details)
        )
        and (
            provenance == "telegram_owner_private"
            if require_production
            else provenance in {None, "telegram_owner_private"}
        )
    )


def _normalized_enum(value: object, allowed: frozenset[str]) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in allowed else None


def _safe_token(
    value: object, *, max_length: int, lowercase: bool = False
) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token or len(token) > max_length or _SAFE_TOKEN.fullmatch(token) is None:
        return None
    return token.lower() if lowercase else token


class EventService:
    def __init__(self, event_repo: EventLogRepository):
        self.event_repo = event_repo

    async def append_event(
        self,
        event_type: EventType,
        entity_type: str,
        entity_id: str,
        payload: dict | None = None,
        created_at: datetime | None = None,
    ) -> EventLog:
        event = EventLog(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload or {},
            created_at=created_at or datetime.now(UTC),
        )
        return await self.event_repo.create(event)

    async def list_events_for_entity(self, entity_type: str, entity_id: str) -> list[EventLog]:
        return await self.event_repo.list_by_entity(entity_type, entity_id)

    async def get_event(self, event_id: str) -> EventLog | None:
        return await self.event_repo.get_by_id(event_id)

    async def list_recent(
        self,
        event_type: EventType | None = None,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[EventLog]:
        return await self.event_repo.list_recent(event_type=event_type, since=since, limit=limit)

    async def was_undone(self, event_id: str) -> bool:
        events = await self.event_repo.list_by_entity("work_event", event_id)
        return any(event.event_type == EventType.WORK_ITEM_UNDONE for event in events)

    async def exists_recent(
        self,
        event_type: EventType,
        entity_type: str,
        entity_id: str,
        since: datetime,
    ) -> bool:
        return await self.event_repo.exists_recent(event_type, entity_type, entity_id, since)

    async def record_owner_observation(
        self,
        interaction_kind: str,
        *,
        observed_at: datetime | None = None,
        display_timezone: tzinfo,
    ) -> EventLog:
        if interaction_kind not in {"command", "message", "callback"}:
            raise ValueError("interaction_kind must be command, message, or callback")
        observed_at = observed_at or datetime.now(UTC)
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        observed_at = observed_at.astimezone(UTC)
        observation_day = observed_at.astimezone(display_timezone).date().isoformat()
        event_id = str(uuid5(NAMESPACE_URL, f"memocore:owner-observation:{observation_day}"))
        event = EventLog(
            id=event_id,
            event_type=EventType.TELEGRAM_OWNER_INTERACTION_OBSERVED,
            entity_type="review_window_day",
            entity_id=observation_day,
            payload={
                "schema_version": 1,
                "metadata_policy_version": 1,
                "provenance": "telegram_owner_private",
                "interaction_kind": interaction_kind,
                "observation_day": observation_day,
            },
            created_at=observed_at,
        )
        try:
            return await self.event_repo.create(event)
        except sqlite3.IntegrityError as exc:
            existing = await self.event_repo.get_by_id(event_id)
            if existing is not None and _valid_owner_observation(
                existing,
                observation_day=observation_day,
                display_timezone=display_timezone,
            ):
                return existing
            raise RuntimeError(
                f"deterministic owner-observation key collision for {observation_day}"
            ) from exc

    async def record_feedback(
        self,
        signal: FeedbackSignal,
        artifact_type: str,
        artifact_id: str,
        *,
        source_chat_id: str | None = None,
        source_message_id: str | None = None,
        source_note_id: str | None = None,
        action: str | None = None,
        status: FeedbackStatus | None = None,
        details: dict | None = None,
    ) -> EventLog:
        if source_chat_id is not None and source_message_id is not None and (
            not isinstance(source_chat_id, str)
            or not source_chat_id.strip()
            or not isinstance(source_message_id, str)
            or not source_message_id.strip()
        ):
            raise ValueError("source_chat_id and source_message_id must both be non-empty")
        if _safe_token(artifact_type, max_length=64) is None:
            raise ValueError("artifact_type must be a safe token up to 64 characters")
        if _safe_token(artifact_id, max_length=128) is None:
            raise ValueError("artifact_id must be a safe token up to 128 characters")
        if source_note_id is not None and _safe_token(source_note_id, max_length=128) is None:
            raise ValueError("source_note_id must be a safe token up to 128 characters")
        if action is not None and _safe_token(action, max_length=64) is None:
            raise ValueError("action must be a safe token up to 64 characters")
        feedback_status = status or (
            FeedbackStatus.OPEN
            if signal == FeedbackSignal.CORRECTION
            else FeedbackStatus.RESOLVED
        )
        payload = {
            "schema_version": 1,
            "metadata_policy_version": 1,
            "signal": signal.value,
            "status": feedback_status.value,
            "artifact": {"type": artifact_type, "id": artifact_id},
            "source_note_id": source_note_id,
        }
        if source_chat_id is not None and source_message_id is not None:
            payload["provenance"] = "telegram_owner_private"
        if action:
            payload["action"] = action
        safe_details = sanitize_feedback_details(details)
        if safe_details:
            payload["details"] = safe_details
        if not valid_feedback_payload(payload):
            raise ValueError("feedback payload does not match metadata policy version 1")
        return await self.append_event(
            EventType.USER_FEEDBACK_RECORDED,
            artifact_type,
            artifact_id,
            payload,
        )
    async def resolve_feedback(self, feedback_event_id: str) -> EventLog | None:
        feedback = await self.get_event(feedback_event_id)
        if (
            feedback is None
            or feedback.event_type != EventType.USER_FEEDBACK_RECORDED
            or feedback.payload.get("schema_version") != 1
        ):
            return None
        resolved = await self.list_recent(
            EventType.USER_FEEDBACK_RESOLVED,
            limit=500,
        )
        if any(
            event.payload.get("feedback_event_id") == feedback_event_id
            for event in resolved
        ):
            return next(
                event
                for event in resolved
                if event.payload.get("feedback_event_id") == feedback_event_id
            )
        return await self.append_event(
            EventType.USER_FEEDBACK_RESOLVED,
            feedback.entity_type,
            feedback.entity_id,
            {
                "schema_version": 1,
                "feedback_event_id": feedback_event_id,
                "signal": feedback.payload.get("signal"),
                "status": FeedbackStatus.RESOLVED.value,
            },
        )


def _valid_owner_observation(
    event: EventLog,
    *,
    observation_day: str,
    display_timezone: tzinfo,
) -> bool:
    payload = event.payload
    return (
        event.event_type == EventType.TELEGRAM_OWNER_INTERACTION_OBSERVED
        and event.entity_type == "review_window_day"
        and event.entity_id == observation_day
        and set(payload)
        == {
            "schema_version",
            "metadata_policy_version",
            "provenance",
            "interaction_kind",
            "observation_day",
        }
        and payload.get("schema_version") == 1
        and payload.get("metadata_policy_version") == 1
        and payload.get("provenance") == "telegram_owner_private"
        and payload.get("interaction_kind") in {"command", "message", "callback"}
        and payload.get("observation_day") == observation_day
        and event.created_at.astimezone(display_timezone).date().isoformat()
        == observation_day
    )
