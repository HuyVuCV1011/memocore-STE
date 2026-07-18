from __future__ import annotations

from scripts.quality import docs_link_check


def test_markdown_link_check_accepts_existing_file_and_anchor(tmp_path):
    readme = tmp_path / "README.md"
    docs = tmp_path / "docs"
    docs.mkdir()
    target = docs / "guide.md"
    readme.write_text("[Guide](docs/guide.md#quick-start)\n", encoding="utf-8")
    target.write_text("# Quick Start\n", encoding="utf-8")

    assert docs_link_check.check_markdown_links(tmp_path) == []


def test_markdown_link_check_reports_missing_file(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("[Missing](docs/missing.md)\n", encoding="utf-8")

    issues = docs_link_check.check_markdown_links(tmp_path)

    assert len(issues) == 1
    assert issues[0].message == "target file does not exist"


def test_markdown_link_check_reports_missing_anchor(tmp_path):
    readme = tmp_path / "README.md"
    target = tmp_path / "guide.md"
    readme.write_text("[Guide](guide.md#missing)\n", encoding="utf-8")
    target.write_text("# Present\n", encoding="utf-8")

    issues = docs_link_check.check_markdown_links(tmp_path)

    assert len(issues) == 1
    assert issues[0].message == "target anchor does not exist"


def test_markdown_link_check_ignores_external_and_code_fence(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(
        "[External](https://example.com)\n"
        "```\n"
        "[Missing](missing.md)\n"
        "```\n",
        encoding="utf-8",
    )

    assert docs_link_check.check_markdown_links(tmp_path) == []
