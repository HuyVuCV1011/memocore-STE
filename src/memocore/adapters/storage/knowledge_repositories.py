from __future__ import annotations

import json

from memocore.adapters.storage.repositories import BaseRepository, _dt, _loads
from memocore.domain.knowledge import Decision, DecisionStatus, Organization


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
                organization_id, source_note_id, confidence, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
