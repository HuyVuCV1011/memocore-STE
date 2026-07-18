from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from urllib.parse import unquote


REPO_ROOT = Path(__file__).resolve().parents[2]
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass(frozen=True)
class LinkIssue:
    path: Path
    line: int
    target: str
    message: str

    def format(self, root: Path) -> str:
        display = self.path.relative_to(root).as_posix()
        return f"{display}:{self.line}: {self.target} - {self.message}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check local Markdown links.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="Repository root.")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    issues = check_markdown_links(root)
    if issues:
        for issue in issues:
            print(f"FAIL: {issue.format(root)}", file=sys.stderr)
        return 1
    print("Markdown link check OK")
    return 0


def check_markdown_links(root: Path) -> list[LinkIssue]:
    issues: list[LinkIssue] = []
    for path in sorted(root.rglob("*.md")):
        if _is_ignored_path(path, root):
            continue
        issues.extend(_check_file(path, root))
    return issues


def _check_file(path: Path, root: Path) -> list[LinkIssue]:
    issues: list[LinkIssue] = []
    in_fence = False
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in LINK_RE.finditer(line):
            raw_target = match.group(1).strip()
            if _is_external_or_special(raw_target):
                continue
            issue = _validate_target(path, root, raw_target, line_number)
            if issue:
                issues.append(issue)
    return issues


def _validate_target(
    source: Path,
    root: Path,
    raw_target: str,
    line_number: int,
) -> LinkIssue | None:
    target = _strip_title(raw_target)
    if not target or target.startswith("#"):
        target_path = source
        anchor = target[1:] if target.startswith("#") else ""
    else:
        path_part, _, anchor = target.partition("#")
        path_part = unquote(path_part)
        if path_part.startswith("/"):
            target_path = root / path_part.lstrip("/")
        else:
            target_path = (source.parent / path_part).resolve()
        if not _is_inside(target_path, root):
            return LinkIssue(source, line_number, raw_target, "target escapes repository root")
        if not target_path.exists():
            return LinkIssue(source, line_number, raw_target, "target file does not exist")
    if anchor and target_path.suffix.lower() == ".md":
        anchors = _anchors_for(target_path)
        normalized = _anchor(anchor)
        if normalized not in anchors:
            return LinkIssue(source, line_number, raw_target, "target anchor does not exist")
    return None


def _anchors_for(path: Path) -> set[str]:
    anchors: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = HEADING_RE.match(line)
        if match:
            anchors.add(_anchor(match.group(2)))
    return anchors


def _anchor(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text)
    return text.strip("-")


def _strip_title(target: str) -> str:
    if target.startswith("<") and ">" in target:
        return target[1 : target.index(">")]
    return target.split()[0]


def _is_external_or_special(target: str) -> bool:
    lowered = target.lower()
    return (
        "://" in lowered
        or lowered.startswith(("mailto:", "tel:", "app://", "file:", "vscode:"))
    )


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _is_ignored_path(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    ignored_parts = {
        ".git",
        ".venv",
        "__pycache__",
        "backups",
        "data",
        "exports",
        "logs",
        "personal_profile_review",
    }
    return any(part in ignored_parts for part in relative.parts)


if __name__ == "__main__":
    raise SystemExit(main())
