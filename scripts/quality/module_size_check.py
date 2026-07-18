from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ModuleBudget:
    path: str
    max_lines: int
    note: str


MODULE_BUDGETS = (
    ModuleBudget(
        "src/memocore/services/conversation_service.py",
        4050,
        "compatibility facade; new behavior should move behind focused services",
    ),
    ModuleBudget(
        "src/memocore/adapters/storage/repositories.py",
        2050,
        "repository layer should not absorb new domains without extraction",
    ),
    ModuleBudget(
        "src/memocore/services/capture_service.py",
        1500,
        "capture orchestration should stay behind extraction/persistence boundaries",
    ),
    ModuleBudget(
        "src/memocore/services/secretary_service.py",
        1550,
        "presentation/query behavior should keep moving into focused services",
    ),
    ModuleBudget(
        "src/memocore/services/clarification_service.py",
        1350,
        "clarification workflows should remain split by durable flow",
    ),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Guard MemoCore's known large modules against silent growth."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root. Defaults to the script's repository.",
    )
    args = parser.parse_args(argv)
    failures = check_module_sizes(args.root)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("Module size guard OK")
    for budget in MODULE_BUDGETS:
        count = count_lines(args.root / budget.path)
        print(f"- {budget.path}: {count}/{budget.max_lines}")
    return 0


def check_module_sizes(root: Path) -> list[str]:
    failures: list[str] = []
    for budget in MODULE_BUDGETS:
        module_path = root / budget.path
        if not module_path.exists():
            failures.append(f"{budget.path} is missing")
            continue
        line_count = count_lines(module_path)
        if line_count > budget.max_lines:
            failures.append(
                f"{budget.path} has {line_count} lines, budget is {budget.max_lines}; {budget.note}"
            )
    return failures


def count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


if __name__ == "__main__":
    raise SystemExit(main())
