from memocore.domain.models import EventType, Person
from memocore.services.entity_confirmation_service import EntityConfirmationService
from memocore.services.event_service import EventService


async def test_person_alias_is_only_added_after_confirmation(repos):
    person = await repos["people"].create(Person(display_name="Nguyễn Hoàng Khôi Nguyên"))
    event_service = EventService(repos["events"])
    suggestion = await event_service.append_event(
        EventType.ENTITY_ALIAS_SUGGESTED,
        "person",
        person.id,
        {"alias": "Dưa hấu", "canonical_name": person.display_name},
    )
    service = EntityConfirmationService(
        repos["people"],
        repos["projects"],
        event_service,
    )

    prompt = await service.prompt(suggestion.id)
    before = await repos["people"].get_by_id(person.id)
    assert prompt is not None and "Dưa hấu" in prompt.summary
    assert before is not None and before.aliases == []

    result = await service.confirm(suggestion.id)
    after = await repos["people"].get_by_id(person.id)
    assert result is not None
    assert after is not None and after.aliases == ["Dưa hấu"]


async def test_project_alias_confirmation_updates_lookup(repos):
    project = await repos["projects"].find_or_create("STE Dashboard")
    event_service = EventService(repos["events"])
    suggestion = await event_service.append_event(
        EventType.ENTITY_ALIAS_SUGGESTED,
        "project",
        project.id,
        {"alias": "dashboard STE", "canonical_name": project.name},
    )
    service = EntityConfirmationService(
        repos["people"],
        repos["projects"],
        event_service,
    )

    await service.confirm(suggestion.id)
    resolved = await repos["projects"].find_or_create("dashboard STE")

    assert resolved.id == project.id
