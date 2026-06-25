from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from memocore.domain.models import TimestampedModel, utc_now


class KnowledgeEntityType(StrEnum):
    PROJECT = "project"
    PERSON = "person"
    ORGANIZATION = "organization"
    DECISION = "decision"


class DecisionStatus(StrEnum):
    PROPOSED = "proposed"
    DECIDED = "decided"
    SUPERSEDED = "superseded"


class KnowledgeRelationStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    REJECTED = "rejected"


class KnowledgeRelation(TimestampedModel):
    source_type: KnowledgeEntityType
    source_id: str
    target_type: KnowledgeEntityType
    target_id: str
    relation_type: str
    source_note_id: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    status: KnowledgeRelationStatus = KnowledgeRelationStatus.CANDIDATE


class Organization(TimestampedModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    summary: str = ""
    status: str = "active"
    tags: list[str] = Field(default_factory=list)


class Decision(TimestampedModel):
    title: str
    summary: str = ""
    status: DecisionStatus = DecisionStatus.DECIDED
    decided_at: datetime = Field(default_factory=utc_now)
    project_id: str | None = None
    person_id: str | None = None
    organization_id: str | None = None
    source_note_id: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    supersedes_decision_id: str | None = None
