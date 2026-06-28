from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
from enum import StrEnum
import re
import unicodedata

from memocore.adapters.storage.repositories import (
    TaskListContext,
    TaskListContextRepository,
    TaskRepository,
)
from memocore.domain.models import Task


class TaskSelectionMode(StrEnum):
    NONE = "none"
    SINGLE = "single"
    MULTIPLE = "multiple"
    AMBIGUOUS = "ambiguous"


class TaskSelectionSource(StrEnum):
    NUMBER = "number"
    LISTED_CONTEXT = "listed_context"
    TIME_SCOPE = "time_scope"
    TITLE_MATCH = "title_match"


@dataclass(frozen=True)
class ResolvedTaskSelection:
    tasks: tuple[Task, ...] = ()
    mode: TaskSelectionMode = TaskSelectionMode.NONE
    source: TaskSelectionSource | None = None
    source_view: str | None = None
    requires_confirmation: bool = False
    candidate_count: int = 0
    context_age_seconds: int | None = None
    resolution_reason: str = "no_match"

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(task.id for task in self.tasks)


class TaskReferenceResolver:
    """Resolve task language into one explicit mutation target set."""

    def __init__(
        self,
        task_repo: TaskRepository,
        task_list_repo: TaskListContextRepository,
        *,
        display_timezone: tzinfo = UTC,
        context_ttl: timedelta = timedelta(hours=6),
        bulk_confirmation_threshold: int = 5,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.task_repo = task_repo
        self.task_list_repo = task_list_repo
        self.display_timezone = display_timezone
        self.context_ttl = context_ttl
        self.bulk_confirmation_threshold = bulk_confirmation_threshold
        self.now_provider = now_provider or (lambda: datetime.now(UTC))

    async def resolve(
        self,
        raw_text: str,
        source_chat_id: str | None,
        *,
        title_hint: str | None = None,
        force_confirmation: bool = False,
        now: datetime | None = None,
    ) -> ResolvedTaskSelection:
        now = now or self.now_provider()
        normalized = _normalize(raw_text)
        active = await self.task_repo.list_active()
        active_by_id = {task.id: task for task in active}
        context, context_tasks = await self._context_selection(
            source_chat_id, active_by_id, now=now
        )
        context_age_seconds = (
            max(0, int((now - context.updated_at).total_seconds()))
            if context is not None
            else None
        )

        count = _listed_task_count(normalized)
        if count is not None and 0 < count <= len(context_tasks):
            return _selection(
                context_tasks[:count],
                TaskSelectionSource.LISTED_CONTEXT,
                source_view=context.source_view if context else None,
                context_age_seconds=context_age_seconds,
                resolution_reason="explicit_list_count",
                requires_confirmation=(
                    force_confirmation or count > self.bulk_confirmation_threshold
                ),
            )

        number = _singular_number_reference(normalized)
        if number is not None and 1 <= number <= len(context_tasks):
            return _selection(
                [context_tasks[number - 1]],
                TaskSelectionSource.NUMBER,
                source_view=context.source_view if context else None,
                context_age_seconds=context_age_seconds,
                resolution_reason="number_reference",
                requires_confirmation=force_confirmation,
            )

        if _is_all_today(normalized):
            local_now = now.astimezone(self.display_timezone)
            day_end = datetime.combine(
                local_now.date(), time.max, tzinfo=self.display_timezone
            ).astimezone(UTC)
            context_is_today = (
                context is not None
                and context.source_view.removeprefix("query_") in {"today", "todays"}
            )
            tasks = (
                context_tasks
                if context_is_today
                else [
                    task
                    for task in active
                    if task.due_at is not None and task.due_at <= day_end
                ]
            )
            return _selection(
                tasks,
                TaskSelectionSource.TIME_SCOPE,
                source_view=context.source_view if context_is_today else None,
                context_age_seconds=context_age_seconds if context_is_today else None,
                resolution_reason=(
                    "recent_today_view" if context_is_today else "dynamic_today_scope"
                ),
                requires_confirmation=(
                    force_confirmation
                    or not context_is_today
                    or len(tasks) > self.bulk_confirmation_threshold
                ),
            )

        if _is_explicit_list_scope(normalized) and context_tasks:
            return _selection(
                context_tasks,
                TaskSelectionSource.LISTED_CONTEXT,
                source_view=context.source_view if context else None,
                context_age_seconds=context_age_seconds,
                resolution_reason="explicit_list_scope",
                requires_confirmation=(
                    force_confirmation
                    or len(context_tasks) > self.bulk_confirmation_threshold
                ),
            )

        if _is_vague_bulk_scope(normalized) and context_tasks:
            return _selection(
                context_tasks,
                TaskSelectionSource.LISTED_CONTEXT,
                source_view=context.source_view if context else None,
                context_age_seconds=context_age_seconds,
                resolution_reason="vague_list_scope",
                requires_confirmation=True,
            )

        query = _completion_query(_normalize(title_hint or normalized))
        matches = _ranked_matches(query, active)
        if not matches:
            return ResolvedTaskSelection()
        if len(matches) > 1:
            return ResolvedTaskSelection(
                tasks=tuple(matches),
                mode=TaskSelectionMode.AMBIGUOUS,
                source=TaskSelectionSource.TITLE_MATCH,
                requires_confirmation=True,
                candidate_count=len(matches),
                context_age_seconds=context_age_seconds,
                resolution_reason="ambiguous_title_match",
            )
        task = matches[0]
        return _selection(
            [task],
            TaskSelectionSource.TITLE_MATCH,
            context_age_seconds=context_age_seconds,
            resolution_reason="unique_title_match",
            requires_confirmation=(
                force_confirmation or not _is_strong_match(query, task.title)
            ),
        )

    async def _context_selection(
        self,
        source_chat_id: str | None,
        active_by_id: dict[str, Task],
        *,
        now: datetime,
    ) -> tuple[TaskListContext | None, list[Task]]:
        if not source_chat_id:
            return None, []
        context = await self.task_list_repo.get_context(
            source_chat_id,
            max_age=self.context_ttl,
            now=now,
        )
        if context is None:
            return None, []
        return context, [
            active_by_id[task_id]
            for task_id in context.task_ids
            if task_id in active_by_id
        ]


def _selection(
    tasks: list[Task],
    source: TaskSelectionSource,
    *,
    source_view: str | None = None,
    context_age_seconds: int | None = None,
    resolution_reason: str,
    requires_confirmation: bool,
) -> ResolvedTaskSelection:
    if not tasks:
        return ResolvedTaskSelection()
    return ResolvedTaskSelection(
        tasks=tuple(tasks),
        mode=(
            TaskSelectionMode.SINGLE
            if len(tasks) == 1
            else TaskSelectionMode.MULTIPLE
        ),
        source=source,
        source_view=source_view,
        requires_confirmation=requires_confirmation,
        candidate_count=len(tasks),
        context_age_seconds=context_age_seconds,
        resolution_reason=resolution_reason,
    )


def _singular_number_reference(normalized: str) -> int | None:
    match = re.search(
        r"\b(?:(?:task|viec|so)\s*(\d+)|cai\s+thu\s+(\d+))\b",
        normalized,
    )
    if match:
        return int(match.group(1) or match.group(2))
    if any(
        cue in normalized
        for cue in (
            "bo",
            "cancel",
            "doi",
            "hoan thanh",
            "huy",
            "lap",
            "priority",
            "sua",
            "xoa",
            "xong",
        )
    ):
        bare = re.search(r"\b(\d+)\b", normalized)
        if bare:
            return int(bare.group(1))
    return None


def _listed_task_count(normalized: str) -> int | None:
    patterns = (
        r"\b(?:xong|hoan thanh|done)\s+(\d+)\s+(?:task|viec)\s+(?:do|nay)\b",
        r"\b(\d+)\s+(?:task|viec)\s+(?:do|nay)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return int(match.group(1))
    return None


def _is_all_today(normalized: str) -> bool:
    return (
        any(scope in normalized for scope in ("hom nay", "today"))
        and _has_bulk_cue(normalized)
    )


def _is_explicit_list_scope(normalized: str) -> bool:
    return _has_bulk_cue(normalized) and re.search(
        r"\b(?:task|viec)\s+(?:do|nay)\b", normalized
    ) is not None


def _is_vague_bulk_scope(normalized: str) -> bool:
    return _has_bulk_cue(normalized) and any(
        word in normalized.split() for word in ("task", "viec")
    )


def _has_bulk_cue(normalized: str) -> bool:
    return any(
        cue in normalized
        for cue in ("het", "tat ca", "toan bo", "all", "every")
    )


def _completion_query(normalized: str) -> str:
    query = normalized
    for phrase in (
        "toi da lam xong",
        "da lam xong",
        "lam xong",
        "da hoan thanh",
        "hoan thanh",
        "danh dau",
        "da xong",
        "finished",
        "completed",
        "done",
        "xong",
        "task",
        "viec",
        "hom nay",
        "today",
    ):
        query = query.replace(phrase, " ")
    return " ".join(query.split())


def _ranked_matches(query: str, tasks: list[Task]) -> list[Task]:
    query_tokens = _task_name_tokens(query)
    if not query_tokens:
        return []
    scored: list[tuple[float, Task]] = []
    for task in tasks:
        title_tokens = _task_name_tokens(task.title)
        overlap = query_tokens & title_tokens
        score = len(overlap) / max(1, min(len(query_tokens), len(title_tokens)))
        if score >= 0.5 or (len(overlap) >= 2 and len(query_tokens) <= 3):
            scored.append((score, task))
    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.2:
        return [task for _, task in scored]
    return [scored[0][1]] if scored else []


def _is_strong_match(query: str, title: str) -> bool:
    query_tokens = _task_name_tokens(query)
    title_tokens = _task_name_tokens(title)
    return bool(query_tokens) and query_tokens.issubset(title_tokens)


def _task_name_tokens(value: str) -> set[str]:
    return _meaningful_tokens(value) - {
        "clock",
        "gio",
        "hom",
        "mai",
        "nay",
        "ngay",
        "today",
        "tomorrow",
    }


def _meaningful_tokens(value: str) -> set[str]:
    stopwords = {
        "a",
        "anh",
        "cua",
        "da",
        "em",
        "giup",
        "la",
        "mark",
        "toi",
    }
    return {
        token
        for token in _normalize(value).split()
        if len(token) > 1 and token not in stopwords
    }


def _normalize(value: str) -> str:
    lowered = value.casefold().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", lowered)
    ascii_text = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    return " ".join(
        "".join(char if char.isalnum() else " " for char in ascii_text).split()
    )
