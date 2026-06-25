import pytest
from datetime import UTC, datetime
from uuid import uuid4

from memocore.domain.models import (
    Project,
    ProjectType,
    ProjectStatus,
    Task,
    MemoryItem,
    MemoryBucket,
    MemoryKind,
    MemoryStatus,
    Note,
)
from memocore.services.secretary_service import SecretaryService


@pytest.mark.anyio
async def test_migration_and_backfill(repos):
    project_repo = repos["projects"]
    
    # Check that structural parent nodes created in migration exist
    mindx = await project_repo.find_by_name_or_alias("MindX")
    assert mindx is not None
    assert mindx.project_type == ProjectType.PORTFOLIO
    assert mindx.status == ProjectStatus.ACTIVE
    
    data_bi = await project_repo.find_by_name_or_alias("Data & BI")
    assert data_bi is not None
    assert data_bi.project_type == ProjectType.CAPABILITY
    assert data_bi.status == ProjectStatus.ACTIVE
    # STE (parent of Data & BI) should be portfolio
    ste = await project_repo.get_by_id(data_bi.parent_project_id)
    assert ste is not None
    assert ste.name == "STE"
    assert ste.project_type == ProjectType.PORTFOLIO


@pytest.mark.anyio
async def test_migration_dynamic_foreign_key(tmp_path):
    from memocore.adapters.storage.sqlite import Database, SCHEMA, MIGRATION_LEDGER_SCHEMA
    
    db_file = tmp_path / "memocore_dynamic_migration.db"
    database = Database(db_file)
    
    conn = await database.connection()
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.executescript(MIGRATION_LEDGER_SCHEMA)
    await conn.executescript(SCHEMA)
    
    # Pre-insert STE with a random custom ID to simulate legacy production state
    custom_ste_id = str(uuid4())
    await conn.execute(
        "INSERT INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (custom_ste_id, "STE", "[]", "Custom STE description", "active", "[]", "2026-06-21T00:00:00", "2026-06-21T00:00:00", "2026-06-21T00:00:00"),
    )
    await conn.commit()
    
    # Apply migration (including 006)
    # The dynamic resolution SELECT subquery in 006 must resolve parent ID properly and avoid FK IntegrityError.
    await database._apply_migrations(conn)
    await conn.commit()
    
    # Verify Data & BI is linked to our custom STE ID
    row = await (await conn.execute("SELECT * FROM projects WHERE name = ?", ("Data & BI",))).fetchone()
    assert row is not None
    assert row["parent_project_id"] == custom_ste_id
    
    await database.close()


@pytest.mark.anyio
async def test_project_repository_find_or_create_defaults(repos):
    project_repo = repos["projects"]
    
    # Automatically created projects must default to status REVIEW and project_type None
    proj = await project_repo.find_or_create("Auto Generated Project")
    assert proj.status == ProjectStatus.REVIEW
    assert proj.project_type is None


@pytest.mark.anyio
async def test_project_repository_roundtrip(repos):
    project_repo = repos["projects"]
    
    # 1. Create root portfolio
    root = Project(
        name="Org A",
        project_type=ProjectType.PORTFOLIO,
        status=ProjectStatus.ACTIVE,
    )
    await repos["projects"]._execute(
        """
        INSERT INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (root.id, root.name, "[]", "", root.status.value, "[]", "2026-06-21T00:00:00", "2026-06-21T00:00:00", "2026-06-21T00:00:00", root.project_type.value, root.parent_project_id),
    )
    
    # 2. Create child capability
    child = Project(
        name="Org A Capability",
        project_type=ProjectType.CAPABILITY,
        status=ProjectStatus.ACTIVE,
        parent_project_id=root.id,
    )
    await repos["projects"]._execute(
        """
        INSERT INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (child.id, child.name, "[]", "", child.status.value, "[]", "2026-06-21T00:00:00", "2026-06-21T00:00:00", "2026-06-21T00:00:00", child.project_type.value, child.parent_project_id),
    )
    
    # Fetch back
    fetched_root = await project_repo.get_by_id(root.id)
    assert fetched_root is not None
    assert fetched_root.project_type == ProjectType.PORTFOLIO
    assert fetched_root.parent_project_id is None
    
    fetched_child = await project_repo.get_by_id(child.id)
    assert fetched_child is not None
    assert fetched_child.project_type == ProjectType.CAPABILITY
    assert fetched_child.parent_project_id == root.id
    
    # list_roots
    roots = await project_repo.list_roots()
    assert root.id in {r.id for r in roots}
    assert child.id not in {r.id for r in roots}
    
    # list_children
    children = await project_repo.list_children(root.id)
    assert child.id in {c.id for c in children}
    
    # list_by_type_or_status
    caps = await project_repo.list_by_type_or_status(project_type=ProjectType.CAPABILITY)
    assert child.id in {c.id for c in caps}
    assert root.id not in {c.id for c in caps}


@pytest.mark.anyio
async def test_project_repository_cycle_prevention(repos):
    project_repo = repos["projects"]
    
    p1 = await project_repo.find_or_create("P1")
    p2 = await project_repo.find_or_create("P2")
    p3 = await project_repo.find_or_create("P3")
    
    # Prevent self parenting
    with pytest.raises(ValueError, match="Project cannot be its own parent"):
        await project_repo.update_taxonomy(p1.id, ProjectType.PRODUCT, ProjectStatus.ACTIVE, p1.id)
        
    # Set parent: P1 -> P2
    await project_repo.update_taxonomy(p1.id, ProjectType.PRODUCT, ProjectStatus.ACTIVE, p2.id)
    # Set parent: P2 -> P3
    await project_repo.update_taxonomy(p2.id, ProjectType.PRODUCT, ProjectStatus.ACTIVE, p3.id)
    
    # Cycle: P3 -> P1 (creates cycle P3 -> P1 -> P2 -> P3)
    with pytest.raises(ValueError, match="Cycle detected in project hierarchy"):
        await project_repo.update_taxonomy(p3.id, ProjectType.PRODUCT, ProjectStatus.ACTIVE, p1.id)


@pytest.mark.anyio
async def test_project_repository_update_taxonomy_idempotent(repos):
    project_repo = repos["projects"]
    events_repo = repos["events"]
    
    p = await project_repo.find_or_create("Idempotent Test Project")
    
    # First update
    await project_repo.update_taxonomy(p.id, ProjectType.PRODUCT, ProjectStatus.ACTIVE, None)
    events_1 = await events_repo.list_by_entity("project", p.id)
    tax_events_1 = [e for e in events_1 if e.event_type == "project_taxonomy_updated"]
    assert len(tax_events_1) == 1
    
    # Second update with identical values (must not trigger new audit event)
    await project_repo.update_taxonomy(p.id, ProjectType.PRODUCT, ProjectStatus.ACTIVE, None)
    events_2 = await events_repo.list_by_entity("project", p.id)
    tax_events_2 = [e for e in events_2 if e.event_type == "project_taxonomy_updated"]
    assert len(tax_events_2) == 1


@pytest.mark.anyio
async def test_project_durable_links_integrity(repos):
    project_repo = repos["projects"]
    task_repo = repos["tasks"]
    memory_repo = repos["memory"]
    note_repo = repos["notes"]
    
    p = await project_repo.find_or_create("Linked Project")
    
    # Create valid Note
    note = await note_repo.create(Note(raw_text="Durable integrity context note"))
    
    # Create linked task
    task = await task_repo.create(
        Task(
            title="Integrity Task",
            source_note_id=note.id,
            project_id=p.id,
        )
    )
    
    # Create linked memory
    memory = await memory_repo.create(
        MemoryItem(
            bucket=MemoryBucket.PROJECT,
            kind=MemoryKind.FACT,
            content="Linked Memory fact",
            source_note_id=note.id,
            project_id=p.id,
        )
    )
    
    # Update project taxonomy
    await project_repo.update_taxonomy(p.id, ProjectType.PRODUCT, ProjectStatus.PAUSED, None)
    
    # Verify links are still fully intact
    active_tasks = await task_repo.list_active_by_project(p.id)
    assert len(active_tasks) == 1
    assert active_tasks[0].id == task.id
    
    active_memories = await memory_repo.list_active_by_project(p.id)
    assert len(active_memories) == 1
    assert active_memories[0].id == memory.id


@pytest.mark.anyio
async def test_project_context_descendants_aggregation(repos):
    project_repo = repos["projects"]
    task_repo = repos["tasks"]
    note_repo = repos["notes"]
    memory_repo = repos["memory"]
    
    # Clean DB projects
    conn = await project_repo.database.connection()
    await conn.execute("DELETE FROM projects")
    await conn.commit()
    
    # Portfolio Root
    ste = Project(name="STE", project_type=ProjectType.PORTFOLIO, status=ProjectStatus.ACTIVE)
    await project_repo._execute(
        "INSERT INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ste.id, ste.name, "[]", "", ste.status.value, "[]", "2026-06-21T00:00:00", "2026-06-21T00:00:00", "2026-06-21T00:00:00", ste.project_type.value, ste.parent_project_id),
    )
    
    # Capability Child
    data_bi = Project(name="Data & BI", project_type=ProjectType.CAPABILITY, status=ProjectStatus.ACTIVE, parent_project_id=ste.id)
    await project_repo._execute(
        "INSERT INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (data_bi.id, data_bi.name, "[]", "", data_bi.status.value, "[]", "2026-06-21T00:00:00", "2026-06-21T00:00:00", "2026-06-21T00:00:00", data_bi.project_type.value, data_bi.parent_project_id),
    )
    
    # Product grandchild
    stedata = Project(name="STEDATA", project_type=ProjectType.PRODUCT, status=ProjectStatus.ACTIVE, parent_project_id=data_bi.id)
    await project_repo._execute(
        "INSERT INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (stedata.id, stedata.name, "[]", "", stedata.status.value, "[]", "2026-06-21T00:00:00", "2026-06-21T00:00:00", "2026-06-21T00:00:00", stedata.project_type.value, stedata.parent_project_id),
    )
    
    # Create valid Note
    note = await note_repo.create(Note(raw_text="Grandchild task note"))
    
    # Create task linked to STEDATA grandchild
    task = await task_repo.create(Task(title="Descendant task", source_note_id=note.id, project_id=stedata.id))
    
    # Create memory linked to Data & BI child
    mem = await memory_repo.create(MemoryItem(
        bucket=MemoryBucket.PROJECT, kind=MemoryKind.FACT, content="Descendant memory fact",
        source_note_id=note.id, project_id=data_bi.id, status=MemoryStatus.ACTIVE
    ))
    
    service = SecretaryService(
        task_repo=task_repo, reminder_repo=repos["reminders"], followup_repo=repos["followups"],
        project_repo=project_repo, memory_repo=memory_repo
    )
    
    context = await service.project_context("STE")
    
    # Context of STE must aggregate both grandchild's task and child's memory
    assert "Descendant task" in context
    assert "Descendant memory fact" in context


@pytest.mark.anyio
async def test_reference_resolution(repos):
    project_repo = repos["projects"]
    
    p = await project_repo.find_or_create("STE Subproject X")
    await project_repo.update_aliases(p.id, ["SubX", "X-Branch"])
    
    # Resolve by name
    resolved_by_name = await project_repo.find_by_name_or_alias("STE Subproject X")
    assert resolved_by_name is not None
    assert resolved_by_name.id == p.id
    
    # Resolve by alias
    resolved_by_alias = await project_repo.find_by_name_or_alias("SubX")
    assert resolved_by_alias is not None
    assert resolved_by_alias.id == p.id


@pytest.mark.anyio
async def test_telegram_formatting(repos):
    project_repo = repos["projects"]
    task_repo = repos["tasks"]
    note_repo = repos["notes"]
    
    # Clean default ones to have full control
    conn = await project_repo.database.connection()
    await conn.execute("DELETE FROM projects")
    await conn.commit()
    
    # Portfolio Root
    ste = Project(
        name="STE",
        project_type=ProjectType.PORTFOLIO,
        status=ProjectStatus.ACTIVE,
    )
    await repos["projects"]._execute(
        """
        INSERT INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ste.id, ste.name, "[]", "", ste.status.value, "[]", "2026-06-21T00:00:00", "2026-06-21T00:00:00", "2026-06-21T00:00:00", ste.project_type.value, ste.parent_project_id),
    )
    
    # Capability Child
    data_bi = Project(
        name="STE Data & BI",
        project_type=ProjectType.CAPABILITY,
        status=ProjectStatus.ACTIVE,
        parent_project_id=ste.id,
    )
    await repos["projects"]._execute(
        """
        INSERT INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (data_bi.id, data_bi.name, "[]", "", data_bi.status.value, "[]", "2026-06-21T00:00:00", "2026-06-21T00:00:00", "2026-06-21T00:00:00", data_bi.project_type.value, data_bi.parent_project_id),
    )
    
    # Product grandchild with 0 tasks
    prod1 = Project(
        name="STE STEDATA / Dashboard",
        project_type=ProjectType.PRODUCT,
        status=ProjectStatus.ACTIVE,
        parent_project_id=data_bi.id,
    )
    await repos["projects"]._execute(
        """
        INSERT INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (prod1.id, prod1.name, "[]", "", prod1.status.value, "[]", "2026-06-21T00:00:00", "2026-06-21T00:00:00", "2026-06-21T00:00:00", prod1.project_type.value, prod1.parent_project_id),
    )
    
    # Product grandchild with 2 tasks
    prod2 = Project(
        name="STE Data Analyst Product",
        project_type=ProjectType.PRODUCT,
        status=ProjectStatus.ACTIVE,
        parent_project_id=data_bi.id,
    )
    await repos["projects"]._execute(
        """
        INSERT INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (prod2.id, prod2.name, "[]", "", prod2.status.value, "[]", "2026-06-21T00:00:00", "2026-06-21T00:00:00", "2026-06-21T00:00:00", prod2.project_type.value, prod2.parent_project_id),
    )
    
    # Create valid Note
    note = await note_repo.create(Note(raw_text="Telegram formatting presentation note"))
    
    # Create 2 tasks for prod2
    await task_repo.create(Task(title="Task A", source_note_id=note.id, project_id=prod2.id))
    await task_repo.create(Task(title="Task B", source_note_id=note.id, project_id=prod2.id))
    
    service = SecretaryService(
        task_repo=task_repo,
        reminder_repo=repos["reminders"],
        followup_repo=repos["followups"],
        project_repo=project_repo,
        memory_repo=repos["memory"],
    )
    
    output = await service.projects()
    
    # Expectations:
    # 1. Output must group under STE root.
    # 2. "STE Data & BI" is nested under STE, clean name should remove prefix "STE" -> "Data & BI".
    # 3. "STE STEDATA / Dashboard" clean name should remove prefix "STE Data & BI" and "STE" -> "STEDATA / Dashboard".
    # 4. Zero tasks must NOT display "Task đang mở: 0" line.
    # 5. Non-zero task counts should show up (e.g. " (2)") beside project name.
    
    assert "STE" in output
    assert "- Data & BI" in output
    assert "  - STEDATA / Dashboard" in output
    assert "  - Data Analyst Product (2)" in output
    assert "Task đang mở: 0" not in output


@pytest.mark.anyio
async def test_incubating_and_review_grouping(repos):
    project_repo = repos["projects"]
    
    # Clean default ones to have full control
    conn = await project_repo.database.connection()
    await conn.execute("DELETE FROM projects")
    await conn.commit()
    
    # Incubating project
    inc = Project(
        name="STE Gacha App",
        project_type=ProjectType.INDEPENDENT_PROJECT,
        status=ProjectStatus.INCUBATING,
    )
    await repos["projects"]._execute(
        """
        INSERT INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (inc.id, inc.name, "[]", "", inc.status.value, "[]", "2026-06-21T00:00:00", "2026-06-21T00:00:00", "2026-06-21T00:00:00", inc.project_type.value, inc.parent_project_id),
    )
    
    # Review project
    rev = Project(
        name="STE Course Materials Syllabus",
        project_type=ProjectType.PRODUCT,
        status=ProjectStatus.REVIEW,
    )
    await repos["projects"]._execute(
        """
        INSERT INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (rev.id, rev.name, "[]", "", rev.status.value, "[]", "2026-06-21T00:00:00", "2026-06-21T00:00:00", "2026-06-21T00:00:00", rev.project_type.value, rev.parent_project_id),
    )
    
    service = SecretaryService(
        task_repo=repos["tasks"],
        reminder_repo=repos["reminders"],
        followup_repo=repos["followups"],
        project_repo=project_repo,
        memory_repo=repos["memory"],
    )
    
    output = await service.projects()
    
    assert "Ideas / Needs review" in output
    assert "- Gacha App (incubating)" in output
    assert "- Course Materials Syllabus (review)" in output
