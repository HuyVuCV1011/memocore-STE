from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class NoteStatus(StrEnum):
    CAPTURED = "captured"
    PROCESSED = "processed"
    FAILED = "failed"


class TaskStatus(StrEnum):
    CANDIDATE = "candidate"
    OPEN = "open"
    WAITING = "waiting"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class ReminderStatus(StrEnum):
    CANDIDATE = "candidate"
    SCHEDULED = "scheduled"
    SENT = "sent"
    CANCELLED = "cancelled"
    FAILED = "failed"


class FollowUpStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


class CommitmentStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


class CommitmentDirection(StrEnum):
    USER_OWES = "user_owes"
    OWED_TO_USER = "owed_to_user"
    MUTUAL = "mutual"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class MemoryBucket(StrEnum):
    PROFILE = "profile"
    PROJECT = "project"
    INTERACTION = "interaction"


class MemoryKind(StrEnum):
    PREFERENCE = "preference"
    BOUNDARY = "boundary"
    FACT = "fact"
    CORRECTION = "correction"
    PROJECT_STATE = "project_state"


class MemoryStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class ClarificationStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class EventType(StrEnum):
    NOTE_CAPTURED = "note_captured"
    NOTE_PROCESSED = "note_processed"
    NOTE_FAILED = "note_failed"
    TASK_CANDIDATE_CREATED = "task_candidate_created"
    TASK_DONE = "task_done"
    REMINDER_CANDIDATE_CREATED = "reminder_candidate_created"
    REMINDER_SCHEDULED = "reminder_scheduled"
    REMINDER_SENT = "reminder_sent"
    REMINDER_FAILED = "reminder_failed"
    MEMORY_CANDIDATE_CREATED = "memory_candidate_created"
    PROJECT_SEEN = "project_seen"
    PERSON_CREATED = "person_created"
    MODEL_OUTPUT_INVALID = "model_output_invalid"
    EXTRACTION_LIKELY_INCOMPLETE = "extraction_likely_incomplete"
    FOLLOWUP_CREATED = "followup_created"
    FOLLOWUP_DONE = "followup_done"
    COMMITMENT_CREATED = "commitment_created"
    COMMITMENT_DONE = "commitment_done"
    MEETING_CREATED = "meeting_created"
    MEMORY_ACTIVATED = "memory_activated"
    MEMORY_SUPERSEDED = "memory_superseded"
    MEMORY_REJECTED = "memory_rejected"
    MEMORY_DELETED = "memory_deleted"
    CLARIFICATION_REQUESTED = "clarification_requested"
    CLARIFICATION_RESOLVED = "clarification_resolved"
    CLARIFICATION_FAILED = "clarification_failed"
    USER_FEEDBACK_RECORDED = "user_feedback_recorded"
    BRIEFING_SENT = "briefing_sent"
    WEEKLY_REVIEW_SENT = "weekly_review_sent"
    NUDGE_SENT = "nudge_sent"


class TimestampedModel(BaseModel):
    id: str = Field(default_factory=new_id)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Note(TimestampedModel):
    source: str = "telegram"
    source_message_id: str | None = None
    source_chat_id: str | None = None
    raw_text: str
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    status: NoteStatus = NoteStatus.CAPTURED
    metadata: dict[str, Any] = Field(default_factory=dict)


class Task(TimestampedModel):
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.CANDIDATE
    priority: str = "medium"
    due_at: datetime | None = None
    project_id: str | None = None
    person_id: str | None = None
    source_note_id: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Reminder(TimestampedModel):
    title: str
    remind_at: datetime | None = None
    status: ReminderStatus = ReminderStatus.CANDIDATE
    task_id: str | None = None
    source_note_id: str
    delivery_channel: str = "telegram"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    attempt_count: int = 0
    claimed_at: datetime | None = None
    recurrence_rule: str | None = None


class Project(TimestampedModel):
    name: str
    summary: str = ""
    status: ProjectStatus = ProjectStatus.ACTIVE
    tags: list[str] = Field(default_factory=list)
    last_seen_at: datetime = Field(default_factory=utc_now)


class Person(TimestampedModel):
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    relationship: str = ""
    notes: str = ""


class Meeting(TimestampedModel):
    title: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    project_id: str | None = None
    person_id: str | None = None
    source_note_id: str
    notes: str = ""


class FollowUp(TimestampedModel):
    title: str
    due_at: datetime | None = None
    status: FollowUpStatus = FollowUpStatus.OPEN
    person_id: str | None = None
    project_id: str | None = None
    source_note_id: str | None = None
    notes: str = ""


class Commitment(TimestampedModel):
    title: str
    direction: CommitmentDirection = CommitmentDirection.USER_OWES
    status: CommitmentStatus = CommitmentStatus.OPEN
    person_id: str | None = None
    project_id: str | None = None
    due_at: datetime | None = None
    source_note_id: str | None = None
    notes: str = ""


class MemoryItem(TimestampedModel):
    bucket: MemoryBucket
    kind: MemoryKind
    content: str
    source_note_id: str
    project_id: str | None = None
    person_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: MemoryStatus = MemoryStatus.CANDIDATE


class ClarificationRequest(TimestampedModel):
    source_chat_id: str
    source_message_id: str | None = None
    entity_type: str
    entity_id: str
    field_name: str
    question: str
    status: ClarificationStatus = ClarificationStatus.PENDING
    answer_text: str | None = None
    resolved_at: datetime | None = None


class EventLog(BaseModel):
    id: str = Field(default_factory=new_id)
    event_type: EventType
    entity_type: str
    entity_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
