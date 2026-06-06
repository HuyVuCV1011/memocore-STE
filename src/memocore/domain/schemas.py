from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from memocore.domain.models import MemoryBucket, MemoryKind


def normalize_confidence(value: Any) -> float:
    if isinstance(value, str):
        labels = {"high": 0.9, "medium": 0.6, "low": 0.3}
        lowered = value.strip().lower()
        if lowered in labels:
            return labels[lowered]
        try:
            return float(lowered)
        except ValueError:
            return 0.5
    if value is None:
        return 0.5
    return float(value)


class TaskCandidate(BaseModel):
    title: str
    description: str = ""
    priority: str = "medium"
    due_at: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> float:
        return normalize_confidence(value)


class ReminderCandidate(BaseModel):
    title: str
    remind_at: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> float:
        return normalize_confidence(value)


class MemoryCandidate(BaseModel):
    bucket: MemoryBucket
    kind: MemoryKind
    content: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> float:
        return normalize_confidence(value)


class ProjectHint(BaseModel):
    name: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> float:
        return normalize_confidence(value)


class NoteExtraction(BaseModel):
    summary: str
    tags: list[str] = Field(default_factory=list)
    tasks: list[TaskCandidate] = Field(default_factory=list)
    reminders: list[ReminderCandidate] = Field(default_factory=list)
    projects: list[ProjectHint] = Field(default_factory=list)
    memories: list[MemoryCandidate] = Field(default_factory=list)


class CaptureRequest(BaseModel):
    source: str = "telegram"
    source_message_id: str | None = None
    source_chat_id: str | None = None
    raw_text: str


class CaptureResponse(BaseModel):
    note_id: str
    summary: str
    tasks_created: int = 0
    tasks_completed: int = 0
    reminders_created: int = 0
    memories_created: int = 0
    memories_deleted: int = 0
    duplicate: bool = False
    clarification_question: str | None = None
    errors: list[str] = Field(default_factory=list)


class IntentClassification(BaseModel):
    intent: Literal[
        "query_today",
        "query_tomorrow",
        "query_memory",
        "query_tasks",
        "query_tasks_due",
        "query_reminders",
        "query_projects",
        "query_people",
        "query_commitments",
        "query_context",
        "query_meeting_prep",
        "capture_task",
        "capture_reminder",
        "capture_memory",
        "update_task",
        "update_task_due",
        "mark_task_done",
        "delete_all_tasks",
        "memory_delete",
        "memory_correction",
        "correction_feedback",
        "clarification_answer",
        "casual_or_noop",
        "needs_clarification",
    ]
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    target_entity_hints: str | None = None
    ambiguity_detected: bool = False
    clarification_question: str | None = None
