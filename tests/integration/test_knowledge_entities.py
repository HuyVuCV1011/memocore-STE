from memocore.domain.knowledge import Decision, DecisionStatus, Organization
from memocore.domain.models import Note, Person
from memocore.domain.schemas import (
    CaptureRequest,
    DecisionCandidate,
    KnowledgeRelationCandidate,
    NoteExtraction,
    OrganizationCandidate,
    ProjectHint,
)
from memocore.services.reference_resolver import ReferenceResolver


async def test_organization_and_decision_repositories_round_trip(repos):
    organization = await repos["organizations"].create(
        Organization(name="STE", aliases=["STE Company"], summary="Personal venture")
    )
    note = await repos["notes"].create(Note(raw_text="decision"))
    decision = await repos["decisions"].create(
        Decision(
            title="Keep orchestration on hold",
            organization_id=organization.id,
            source_note_id=note.id,
        )
    )

    assert (await repos["organizations"].get_by_id(organization.id)).name == "STE"
    assert (await repos["decisions"].list_all())[0].id == decision.id


async def test_capture_persists_explicit_organization_and_decision(
    capture_service, fake_provider, repos
):
    fake_provider.response = NoteExtraction(
        summary="MemoCore architecture decision",
        tags=["decision"],
        projects=[ProjectHint(name="MemoCore", confidence=1.0)],
        organizations=[
            OrganizationCandidate(name="STE", summary="Owner organization", confidence=1.0)
        ],
        decisions=[
            DecisionCandidate(
                title="Keep tool orchestration postponed",
                summary="Stabilize conversation transcripts first.",
                project_name="MemoCore",
                organization_name="STE",
                confidence=1.0,
            )
        ],
    )

    response = await capture_service.capture(
        CaptureRequest(
            raw_text=(
                "STE quyết định cho project MemoCore: hoãn tool orchestration "
                "cho tới khi transcript hội thoại ổn định."
            ),
            source_chat_id="knowledge-chat",
            source_message_id="knowledge-1",
        )
    )

    organizations = await repos["organizations"].list_all()
    decisions = await repos["decisions"].list_all()
    project = await repos["projects"].find_by_name_or_alias("MemoCore")

    assert response.organizations_created == 1
    assert response.decisions_created == 1
    assert organizations[0].name == "STE"
    assert decisions[0].project_id == project.id
    assert decisions[0].organization_id == organizations[0].id


async def test_context_resolver_distinguishes_organization_from_same_named_project(repos):
    await repos["projects"].find_or_create("STE")
    organization = await repos["organizations"].find_or_create("STE")
    resolver = ReferenceResolver(
        repos["chat_contexts"],
        repos["projects"],
        repos["people"],
        repos["tasks"],
        repos["organizations"],
    )

    reference = await resolver.resolve("org-chat", "nói cho tôi biết về tổ chức STE")

    assert reference.entity_type == "organization"
    assert reference.entity_id == organization.id


async def test_capture_persists_explicit_person_organization_project_relation(
    capture_service, fake_provider, repos
):
    fake_provider.response = NoteExtraction(
        summary="Lan leads Atlas at STE",
        projects=[ProjectHint(name="Atlas", confidence=1.0)],
        people=[],
        organizations=[OrganizationCandidate(name="STE", confidence=1.0)],
        relationships=[
            KnowledgeRelationCandidate(
                source_type="organization",
                source_name="STE",
                target_type="project",
                target_name="Atlas",
                relation_type="owns",
                confidence=0.95,
            )
        ],
    )

    response = await capture_service.capture(
        CaptureRequest(
            raw_text="STE owns project Atlas",
            source_chat_id="relations",
            source_message_id="1",
        )
    )
    project = await repos["projects"].find_by_name_or_alias("Atlas")
    relations = await repos["knowledge_relations"].list_for_entity("project", project.id)

    assert response.relationships_created == 1
    assert relations[0].relation_type == "owns"
    assert relations[0].source_note_id == response.note_id


async def test_decision_lifecycle_supersedes_previous_decision(
    capture_service, fake_provider, repos
):
    fake_provider.response = NoteExtraction(
        summary="Initial proposal",
        decisions=[
            DecisionCandidate(title="Use weekly review", status="proposed", confidence=1.0)
        ],
    )
    await capture_service.capture(
        CaptureRequest(raw_text="Proposed: Use weekly review", source_message_id="decision-1")
    )
    fake_provider.response = NoteExtraction(
        summary="Final decision",
        decisions=[
            DecisionCandidate(
                title="Use daily review",
                status="decided",
                supersedes_title="Use weekly review",
                confidence=1.0,
            )
        ],
    )
    await capture_service.capture(
        CaptureRequest(raw_text="Decided: Use daily review", source_message_id="decision-2")
    )

    decisions = {item.title: item for item in await repos["decisions"].list_all()}
    assert decisions["Use weekly review"].status == DecisionStatus.SUPERSEDED
    assert decisions["Use daily review"].supersedes_decision_id == decisions["Use weekly review"].id
