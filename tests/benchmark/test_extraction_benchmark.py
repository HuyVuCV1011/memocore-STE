import os

import pytest

from memocore.adapters.llm.provider_factory import create_provider
from memocore.config import ModelConfig
from memocore.services.task_extraction_service import ExtractionService
from tests.fixtures.benchmark_notes import BENCHMARK_CASES


pytestmark = pytest.mark.ollama


def score_extraction(extraction, expect: dict) -> tuple[int, int]:
    checks = {
        "has_task": bool(extraction.tasks),
        "has_reminder": bool(extraction.reminders),
        "has_memory": bool(extraction.memories),
        "has_project": bool(extraction.projects),
        "summary_not_empty": bool(extraction.summary.strip()),
        "reminder_has_date": bool(extraction.reminders and extraction.reminders[0].remind_at),
        "task_has_due_date": bool(extraction.tasks and extraction.tasks[0].due_at),
        "confidence_is_float": all(
            isinstance(item.confidence, float)
            for group in (
                extraction.tasks,
                extraction.reminders,
                extraction.memories,
                extraction.projects,
            )
            for item in group
        ),
    }
    if extraction.tasks:
        checks["task_title_contains"] = extraction.tasks[0].title
    if extraction.reminders:
        checks["reminder_title_contains"] = extraction.reminders[0].title
    if extraction.projects:
        checks["project_name_contains"] = extraction.projects[0].name
    if extraction.memories:
        checks["memory_bucket"] = extraction.memories[0].bucket.value
        checks["memory_kind"] = extraction.memories[0].kind.value

    passed = 0
    for key, expected in expect.items():
        actual = checks.get(key)
        if key.endswith("_contains"):
            passed += int(isinstance(actual, str) and expected.lower() in actual.lower())
        else:
            passed += int(actual == expected)
    return passed, len(expect)


@pytest.mark.skipif(
    not os.getenv("MEMOCORE_RUN_LIVE_BENCHMARK"),
    reason="set MEMOCORE_RUN_LIVE_BENCHMARK=1 to run provider benchmark",
)
async def test_live_extraction_benchmark():
    provider = create_provider(
        ModelConfig(
            provider=os.getenv("MODEL_PROVIDER", "ollama"),
            name=os.getenv("MODEL_NAME", "qwen3:14b"),
            base_url=os.getenv("MODEL_BASE_URL", "http://127.0.0.1:11434"),
            api_key=os.getenv("MODEL_API_KEY"),
        )
    )
    service = ExtractionService(provider)

    passed = 0
    total = 0
    for case in BENCHMARK_CASES:
        extraction = await service.extract(case["note"])
        case_passed, case_total = score_extraction(extraction, case["expect"])
        passed += case_passed
        total += case_total

    assert passed / total >= 0.85
