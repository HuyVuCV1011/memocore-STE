BENCHMARK_CASES = [
    {
        "id": "task_simple_en",
        "note": "Call Alex tomorrow at 9am about the budget",
        "expect": {
            "has_task": True,
            "task_title_contains": "Alex",
            "has_reminder": True,
            "reminder_has_date": True,
            "has_memory": False,
            "has_project": False,
        },
    },
    {
        "id": "reminder_vi",
        "note": "Nhac toi mai luc 8h goi cho Minh",
        "expect": {
            "has_task": False,
            "has_reminder": True,
            "reminder_has_date": True,
            "reminder_title_contains": "Minh",
        },
    },
    {
        "id": "memory_preference",
        "note": "Remember that I prefer dark mode and bullet points",
        "expect": {
            "has_memory": True,
            "memory_bucket": "profile",
            "memory_kind": "preference",
            "has_reminder": False,
        },
    },
    {
        "id": "memory_vs_reminder",
        "note": "Remember that my wifi password is abc123",
        "expect": {"has_memory": True, "has_reminder": False},
    },
    {
        "id": "no_action",
        "note": "The weather is really nice today",
        "expect": {
            "has_task": False,
            "has_reminder": False,
            "has_memory": False,
            "has_project": False,
            "summary_not_empty": True,
        },
    },
    {
        "id": "explicit_project",
        "note": "For the MemoCore project, add unit tests for the provider",
        "expect": {
            "has_project": True,
            "project_name_contains": "MemoCore",
            "has_task": True,
        },
    },
    {
        "id": "no_hallucinated_project",
        "note": "Buy groceries on the way home",
        "expect": {"has_project": False, "has_task": True},
    },
    {
        "id": "complex_multi",
        "note": (
            "Meeting with Lan tomorrow at 2pm about the Q3 report. "
            "Remind me to prepare slides tonight. "
            "Remember that Lan prefers detailed agendas."
        ),
        "expect": {"has_task": True, "has_reminder": True, "has_memory": True},
    },
    {
        "id": "confidence_numeric",
        "note": "Maybe call the dentist next week",
        "expect": {"has_task": True, "confidence_is_float": True},
    },
    {
        "id": "relative_date_next_monday",
        "note": "Submit the report next Monday",
        "expect": {"has_task": True, "task_has_due_date": True},
    },
]
