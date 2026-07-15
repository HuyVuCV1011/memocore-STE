from datetime import UTC, datetime, timedelta

from memocore.domain.models import Task, TaskStatus
from memocore.services.work_state_service import WorkStateService


def test_work_state_keeps_waiting_and_blocked_out_of_next_actions():
    now = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    service = WorkStateService()
    actionable = Task(
        title="Finish report",
        source_note_id="note-1",
        due_at=now - timedelta(hours=1),
        priority="high",
    )
    waiting = Task(
        title="Wait for Alex",
        source_note_id="note-1",
        status=TaskStatus.WAITING,
        due_at=now - timedelta(days=1),
    )
    blocked = Task(
        title="Blocked by vendor",
        source_note_id="note-1",
        status=TaskStatus.BLOCKED,
        due_at=now - timedelta(days=1),
    )

    state = service.classify([waiting, blocked, actionable], now)

    assert [item.task.title for item in state.next_actions] == ["Finish report"]
    assert [task.title for task in state.waiting] == ["Wait for Alex"]
    assert [task.title for task in state.blocked] == ["Blocked by vendor"]


def test_work_state_keeps_routines_actionable_but_lower_than_hard_deadlines():
    now = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    service = WorkStateService()
    hard_deadline = Task(
        title="Submit proposal",
        source_note_id="note-1",
        due_at=now + timedelta(hours=2),
    )
    routine = Task(
        title="Tập gym",
        source_note_id="note-1",
        due_at=now + timedelta(hours=1),
        recurrence_rule="daily",
    )

    state = service.classify([routine, hard_deadline], now)

    assert [item.task.title for item in state.next_actions] == ["Submit proposal", "Tập gym"]
    assert state.next_actions[0].tier == "P1"
    assert state.next_actions[1].tier == "P3"


def test_work_state_uses_upcoming_only_when_today_has_no_actionable_work():
    now = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    service = WorkStateService()
    upcoming = Task(
        title="Prepare next sprint",
        source_note_id="note-1",
        due_at=now + timedelta(days=2),
    )

    state = service.classify([upcoming], now)

    assert [item.task.title for item in state.next_actions] == ["Prepare next sprint"]
    assert state.next_actions[0].reason == "mốc sắp tới"
