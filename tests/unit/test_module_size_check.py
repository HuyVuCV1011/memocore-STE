from __future__ import annotations

from scripts.quality import module_size_check


def test_module_size_guard_passes_current_workspace():
    assert module_size_check.check_module_sizes(module_size_check.REPO_ROOT) == []


def test_module_size_guard_reports_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(
        module_size_check,
        "MODULE_BUDGETS",
        (module_size_check.ModuleBudget("missing.py", 1, "test budget"),),
    )

    failures = module_size_check.check_module_sizes(tmp_path)

    assert failures == ["missing.py is missing"]


def test_module_size_guard_reports_over_budget(tmp_path, monkeypatch):
    module_path = tmp_path / "large.py"
    module_path.write_text("a\nb\nc\n", encoding="utf-8")
    monkeypatch.setattr(
        module_size_check,
        "MODULE_BUDGETS",
        (module_size_check.ModuleBudget("large.py", 2, "split me"),),
    )

    failures = module_size_check.check_module_sizes(tmp_path)

    assert failures == ["large.py has 3 lines, budget is 2; split me"]
