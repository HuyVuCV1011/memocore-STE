from __future__ import annotations

import json

from memocore.adapters.storage.repositories import BaseRepository, _dt, _loads
from memocore.domain.knowledge import (
    Decision,
    DecisionStatus,
    KnowledgeRelation,
    KnowledgeRelationStatus,
    Organization,
)
from memocore.domain.models import utc_now


class OrganizationRepository(BaseRepository):
    async def create(self, organization: Organization) -> Organization:
        await self._execute(
            """
            INSERT INTO organizations (
                id, name, aliases, summary, status, tags, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization.id,
                organization.name,
                json.dumps(organization.aliases),
                organization.summary,
                organization.status,
                json.dumps(organization.tags),
                _dt(organization.created_at),
                _dt(organization.updated_at),
            ),
        )
        return organization

    async def find_or_create(self, name: str) -> Organization:
        for item in await self.list_all():
            names = {item.name.casefold(), *(alias.casefold() for alias in item.aliases)}
            if name.casefold() in names:
                return item
        return await self.create(Organization(name=name))

    async def list_all(self) -> list[Organization]:
        conn = await self.database.connection()
        rows = await (await conn.execute("SELECT * FROM organizations ORDER BY name")).fetchall()
        return [_organization_from_row(row) for row in rows]

    async def get_by_id(self, organization_id: str) -> Organization | None:
        conn = await self.database.connection()
        row = await (
            await conn.execute("SELECT * FROM organizations WHERE id = ?", (organization_id,))
        ).fetchone()
        return _organization_from_row(row) if row else None


class DecisionRepository(BaseRepository):
    async def create(self, decision: Decision) -> Decision:
        await self._execute(
            """
            INSERT INTO decisions (
                id, title, summary, status, decided_at, project_id, person_id,
                organization_id, source_note_id, confidence, supersedes_decision_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.id,
                decision.title,
                decision.summary,
                decision.status.value,
                _dt(decision.decided_at),
                decision.project_id,
                decision.person_id,
                decision.organization_id,
                decision.source_note_id,
                decision.confidence,
                decision.supersedes_decision_id,
                _dt(decision.created_at),
                _dt(decision.updated_at),
            ),
        )
        return decision

    async def list_all(self) -> list[Decision]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute("SELECT * FROM decisions ORDER BY decided_at DESC")
        ).fetchall()
        return [_decision_from_row(row) for row in rows]

    async def list_by_project(self, project_id: str) -> list[Decision]:
        return [item for item in await self.list_all() if item.project_id == project_id]

    async def get_by_id(self, decision_id: str) -> Decision | None:
        conn = await self.database.connection()
        row = await (
            await conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,))
        ).fetchone()
        return _decision_from_row(row) if row else None

    async def find_current_by_title(self, title: str) -> Decision | None:
        normalized = title.casefold().strip()
        return next(
            (
                item
                for item in await self.list_all()
                if item.title.casefold().strip() == normalized
                and item.status != DecisionStatus.SUPERSEDED
            ),
            None,
        )

    async def supersede(self, decision_id: str, replacement_id: str) -> None:
        await self._execute(
            "UPDATE decisions SET status = ?, updated_at = ? WHERE id = ?",
            (DecisionStatus.SUPERSEDED.value, _dt(utc_now()), decision_id),
        )
        await self._execute(
            "UPDATE decisions SET supersedes_decision_id = ?, updated_at = ? WHERE id = ?",
            (decision_id, _dt(utc_now()), replacement_id),
        )


class KnowledgeRelationRepository(BaseRepository):
    async def create(self, relation: KnowledgeRelation) -> KnowledgeRelation:
        await self._execute(
            """
            INSERT OR IGNORE INTO knowledge_relations (
                id, source_type, source_id, target_type, target_id, relation_type,
                source_note_id, confidence, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                relation.id,
                relation.source_type.value,
                relation.source_id,
                relation.target_type.value,
                relation.target_id,
                relation.relation_type,
                relation.source_note_id,
                relation.confidence,
                relation.status.value,
                _dt(relation.created_at),
                _dt(relation.updated_at),
            ),
        )
        return relation

    async def list_for_entity(self, entity_type: str, entity_id: str) -> list[KnowledgeRelation]:
        conn = await self.database.connection()
        rows = await (
            await conn.execute(
                """
                SELECT * FROM knowledge_relations
                WHERE (source_type = ? AND source_id = ?)
                   OR (target_type = ? AND target_id = ?)
                ORDER BY confidence DESC, created_at DESC
                """,
                (entity_type, entity_id, entity_type, entity_id),
            )
        ).fetchall()
        return [_relation_from_row(row) for row in rows]

    async def update_status(self, relation_id: str, status: KnowledgeRelationStatus) -> None:
        await self._execute(
            "UPDATE knowledge_relations SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, _dt(utc_now()), relation_id),
        )


def _organization_from_row(row) -> Organization:
    return Organization(
        id=row["id"],
        name=row["name"],
        aliases=_loads(row["aliases"]),
        summary=row["summary"],
        status=row["status"],
        tags=_loads(row["tags"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _decision_from_row(row) -> Decision:
    return Decision(
        id=row["id"],
        title=row["title"],
        summary=row["summary"],
        status=DecisionStatus(row["status"]),
        decided_at=row["decided_at"],
        project_id=row["project_id"],
        person_id=row["person_id"],
        organization_id=row["organization_id"],
        source_note_id=row["source_note_id"],
        confidence=row["confidence"],
        supersedes_decision_id=row["supersedes_decision_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _relation_from_row(row) -> KnowledgeRelation:
    return KnowledgeRelation(
        id=row["id"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        relation_type=row["relation_type"],
        source_note_id=row["source_note_id"],
        confidence=row["confidence"],
        status=KnowledgeRelationStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
