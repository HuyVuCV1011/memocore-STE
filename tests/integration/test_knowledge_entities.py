from memocore.domain.knowledge import Decision, Organization
from memocore.domain.models import Note
from memocore.domain.schemas import (
    CaptureRequest,
    DecisionCandidate,
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
