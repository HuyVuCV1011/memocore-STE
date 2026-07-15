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


async def test_confirmed_alias_is_filtered_from_review(repos):
    person = await repos["people"].create(Person(display_name="Nguyễn Hoàng Khôi Nguyên"))
    event_service = EventService(repos["events"])
    suggestion = await event_service.append_event(
        EventType.ENTITY_ALIAS_SUGGESTED,
        "person",
        person.id,
        {"alias": "Dưa hấu", "canonical_name": person.display_name},
    )
    service = EntityConfirmationService(repos["people"], repos["projects"], event_service)

    await service.confirm(suggestion.id)
    review = await service.review("person")

    assert review.summary == "Chưa có gợi ý alias/merge cần xác nhận."


async def test_existing_alias_is_filtered_even_without_confirmation_event(repos):
    person = await repos["people"].create(
        Person(display_name="Nguyễn Hoàng Khôi Nguyên", aliases=["Dưa hấu"])
    )
    event_service = EventService(repos["events"])
    await event_service.append_event(
        EventType.ENTITY_ALIAS_SUGGESTED,
        "person",
        person.id,
        {"alias": "Dưa hấu", "canonical_name": person.display_name},
    )
    service = EntityConfirmationService(repos["people"], repos["projects"], event_service)

    review = await service.review("person")

    assert review.summary == "Chưa có gợi ý alias/merge cần xác nhận."


async def test_rejected_alias_is_persisted_and_removed_from_review(repos):
    person = await repos["people"].create(Person(display_name="Nguyễn Hoàng Khôi Nguyên"))
    event_service = EventService(repos["events"])
    suggestion = await event_service.append_event(
        EventType.ENTITY_ALIAS_SUGGESTED,
        "person",
        person.id,
        {"alias": "Dưa hấu", "canonical_name": person.display_name},
    )
    service = EntityConfirmationService(repos["people"], repos["projects"], event_service)

    result = await service.reject(suggestion.id)
    review = await service.review("person")
    events = await repos["events"].list_by_entity("person", person.id)

    assert result is not None
    assert review.summary == "Chưa có gợi ý alias/merge cần xác nhận."
    rejected = next(
        event for event in events if event.event_type == EventType.ENTITY_ALIAS_REJECTED
    )
    assert rejected.payload["suggestion_event_id"] == suggestion.id
    assert rejected.payload["status"] == "resolved"
    feedback = next(
        event for event in events if event.event_type == EventType.USER_FEEDBACK_RECORDED
    )
    assert feedback.payload["signal"] == "rejected"
    assert feedback.payload["status"] == "resolved"
    assert await service.confirm(suggestion.id) is None


async def test_ignored_alias_has_distinct_feedback_and_does_not_reappear(repos):
    project = await repos["projects"].find_or_create("MemoCore")
    event_service = EventService(repos["events"])
    suggestion = await event_service.append_event(
        EventType.ENTITY_ALIAS_SUGGESTED,
        "project",
        project.id,
        {"alias": "trợ lý", "canonical_name": project.name},
    )
    service = EntityConfirmationService(repos["people"], repos["projects"], event_service)

    result = await service.ignore(suggestion.id)
    review = await service.review("project")
    events = await repos["events"].list_by_entity("project", project.id)

    assert result is not None
    assert review.summary == "Chưa có gợi ý alias/merge cần xác nhận."
    assert any(
        event.event_type == EventType.ENTITY_ALIAS_IGNORED for event in events
    )
    feedback = next(
        event for event in events if event.event_type == EventType.USER_FEEDBACK_RECORDED
    )
    assert feedback.payload["signal"] == "ignored"
