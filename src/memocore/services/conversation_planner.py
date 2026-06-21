from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class ConversationPlan:
    intent: str
    statements: tuple[str, ...] = ()
    requires_entity: bool = False
    requested_count: int | None = None


class ConversationPlanner:
    """Planning boundary for multi-turn, entity-scoped conversation actions."""

    def plan(self, raw_text: str) -> ConversationPlan | None:
        normalized = _normalize(raw_text)
        rollback_count = _knowledge_rollback_count(normalized)
        if rollback_count is not None:
            return ConversationPlan(
                intent="rollback_knowledge_update",
                requested_count=rollback_count,
            )
        if _is_knowledge_update(normalized):
            return ConversationPlan(
                intent="update_knowledge",
                statements=tuple(_extract_statements(raw_text)),
                requires_entity=True,
            )
        if _is_entity_overview_query(normalized):
            return ConversationPlan(intent="query_context", requires_entity=True)
        return None


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
        "hoan tac" in normalized
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
