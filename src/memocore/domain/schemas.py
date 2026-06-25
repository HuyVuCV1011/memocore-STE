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
    person_name: str | None = None
    project_name: str | None = None
    recurrence_rule: Literal["daily", "weekly"] | None = None
    duration_minutes: int | None = Field(default=None, ge=1)
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
    person_name: str | None = None
    project_name: str | None = None
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


class PersonCandidate(BaseModel):
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    relationship: str = ""
    notes: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> float:
        return normalize_confidence(value)


class OrganizationCandidate(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    summary: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> float:
        return normalize_confidence(value)


class DecisionCandidate(BaseModel):
    title: str
    summary: str = ""
    project_name: str | None = None
    person_name: str | None = None
    organization_name: str | None = None
    status: Literal["proposed", "decided", "superseded"] = "decided"
    supersedes_title: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> float:
        return normalize_confidence(value)


class KnowledgeRelationCandidate(BaseModel):
    source_type: Literal["project", "person", "organization"]
    source_name: str
    target_type: Literal["project", "person", "organization"]
    target_name: str
    relation_type: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> float:
        return normalize_confidence(value)


class MeetingCandidate(BaseModel):
    title: str
    starts_at: str | None = None
    ends_at: str | None = None
    person_names: list[str] = Field(default_factory=list)
    project_name: str | None = None
    notes: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> float:
        return normalize_confidence(value)


class FollowUpCandidate(BaseModel):
    title: str
    due_at: str | None = None
    person_name: str | None = None
    project_name: str | None = None
    notes: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> float:
        return normalize_confidence(value)


class CommitmentCandidate(BaseModel):
    title: str
    direction: Literal["user_owes", "owed_to_user", "mutual"] | None = None
    due_at: str | None = None
    person_name: str | None = None
    project_name: str | None = None
    notes: str = ""
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
    people: list[PersonCandidate] = Field(default_factory=list)
    organizations: list[OrganizationCandidate] = Field(default_factory=list)
    decisions: list[DecisionCandidate] = Field(default_factory=list)
    relationships: list[KnowledgeRelationCandidate] = Field(default_factory=list)
    meetings: list[MeetingCandidate] = Field(default_factory=list)
    followups: list[FollowUpCandidate] = Field(default_factory=list)
    commitments: list[CommitmentCandidate] = Field(default_factory=list)


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
    people_created: int = 0
    organizations_created: int = 0
    decisions_created: int = 0
    relationships_created: int = 0
    meetings_created: int = 0
    followups_created: int = 0
    commitments_created: int = 0
    memories_deleted: int = 0
    duplicate: bool = False
    clarification_question: str | None = None
    duplicate_suggestions: list[str] = Field(default_factory=list)
    entity_suggestion_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class IntentClassification(BaseModel):
    intent: Literal[
        "query_today",
        "query_tomorrow",
        "query_memory",
        "query_profile",
        "query_tasks",
        "query_tasks_due",
        "query_task_recurrence",
        "query_reminders",
        "query_projects",
        "query_people",
        "query_commitments",
        "query_context",
        "query_meeting_prep",
        "update_knowledge",
        "rollback_knowledge_update",
        "capture_task",
        "capture_reminder",
        "capture_memory",
        "update_task",
        "update_task_due",
        "update_task_priority",
        "update_task_recurrence",
        "mark_task_done",
        "cancel_task",
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


class KnowledgeQueryPlan(BaseModel):
    entities: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    record_types: list[
        Literal[
            "memory",
            "project",
            "person",
            "organization",
            "decision",
            "relationship",
            "task",
            "followup",
            "commitment",
            "meeting",
            "reminder",
        ]
    ] = Field(default_factory=list)
    time_scope: Literal["past", "current", "today", "tomorrow", "future", "any"] = "current"
    answer_style: Literal["direct", "summary", "list"] = "direct"


class AssistantAction(BaseModel):
    label: str
    action_id: str
    row: int = 0


class AssistantSection(BaseModel):
    heading: str | None = None
    lines: list[str] = Field(default_factory=list)


class AssistantResponse(BaseModel):
    title: str
    summary: str | None = None
    sections: list[AssistantSection] = Field(default_factory=list)
    footer: str | None = None
    actions: list[AssistantAction] = Field(default_factory=list)
