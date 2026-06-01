from memocore.domain.models import MemoryBucket, MemoryKind
from memocore.domain.schemas import (
    MemoryCandidate,
    NoteExtraction,
    ProjectHint,
    ReminderCandidate,
    TaskCandidate,
)


TASK_AND_REMINDER = NoteExtraction(
    summary="Call Alex about the budget.",
    tags=["budget", "alex"],
    tasks=[
        TaskCandidate(
            title="Call Alex about the budget",
            description="Discuss budget details with Alex.",
            priority="medium",
            due_at="2026-06-01T09:00:00+00:00",
            confidence=0.9,
        )
    ],
    reminders=[
        ReminderCandidate(
            title="Call Alex about the budget",
            remind_at="2026-06-01T09:00:00+00:00",
            confidence=0.9,
        )
    ],
    projects=[ProjectHint(name="Budget", confidence=0.75)],
)

PROFILE_MEMORY = NoteExtraction(
    summary="User prefers concise updates.",
    tags=["preference"],
    memories=[
        MemoryCandidate(
            bucket=MemoryBucket.PROFILE,
            kind=MemoryKind.PREFERENCE,
            content="Prefers concise updates and bullet points.",
            confidence=0.95,
        )
    ],
)

NO_ACTION = NoteExtraction(summary="A thought about the weather.")
