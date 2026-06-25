from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Any

from memocore.services.conversation_frame import ConversationFrame


@dataclass(frozen=True)
class ConversationPlan:
    intent: str
    goal: str | None = None
    statements: tuple[str, ...] = ()
    requires_entity: bool = False
    requested_count: int | None = None
    target_entity_ids: tuple[str, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    reason: str = ""


class ConversationPlanner:
    """Planning boundary for multi-turn, entity-scoped conversation actions."""

    def plan(
        self, raw_text: str, frame: ConversationFrame | None = None
    ) -> ConversationPlan | None:
        normalized = _normalize(raw_text)
        rollback_count = _knowledge_rollback_count(normalized)
        if rollback_count is not None:
            return ConversationPlan(
                intent="rollback_knowledge_update",
                goal="undo_recent_knowledge_write",
                requested_count=rollback_count,
                reason="The user explicitly asked to undo a recent knowledge batch.",
            )
        if _is_generic_undo(normalized):
            target_ids = tuple(frame.last_result_entity_ids) if frame else ()
            return ConversationPlan(
                intent="undo_last_action",
                goal="undo_previous_operation",
                target_entity_ids=target_ids,
                payload={"previous_intent": frame.last_intent if frame else None},
                confidence=1.0 if target_ids else 0.5,
                reason="The user explicitly asked to undo the immediately previous action.",
            )
        merge_queries = parse_task_merge_request(raw_text)
        if merge_queries is not None or _asks_to_merge_recent_tasks(normalized):
            target_ids = _recent_merge_targets(frame)
            return ConversationPlan(
                intent="merge_tasks",
                goal="correct_previous_task_split",
                target_entity_ids=target_ids,
                payload={"task_queries": list(merge_queries or ())},
                confidence=1.0 if merge_queries or len(target_ids) == 2 else 0.6,
                reason=(
                    "The user says multiple task artifacts represent one real-world task."
                ),
            )
        if is_future_task_capture(normalized):
            return ConversationPlan(
                intent="capture_task",
                goal="schedule_future_work",
                reason=(
                    "Future scheduling language overrides completion keywords such as "
                    "'hoàn thành'."
                ),
            )
        if is_daily_schedule_query(normalized):
            return ConversationPlan(
                intent="query_task_recurrence",
                goal="view_recurring_schedule",
                reason="The user is asking for recurring work, not entity context.",
            )
        if _is_knowledge_update(normalized):
            return ConversationPlan(
                intent="update_knowledge",
                goal="update_scoped_knowledge",
                statements=tuple(_extract_statements(raw_text)),
                requires_entity=True,
                reason="Explicit entity-scoped durable knowledge update.",
            )
        if _is_entity_overview_query(normalized):
            return ConversationPlan(
                intent="query_context",
                goal="view_entity_context",
                requires_entity=True,
                reason="Explicit request for an entity overview.",
            )
        return None


def parse_task_merge_request(text: str) -> tuple[str, str] | None:
    normalized = _normalize(text)
    patterns = (
        r"^(?:gop\s+)?(.+?)\s+va\s+(.+?)\s+(?:la|thanh)\s+(?:mot|1)\s+(?:task|viec)\s+chung",
        r"^gop\s+(?:task\s+|viec\s+)?(.+?)\s+va\s+(?:task\s+|viec\s+)?(.+?)(?:\s+thanh\s+(?:mot|1)\s+(?:task|viec))?$",
        r"^dung\s+tach\s+(.+?)\s+va\s+(.+?)(?:\s+nua)?$",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            left, right = (part.strip() for part in match.groups())
            if left and right:
                return left, right
    return None


def is_future_task_capture(normalized: str) -> bool:
    if any(
        signal in normalized
        for signal in (
            "dat lich",
            "dat cho toi lich",
            "dat cho toi cac lich",
            "len lich",
            "schedule",
        )
    ):
        return True
    if _looks_like_schedule_question(normalized):
        return False
    has_future = any(
        signal in normalized
        for signal in (
            "toi nay",
            "toi mai",
            "ngay mai",
            "ngay mot",
            "tuan nay",
            "tuan sau",
            "tonight",
            "tomorrow",
            "day after tomorrow",
        )
    )
    return has_future and any(
        signal in normalized for signal in ("hoan thanh", "can lam", "se lam")
    )


def is_daily_schedule_query(normalized: str) -> bool:
    asks_schedule = any(
        signal in normalized
        for signal in (
            "lich hang ngay",
            "lich hang tuan",
            "lich dinh ky",
            "daily schedule",
        )
    )
    return asks_schedule and any(
        signal in normalized for signal in ("cua toi", "la gi", "show", "xem")
    )


def _looks_like_schedule_question(normalized: str) -> bool:
    asks_day = any(
        signal in normalized
        for signal in (
            "hom nay toi can lam gi",
            "ngay mai toi can lam gi",
            "mai toi can lam gi",
            "what do i need to do",
        )
    )
    return asks_day or normalized.endswith(" gi")


def _asks_to_merge_recent_tasks(normalized: str) -> bool:
    has_merge = any(
        signal in normalized
        for signal in (
            "gop lai",
            "mot task chung",
            "mot viec chung",
            "la mot task",
            "la mot viec",
            "dung tach",
        )
    )
    has_reference = any(
        signal in normalized
        for signal in (
            "hai task",
            "hai viec",
            "2 task",
            "2 viec",
            "hai cai",
            "vua tao",
            "vua roi",
            "do",
        )
    )
    return has_merge and has_reference


def _recent_merge_targets(frame: ConversationFrame | None) -> tuple[str, ...]:
    if frame is None:
        return ()
    active_ids = frame.active_task_ids
    result_ids = tuple(
        entity_id
        for entity_id in frame.last_result_entity_ids
        if entity_id in active_ids
    )
    if len(result_ids) == 2:
        return result_ids
    previous = frame.previous_turn
    if previous is not None:
        previous_ids = tuple(
            entity_id
            for entity_id in previous.result_entity_ids
            if entity_id in active_ids
        )
        if len(previous_ids) == 2:
            return previous_ids
    return ()


def is_bare_entity_reference(raw_text: str, entity_name: str | None) -> bool:
    return bool(entity_name) and _normalize(raw_text) == _normalize(entity_name)


def _is_knowledge_update(normalized: str) -> bool:
    signals = (
        "cap nhat them thong tin",
        "bo sung them thong tin",
        "bo sung thong tin",
        "them thong tin cho",
        "ghi them vao",
        "luu them vao",
        "cap nhat knowledge",
        "update thong tin",
        "update knowledge",
    )
    return any(signal in normalized for signal in signals)


def _knowledge_rollback_count(normalized: str) -> int | None:
    is_rollback = (
        (
            "hoan tac" in normalized
            and any(
                signal in normalized
                for signal in ("thong tin", "knowledge", "memory", "cap nhat")
            )
        )
        or (
            "xoa" in normalized
            and "thong tin" in normalized
            and any(signal in normalized for signal in ("vua cap nhat", "da cap nhat"))
        )
    )
    if not is_rollback:
        return None
    match = re.search(r"\bxoa\s+(\d+)\s+thong tin\b", normalized)
    return int(match.group(1)) if match else 0


def _is_generic_undo(normalized: str) -> bool:
    return any(
        signal in normalized
        for signal in (
            "hoan tac thay doi vua roi",
            "hoan tac viec vua roi",
            "undo last action",
            "undo vua roi",
            "quay lai nhu cu",
            "tra lai nhu cu",
        )
    )


def _is_entity_overview_query(normalized: str) -> bool:
    if any(
        signal in normalized
        for signal in (
            "con gi chua xong",
            "task dang mo",
            "viec dang mo",
            "dang can lam",
            "tien do",
            "deadline",
        )
    ):
        return False
    return any(
        signal in normalized
        for signal in (
            "noi cho toi biet ve du an",
            "cho toi biet ve du an",
            "gioi thieu du an",
            "noi ve du an",
            "tell me about project",
            "describe project",
        )
    ) or bool(re.fullmatch(r"du an .+ la gi", normalized))


def _extract_statements(raw_text: str) -> list[str]:
    lines = [line.strip() for line in raw_text.splitlines()]
    payload_lines: list[str] = []
    for index, line in enumerate(lines):
        cleaned = _strip_list_marker(line)
        if not cleaned:
            continue
        if index == 0 and _is_knowledge_update(_normalize(cleaned)):
            inline = _inline_payload(cleaned)
            if inline:
                payload_lines.append(inline)
            continue
        payload_lines.append(cleaned)
    return _deduplicate(payload_lines)


def _inline_payload(line: str) -> str:
    if ":" in line:
        return line.split(":", 1)[1].strip()
    normalized_words = _normalize(line).split()
    original_words = line.split()
    for marker in ("nhu sau",):
        marker_words = marker.split()
        for index in range(len(normalized_words) - len(marker_words) + 1):
            if normalized_words[index : index + len(marker_words)] == marker_words:
                payload = " ".join(
                    original_words[index + len(marker_words) :]
                ).strip(" :-")
                if _normalize(payload) in {"nhe", "nha", "a", "voi"}:
                    return ""
                return payload
    return ""


def _strip_list_marker(value: str) -> str:
    return re.sub(r"^\s*(?:[-*•]+|\d+[.)])\s*", "", value).strip()


def _deduplicate(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = _normalize(value)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _normalize(value: str) -> str:
    lowered = value.casefold().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    ascii_text = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    return " ".join(
        "".join(char if char.isalnum() else " " for char in ascii_text).split()
    )
