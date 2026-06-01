import pytest

from memocore.domain.models import MemoryBucket, MemoryKind
from memocore.domain.schemas import (
    MemoryCandidate,
    NoteExtraction,
    ProjectHint,
    ReminderCandidate,
    TaskCandidate,
)
from tests.benchmark.test_extraction_benchmark import score_extraction
from tests.fixtures.benchmark_notes import BENCHMARK_CASES


# ---------------------------------------------------------------------------
# Structure validation
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {"id", "note", "expect"}


def test_all_benchmark_cases_have_required_fields():
    for case in BENCHMARK_CASES:
        missing = REQUIRED_FIELDS - set(case.keys())
        assert not missing, f"Case {case.get('id', '?')} missing fields: {missing}"


def test_benchmark_case_ids_unique():
    ids = [case["id"] for case in BENCHMARK_CASES]
    assert len(ids) == len(set(ids)), f"Duplicate case IDs found: {ids}"


# ---------------------------------------------------------------------------
# Scoring tests
# ---------------------------------------------------------------------------

def test_scoring_perfect_match():
    """Build a NoteExtraction that satisfies the 'task_simple_en' case."""
    case = next(c for c in BENCHMARK_CASES if c["id"] == "task_simple_en")
    extraction = NoteExtraction(
        summary="Call Alex tomorrow about the budget",
        tags=["budget"],
        tasks=[
            TaskCandidate(
                title="Call Alex about the budget",
                priority="medium",
                due_at="2026-06-01T09:00:00",
                confidence=0.9,
            )
        ],
        reminders=[
            ReminderCandidate(
                title="Call Alex",
                remind_at="2026-06-01T09:00:00",
                confidence=0.9,
            )
        ],
    )
    passed, total = score_extraction(extraction, case["expect"])
    assert passed == total, f"Expected perfect score {total}/{total}, got {passed}/{total}"


def test_scoring_mismatch():
    """An empty extraction should fail most checks for 'task_simple_en'."""
    case = next(c for c in BENCHMARK_CASES if c["id"] == "task_simple_en")
    extraction = NoteExtraction(summary="Nothing here")
    passed, total = score_extraction(extraction, case["expect"])
    assert passed < total, f"Expected some failures but got {passed}/{total}"


def test_scoring_no_action_perfect():
    """The 'no_action' case should pass with a bare extraction."""
    case = next(c for c in BENCHMARK_CASES if c["id"] == "no_action")
    extraction = NoteExtraction(summary="Nice weather today")
    passed, total = score_extraction(extraction, case["expect"])
    assert passed == total, f"Expected perfect score {total}/{total}, got {passed}/{total}"


def test_scoring_memory_case():
    """The 'memory_preference' case should pass with a correct memory."""
    case = next(c for c in BENCHMARK_CASES if c["id"] == "memory_preference")
    extraction = NoteExtraction(
        summary="User prefers dark mode and bullet points",
        memories=[
            MemoryCandidate(
                bucket=MemoryBucket.PROFILE,
                kind=MemoryKind.PREFERENCE,
                content="Prefers dark mode and bullet points",
                confidence=0.9,
            )
        ],
    )
    passed, total = score_extraction(extraction, case["expect"])
    assert passed == total, f"Expected perfect score {total}/{total}, got {passed}/{total}"
