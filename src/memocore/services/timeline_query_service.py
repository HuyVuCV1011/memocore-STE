from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
import re

from memocore.adapters.storage.knowledge_repositories import DecisionRepository
from memocore.adapters.storage.repositories import (
    CommitmentRepository,
    EventLogRepository,
    FollowUpRepository,
    MeetingRepository,
    MemoryItemRepository,
    NoteRepository,
    PersonRepository,
    ProjectRepository,
    ReminderRepository,
    TaskRepository,
    normalize_lookup,
)
from memocore.domain.knowledge import Decision
from memocore.domain.models import (
    Commitment,
    EventLog,
    FollowUp,
    Meeting,
    MemoryItem,
    Note,
    Project,
    Reminder,
    Task,
)


@dataclass(frozen=True)
class TimelineEntry:
    happened_at: datetime
    artifact_type: str
    title: str
    status: str | None = None
    source_note_id: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    event_type: str | None = None
    reason: str | None = None


class TimelineQueryService:
    """Cross-domain read-only search and source history projection."""

    def __init__(
        self,
        note_repo: NoteRepository,
        task_repo: TaskRepository,
        reminder_repo: ReminderRepository,
        project_repo: ProjectRepository,
        person_repo: PersonRepository,
        meeting_repo: MeetingRepository,
        followup_repo: FollowUpRepository,
        commitment_repo: CommitmentRepository,
        memory_repo: MemoryItemRepository,
        event_repo: EventLogRepository,
        decision_repo: DecisionRepository | None = None,
        *,
        display_timezone: tzinfo = UTC,
    ) -> None:
        self.note_repo = note_repo
        self.task_repo = task_repo
        self.reminder_repo = reminder_repo
        self.project_repo = project_repo
        self.person_repo = person_repo
        self.meeting_repo = meeting_repo
        self.followup_repo = followup_repo
        self.commitment_repo = commitment_repo
        self.memory_repo = memory_repo
        self.event_repo = event_repo
        self.decision_repo = decision_repo
        self.display_timezone = display_timezone

    async def answer(self, query: str, *, limit: int = 8) -> str:
        normalized = normalize_lookup(query)
        if not normalized:
            return "Anh muốn tìm gì? Ví dụ: /search MemoCore tuần trước."
        entries = await self.search(query, limit=limit)
        if not entries:
            return f"Dạ, em chưa tìm thấy dấu vết rõ cho “{query}”."
        lines = [f"Tìm thấy {len(entries)} dấu vết liên quan đến “{query}”:"]
        note_map = await self._note_map(entries)
        for entry in entries:
            lines.append(f"- {_date_label(entry.happened_at, self.display_timezone)} · {entry.title}")
            detail = self._entry_detail(entry, note_map.get(entry.source_note_id or ""))
            if detail:
                lines.append(f"  {detail}")
        return "\n".join(lines)

    async def timeline(self, query: str, *, limit: int = 10) -> str:
        entries = await self.search(query, limit=limit)
        if not entries:
            return f"Dạ, em chưa có timeline đủ rõ cho “{query}”."
        lines = [f"Timeline cho “{query}”:"]
        note_map = await self._note_map(entries)
        for entry in entries:
            lines.append(f"- {_date_label(entry.happened_at, self.display_timezone)} · {entry.title}")
            detail = self._entry_detail(entry, note_map.get(entry.source_note_id or ""))
            if detail:
                lines.append(f"  {detail}")
        return "\n".join(lines)

    async def why(self, query: str) -> str:
        entries = await self.search(query, limit=5)
        primary = next(
            (entry for entry in entries if entry.artifact_type != "event"),
            entries[0] if entries else None,
        )
        if primary is None:
            return f"Dạ, em chưa tìm thấy nguồn tạo ra “{query}”."
        lines = [f"Vì sao có “{primary.title}”?"]
        if primary.source_note_id:
            note = await self.note_repo.get_by_id(primary.source_note_id)
            if note:
                lines.append(f"- Nguồn gốc: {_source_label(note, self.display_timezone)}.")
                quote = _compact(note.summary or note.raw_text, 140)
                if quote:
                    lines.append(f"- Nội dung nguồn: “{quote}”.")
        related_events = await self.event_repo.list_by_entity(
            primary.artifact_type, primary.entity_id or ""
        )
        if related_events:
            lines.append("- Chuỗi thao tác:")
            for event in related_events[-4:]:
                lines.append(
                    f"  - {_date_label(event.created_at, self.display_timezone)} · {_event_label(event.event_type.value)}"
                )
        if len(lines) == 1:
            lines.append("- Em tìm thấy artifact này, nhưng chưa có source/event đủ rõ để giải thích.")
        return "\n".join(lines)

    async def decisions(self, query: str, *, limit: int = 8) -> str:
        entries = [
            entry
            for entry in await self.search(query, limit=limit * 2)
            if entry.artifact_type == "decision"
        ][:limit]
        if not entries:
            return f"Dạ, em chưa thấy quyết định nào khớp “{query}”."
        lines = [f"Quyết định liên quan đến “{query}”:"]
        note_map = await self._note_map(entries)
        for entry in entries:
            lines.append(f"- {_date_label(entry.happened_at, self.display_timezone)} · {entry.title}")
            detail = self._entry_detail(entry, note_map.get(entry.source_note_id or ""))
            if detail:
                lines.append(f"  {detail}")
        return "\n".join(lines)

    async def search(self, query: str, *, limit: int = 10) -> list[TimelineEntry]:
        normalized = normalize_lookup(_strip_query_words(query))
        tokens = [token for token in normalized.split() if len(token) > 1]
        since = _since_hint(query)
        entries = await self._entries()
        scored: list[tuple[int, TimelineEntry]] = []
        for entry in entries:
            if since and entry.happened_at < since:
                continue
            haystack = normalize_lookup(" ".join(self._entry_text(entry)))
            score = _score(tokens, haystack)
            if score > 0 or not tokens:
                scored.append((score, entry))
        scored.sort(key=lambda item: (item[0], item[1].happened_at), reverse=True)
        return [entry for _, entry in scored[:limit]]

    async def _entries(self) -> list[TimelineEntry]:
        entries: list[TimelineEntry] = []
        entries.extend(self._task_entry(item) for item in await self.task_repo.list_all())
        entries.extend(self._reminder_entry(item) for item in await self.reminder_repo.list_all())
        entries.extend(self._meeting_entry(item) for item in await self.meeting_repo.list_all())
        entries.extend(self._followup_entry(item) for item in await self.followup_repo.list_all())
        entries.extend(self._commitment_entry(item) for item in await self.commitment_repo.list_all())
        entries.extend(self._memory_entry(item) for item in await self.memory_repo.list_all())
        entries.extend(self._project_entry(item) for item in await self.project_repo.list_all())
        entries.extend(self._note_entry(item) for item in await self.note_repo.list_recent(limit=200))
        entries.extend(self._event_entry(item) for item in await self.event_repo.list_recent(limit=200))
        if self.decision_repo is not None:
            entries.extend(self._decision_entry(item) for item in await self.decision_repo.list_all())
        return entries

    def _entry_text(self, entry: TimelineEntry) -> list[str]:
        return [
            entry.artifact_type,
            entry.title,
            entry.status or "",
            entry.event_type or "",
            entry.reason or "",
        ]

    async def _note_map(self, entries: list[TimelineEntry]) -> dict[str, Note]:
        result: dict[str, Note] = {}
        for note_id in {entry.source_note_id for entry in entries if entry.source_note_id}:
            note = await self.note_repo.get_by_id(note_id)
            if note is not None:
                result[note_id] = note
        return result

    def _entry_detail(self, entry: TimelineEntry, note: Note | None) -> str:
        parts = [_artifact_label(entry.artifact_type)]
        if entry.status:
            parts.append(f"trạng thái {entry.status}")
        if entry.reason:
            parts.append(entry.reason)
        if note is not None:
            parts.append(f"nguồn: {_source_label(note, self.display_timezone)}")
        elif entry.event_type:
            parts.append(f"nguồn: {_event_label(entry.event_type)}")
        return "; ".join(parts)

    def _task_entry(self, item: Task) -> TimelineEntry:
        return TimelineEntry(
            happened_at=item.updated_at or item.created_at,
            artifact_type="task",
            title=item.title,
            status=str(item.status),
            source_note_id=item.source_note_id,
            entity_type="task",
            entity_id=item.id,
            reason="deadline " + _date_label(item.due_at, self.display_timezone) if item.due_at else None,
        )

    def _reminder_entry(self, item: Reminder) -> TimelineEntry:
        return TimelineEntry(
            happened_at=item.updated_at or item.created_at,
            artifact_type="reminder",
            title=item.title,
            status=str(item.status),
            source_note_id=item.source_note_id,
            entity_type="reminder",
            entity_id=item.id,
            reason="nhắc lúc " + _date_label(item.remind_at, self.display_timezone) if item.remind_at else None,
        )

    def _meeting_entry(self, item: Meeting) -> TimelineEntry:
        return TimelineEntry(
            happened_at=item.starts_at or item.updated_at or item.created_at,
            artifact_type="meeting",
            title=item.title,
            status=None,
            source_note_id=item.source_note_id,
            entity_type="meeting",
            entity_id=item.id,
        )

    def _followup_entry(self, item: FollowUp) -> TimelineEntry:
        return TimelineEntry(
            happened_at=item.updated_at or item.created_at,
            artifact_type="followup",
            title=item.title,
            status=str(item.status),
            source_note_id=item.source_note_id,
            entity_type="followup",
            entity_id=item.id,
        )

    def _commitment_entry(self, item: Commitment) -> TimelineEntry:
        return TimelineEntry(
            happened_at=item.updated_at or item.created_at,
            artifact_type="commitment",
            title=item.title,
            status=str(item.status),
            source_note_id=item.source_note_id,
            entity_type="commitment",
            entity_id=item.id,
        )

    def _memory_entry(self, item: MemoryItem) -> TimelineEntry:
        return TimelineEntry(
            happened_at=item.observed_at or item.updated_at or item.created_at,
            artifact_type="memory",
            title=item.content,
            status=str(item.status),
            source_note_id=item.source_note_id,
            entity_type="memory_item",
            entity_id=item.id,
        )

    def _project_entry(self, item: Project) -> TimelineEntry:
        return TimelineEntry(
            happened_at=item.last_seen_at or item.updated_at or item.created_at,
            artifact_type="project",
            title=item.name,
            status=str(item.status),
            entity_type="project",
            entity_id=item.id,
        )

    def _note_entry(self, item: Note) -> TimelineEntry:
        return TimelineEntry(
            happened_at=item.created_at,
            artifact_type="note",
            title=_compact(item.summary or item.raw_text, 120),
            status=str(item.status),
            source_note_id=item.id,
            entity_type="note",
            entity_id=item.id,
        )

    def _event_entry(self, item: EventLog) -> TimelineEntry:
        return TimelineEntry(
            happened_at=item.created_at,
            artifact_type="event",
            title=_event_label(item.event_type.value),
            event_type=item.event_type.value,
            entity_type=item.entity_type,
            entity_id=item.entity_id,
            reason=_compact(" ".join(str(value) for value in item.payload.values()), 120),
        )

    def _decision_entry(self, item: Decision) -> TimelineEntry:
        return TimelineEntry(
            happened_at=item.decided_at,
            artifact_type="decision",
            title=item.title if not item.summary else f"{item.title}: {_compact(item.summary, 90)}",
            status=item.status.value,
            source_note_id=item.source_note_id,
            entity_type="decision",
            entity_id=item.id,
        )


def _score(tokens: list[str], haystack: str) -> int:
    if not tokens:
        return 1
    score = 0
    for token in tokens:
        if token in haystack:
            score += 2
    if " ".join(tokens) in haystack:
        score += 4
    return score


def _strip_query_words(query: str) -> str:
    normalized = normalize_lookup(query)
    stop = {
        "tim", "kiem", "search", "timeline", "lich", "su", "vi", "sao",
        "tai", "nguon", "goc", "quyet", "dinh", "lan", "gan", "nhat",
        "khi", "nao", "da", "duoc", "tao", "ve", "lien", "quan", "cho", "toi",
    }
    return " ".join(token for token in normalized.split() if token not in stop)


def _since_hint(query: str) -> datetime | None:
    normalized = normalize_lookup(query)
    now = datetime.now(UTC)
    if "tuan truoc" in normalized:
        return now - timedelta(days=14)
    if "thang nay" in normalized:
        return now - timedelta(days=31)
    if "thang truoc" in normalized:
        return now - timedelta(days=62)
    match = re.search(r"(\d+)\s*ngay", normalized)
    if match:
        return now - timedelta(days=int(match.group(1)))
    return None


def _source_label(note: Note, display_timezone: tzinfo) -> str:
    source = "tin nhắn Telegram" if note.source == "telegram" else note.source
    return f"{source} ngày {_date_label(note.created_at, display_timezone)}"


def _date_label(value: datetime | None, display_timezone: tzinfo) -> str:
    if value is None:
        return "chưa rõ ngày"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    local = value.astimezone(display_timezone)
    if local.hour == 0 and local.minute == 0:
        return f"{local:%d/%m/%Y}"
    return f"{local:%d/%m/%Y %H:%M}"


def _artifact_label(value: str) -> str:
    return {
        "task": "task",
        "reminder": "nhắc nhở",
        "meeting": "meeting",
        "followup": "follow-up",
        "commitment": "commitment",
        "memory": "ghi nhớ",
        "project": "project",
        "note": "ghi chú nguồn",
        "event": "sự kiện audit",
        "decision": "quyết định",
    }.get(value, value)


def _event_label(value: str) -> str:
    return {
        "task_candidate_created": "task được tạo",
        "task_done": "task được hoàn thành",
        "task_batch_completed": "nhiều task được hoàn thành",
        "work_item_changed": "thay đổi task",
        "work_item_undone": "hoàn tác thay đổi",
        "reminder_scheduled": "nhắc nhở được lên lịch",
        "reminder_sent": "nhắc nhở đã gửi",
        "memory_candidate_created": "ghi nhớ được đề xuất",
        "memory_activated": "ghi nhớ được xác nhận",
        "memory_rejected": "ghi nhớ bị loại",
        "meeting_created": "meeting được tạo",
        "followup_created": "follow-up được tạo",
        "commitment_created": "commitment được tạo",
        "decision_superseded": "quyết định được thay thế",
        "user_feedback_recorded": "người dùng ghi nhận phản hồi",
    }.get(value, value.replace("_", " "))


def _compact(value: str, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
