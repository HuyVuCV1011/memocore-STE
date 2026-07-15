from __future__ import annotations

from datetime import datetime, time, timedelta
import re
import unicodedata

from memocore.domain.schemas import NoteExtraction, TaskCandidate


def normalize_scheduled_work(
    extraction: NoteExtraction,
    raw_text: str,
    now: datetime,
) -> NoteExtraction:
    """Normalize high-risk schedule semantics before durable persistence."""
    recurring = _recurring_schedule_spec(raw_text, now)
    if recurring is not None:
        title, due_at, rule, duration_minutes = recurring
        existing = extraction.tasks[0] if extraction.tasks else None
        extraction.tasks = [
            TaskCandidate(
                title=title,
                description=existing.description if existing else "",
                priority=existing.priority if existing else "medium",
                due_at=due_at.isoformat(),
                person_name=existing.person_name if existing else None,
                project_name=existing.project_name if existing else None,
                recurrence_rule=rule,
                duration_minutes=duration_minutes,
                confidence=max(existing.confidence if existing else 0.0, 0.95),
            )
        ]
        extraction.memories = []

    scheduled = _scheduled_task_specs(raw_text, now)
    if not scheduled:
        explicit_due, explicit_duration = scheduled_datetime(raw_text, now)
        if explicit_due is not None:
            for task in extraction.tasks:
                task.due_at = explicit_due.isoformat()
                if explicit_duration is not None:
                    task.duration_minutes = explicit_duration
            for meeting in extraction.meetings:
                meeting.starts_at = explicit_due.isoformat()
                if explicit_duration is not None:
                    meeting.ends_at = (
                        explicit_due + timedelta(minutes=explicit_duration)
                    ).isoformat()

    if scheduled:
        normalized_tasks: list[TaskCandidate] = []
        for index, (title, due_at, duration_minutes) in enumerate(scheduled):
            existing = extraction.tasks[index] if index < len(extraction.tasks) else None
            normalized_tasks.append(
                TaskCandidate(
                    title=(
                        existing.title
                        if existing and existing.title.strip()
                        else title
                    ),
                    description=existing.description if existing else "",
                    priority=existing.priority if existing else "medium",
                    due_at=due_at.isoformat(),
                    person_name=existing.person_name if existing else None,
                    project_name=existing.project_name if existing else None,
                    recurrence_rule=existing.recurrence_rule if existing else None,
                    duration_minutes=duration_minutes,
                    confidence=max(existing.confidence if existing else 0.0, 0.95),
                )
            )
        extraction.tasks = normalized_tasks
        extraction.memories = []

    # One outing at one time with one person is one user plan, not one task per verb.
    if len(extraction.meetings) == 1 and len(extraction.tasks) > 1:
        people = {task.person_name for task in extraction.tasks}
        due_values = {task.due_at for task in extraction.tasks}
        if len(people) == 1 and len(due_values) == 1:
            first = extraction.tasks[0]
            combined_title = " và ".join(
                task.title.strip() for task in extraction.tasks
            )
            extraction.tasks = [
                first.model_copy(
                    update={
                        "title": combined_title,
                        "confidence": max(
                            task.confidence for task in extraction.tasks
                        ),
                    }
                )
            ]
            extraction.memories = [
                memory
                for memory in extraction.memories
                if not _is_operational_restatement(memory.content, combined_title)
            ]
    return extraction


def scheduled_datetime(
    text: str,
    now: datetime,
) -> tuple[datetime | None, int | None]:
    normalized = _normalize(text)
    if "ngay mot" in normalized or "day after tomorrow" in normalized:
        day_offset = 2
    elif "ngay mai" in normalized or re.search(r"\bmai\b", normalized):
        day_offset = 1
    elif "hom nay" in normalized or "toi nay" in normalized:
        day_offset = 0
    else:
        weekday = _explicit_weekday(normalized)
        if weekday is None:
            return None, None
        day_offset = (weekday - now.weekday()) % 7

    clock, duration_minutes = time_and_duration(normalized)
    if clock is None:
        if "toi" in normalized:
            # A completion phrase uses evening as a deadline; an outing/meeting
            # uses evening as a start time.
            clock = time(23, 59) if "hoan thanh" in normalized else time(18, 0)
        elif "sang" in normalized:
            clock = time(9, 0)
        elif "chieu" in normalized:
            clock = time(14, 0)
        else:
            return None, None
    local = datetime.combine(
        now.date() + timedelta(days=day_offset), clock, tzinfo=now.tzinfo
    )
    return local, duration_minutes


def time_and_duration(normalized: str) -> tuple[time | None, int | None]:
    clocks = list(
        re.finditer(
            r"\b(\d{1,2})(?:h|:)(\d{0,2})\b\s*(sang|chieu|toi|am|pm)?",
            normalized,
        )
    )
    if not clocks:
        return None, None

    def convert(match) -> time | None:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        period = match.group(3) or ""
        if period in {"chieu", "toi", "pm"} and hour < 12:
            hour += 12
        if hour > 23 or minute > 59:
            return None
        return time(hour, minute)

    start = convert(clocks[0])
    if start is None or len(clocks) == 1:
        return start, None
    end = convert(clocks[1])
    if end is None:
        return start, None
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    if end_minutes <= start_minutes:
        end_minutes += 24 * 60
    return start, end_minutes - start_minutes


def _scheduled_task_specs(
    raw_text: str,
    now: datetime,
) -> list[tuple[str, datetime, int | None]]:
    normalized = _normalize(raw_text)
    if not is_future_schedule_request(normalized):
        return []
    bullet_lines = [
        re.sub(r"^\s*[-*•]\s*", "", line).strip()
        for line in raw_text.splitlines()
        if re.match(r"^\s*[-*•]\s*\S", line)
    ]
    if bullet_lines:
        items = bullet_lines
    else:
        payload = re.sub(
            r"^\s*(?:đặt|dat|lên|len)\s+(?:lịch|lich)"
            r"(?:\s+cho\s+(?:tôi|toi))?\s*[,,:-]?\s*",
            "",
            raw_text,
            flags=re.IGNORECASE,
        ).strip()
        items = [payload] if payload else []

    result: list[tuple[str, datetime, int | None]] = []
    for item in items:
        due_at, duration_minutes = scheduled_datetime(item, now)
        if due_at is None:
            continue
        title = re.sub(
            r"^\s*(?:(?:tối|toi|sáng|sang|chiều|chieu|trưa|trua)\s+)?"
            r"(?:(?:hôm\s+nay|hom\s+nay|ngày\s+mốt|ngay\s+mot|"
            r"ngày\s+mai|ngay\s+mai|mai)\s+)?",
            "",
            item,
            flags=re.IGNORECASE,
        ).strip(" ,.-")
        if title:
            result.append((title, due_at, duration_minutes))
    return result


def _recurring_schedule_spec(
    raw_text: str,
    now: datetime,
) -> tuple[str, datetime, str, int | None] | None:
    normalized = _normalize(raw_text)
    if not is_future_schedule_request(normalized):
        return None
    recurrence_rule = _recurrence_rule(normalized)
    if recurrence_rule is None:
        return None
    clock, duration_minutes = time_and_duration(normalized)
    if clock is None:
        return None
    due_at = datetime.combine(now.date(), clock, tzinfo=now.tzinfo)
    if due_at <= now:
        due_at += timedelta(days=1)
    title_match = re.search(
        r"(?:đặt|dat)(?:\s+cho\s+(?:tôi|toi))?\s+(?:lịch|lich)\s+(.+?)"
        r"(?:\s+định\s+kỳ|\s+dinh\s+ky|\s+như\s+sau|\s+nhu\s+sau|,|"
        r"\s+vào\s+|\s+vao\s+|\s+lúc\s+|\s+luc\s+)",
        raw_text,
        flags=re.IGNORECASE,
    )
    title = (
        title_match.group(1).strip(" ,.-")
        if title_match
        else "Lịch định kỳ"
    )
    return title, due_at, recurrence_rule, duration_minutes


def _recurrence_rule(normalized: str) -> str | None:
    interval = re.search(
        r"\b(?:moi|every)\s+(\d+)\s+(ngay|day|days|tuan|week|weeks)\b",
        normalized,
    )
    if interval is not None:
        count = int(interval.group(1))
        unit = interval.group(2)
        if count >= 1:
            return f"interval:{count}{'w' if unit in {'tuan', 'week', 'weeks'} else 'd'}"
    if any(
        signal in normalized
        for signal in ("moi ngay", "hang ngay", "daily", "every day")
    ):
        return "daily"
    weekday = _explicit_weekday(normalized)
    if weekday is not None and any(
        signal in normalized for signal in ("moi", "hang", "every", "weekly")
    ):
        return f"weekly:{weekday}"
    if any(
        signal in normalized
        for signal in ("moi tuan", "hang tuan", "weekly", "every week")
    ):
        return "weekly"
    return None


def _explicit_weekday(normalized: str) -> int | None:
    weekdays = {
        "thu 2": 0, "thu hai": 0, "monday": 0,
        "thu 3": 1, "thu ba": 1, "tuesday": 1,
        "thu 4": 2, "thu tu": 2, "wednesday": 2,
        "thu 5": 3, "thu nam": 3, "thursday": 3,
        "thu 6": 4, "thu sau": 4, "friday": 4,
        "thu 7": 5, "thu bay": 5, "saturday": 5,
        "chu nhat": 6, "sunday": 6,
    }
    return next(
        (value for label, value in weekdays.items() if label in normalized),
        None,
    )


def is_future_schedule_request(normalized: str) -> bool:
    has_schedule = any(
        signal in normalized
        for signal in (
            "dat lich", "dat cho toi lich", "len lich", "schedule",
            "dat cho toi cac lich",
        )
    )
    has_future_time = any(
        signal in normalized
        for signal in (
            "hom nay", "toi nay", "ngay mai", "toi mai", "ngay mot",
            "tuan nay", "tuan sau", "tomorrow", "tonight", "next week",
        )
    )
    return has_schedule or (has_future_time and "hoan thanh" in normalized)


def _is_operational_restatement(memory: str, task_title: str) -> bool:
    memory_tokens = set(_normalize(memory).split())
    task_tokens = set(_normalize(task_title).split())
    if not memory_tokens or not task_tokens:
        return False
    overlap = memory_tokens & task_tokens
    return len(overlap) >= 3 and len(overlap) / len(task_tokens) >= 0.55


def _normalize(value: str) -> str:
    lowered = value.casefold().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    ascii_text = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    return " ".join(
        "".join(char if char.isalnum() else " " for char in ascii_text).split()
    )
