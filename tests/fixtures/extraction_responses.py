from memocore.domain.models import MemoryBucket, MemoryKind
from memocore.domain.schemas import (
    CommitmentCandidate,
    FollowUpCandidate,
    MeetingCandidate,
    MemoryCandidate,
    NoteExtraction,
    PersonCandidate,
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
            due_at="2099-06-01T09:00:00+00:00",
            confidence=0.9,
        )
    ],
    reminders=[
        ReminderCandidate(
            title="Call Alex about the budget",
            remind_at="2099-06-01T09:00:00+00:00",
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

MISSING_REMINDER_TIME = NoteExtraction(
    summary="Call John.",
    tags=["john"],
    reminders=[
        ReminderCandidate(
            title="Call John",
            remind_at=None,
            confidence=0.9,
        )
    ],
)


V4_NATURAL_CAPTURE = NoteExtraction(
    summary="Alex Nguyen will review MindX with follow-up and commitments.",
    tags=["alex", "mindx", "review"],
    projects=[ProjectHint(name="MindX", confidence=0.9)],
    people=[
        PersonCandidate(
            display_name="Alex Nguyen",
            aliases=["Alex"],
            relationship="MindX reviewer",
            confidence=0.95,
        )
    ],
    tasks=[
        TaskCandidate(
            title="Send Alex Nguyen the MindX brief",
            description="Share the brief before the review meeting.",
            priority="medium",
            due_at="2099-06-01T09:00:00+00:00",
            person_name="Alex Nguyen",
            project_name="MindX",
            confidence=0.9,
        )
    ],
    memories=[
        MemoryCandidate(
            bucket=MemoryBucket.PROJECT,
            kind=MemoryKind.PROJECT_STATE,
            content="Alex Nguyen prefers concise MindX review notes.",
            person_name="Alex Nguyen",
            project_name="MindX",
            confidence=0.9,
        )
    ],
    meetings=[
        MeetingCandidate(
            title="MindX review with Alex Nguyen",
            starts_at="2099-06-02T10:00:00+00:00",
            person_names=["Alex Nguyen"],
            project_name="MindX",
            notes="Review MindX scope.",
            confidence=0.9,
        )
    ],
    followups=[
        FollowUpCandidate(
            title="Ask Alex Nguyen for MindX review slot",
            due_at="2099-06-03T09:00:00+00:00",
            person_name="Alex Nguyen",
            project_name="MindX",
            confidence=0.9,
        )
    ],
    commitments=[
        CommitmentCandidate(
            title="Alex Nguyen owes MindX feedback",
            direction="owed_to_user",
            due_at="2099-06-04T09:00:00+00:00",
            person_name="Alex Nguyen",
            project_name="MindX",
            confidence=0.9,
        )
    ],
)
