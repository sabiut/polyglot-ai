"""Data models for code review."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Category(str, Enum):
    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    STYLE = "style"
    TESTS = "tests"
    LOGIC = "logic"
    ERROR_HANDLING = "error_handling"
    OTHER = "other"


class FileStatus(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


@dataclass
class DiffHunk:
    """A single hunk in a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str = ""
    lines: list[str] = field(default_factory=list)

    @property
    def added_lines(self) -> list[tuple[int, str]]:
        """Return (line_number, content) for added lines."""
        result = []
        line_num = self.new_start
        for line in self.lines:
            if line.startswith("+"):
                result.append((line_num, line[1:]))
                line_num += 1
            elif line.startswith("-"):
                pass  # deleted line, doesn't increment new line number
            else:
                line_num += 1
        return result

    @property
    def removed_lines(self) -> list[tuple[int, str]]:
        """Return (line_number, content) for removed lines."""
        result = []
        line_num = self.old_start
        for line in self.lines:
            if line.startswith("-"):
                result.append((line_num, line[1:]))
                line_num += 1
            elif line.startswith("+"):
                pass
            else:
                line_num += 1
        return result


@dataclass
class DiffFile:
    """A single file's diff."""

    path: str
    status: FileStatus
    old_path: str | None = None  # For renames
    hunks: list[DiffHunk] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0

    @property
    def total_changes(self) -> int:
        return self.additions + self.deletions


@dataclass
class ReviewFinding:
    """A single review finding/comment."""

    file: str
    line: int
    severity: Severity
    category: Category
    title: str
    body: str
    suggestion: str | None = None  # Suggested code fix


@dataclass
class ReviewResult:
    """Complete review result.

    ``status`` distinguishes a successful empty review ("ok" with no findings)
    from a failure ("failed") so UI code can render error states distinctly
    instead of confusing an error with a clean scan. ``error`` holds the
    user-facing failure message when ``status != "ok"``.
    """

    summary: str
    findings: list[ReviewFinding] = field(default_factory=list)
    files_reviewed: int = 0
    total_additions: int = 0
    total_deletions: int = 0
    model: str = ""
    provider: str = ""
    status: str = "ok"  # "ok" | "failed" | "empty"
    error: str | None = None
    truncated_files: list[str] = field(default_factory=list)


@dataclass
class PRSummary:
    """AI-generated pull-request description.

    ``status`` distinguishes "ok" from "failed" so the UI can render a
    distinct error state instead of showing an empty description.
    """

    title: str = ""
    summary: list[str] = field(default_factory=list)
    test_plan: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    files_changed: int = 0
    additions: int = 0
    deletions: int = 0
    model: str = ""
    provider: str = ""
    status: str = "ok"  # "ok" | "failed" | "empty"
    error: str | None = None

    def to_markdown(self, template: str | None = None) -> str:
        """Render the summary as a markdown PR body.

        If ``template`` (the repo's .github/PULL_REQUEST_TEMPLATE.md) is
        provided and contains a recognised section header (``## Summary``,
        ``## Test plan``, ``## Test Plan``, ``## Risks``, ``## Changes``,
        ``## Description``), the matching content is appended below that
        header in the template, preserving every other section the
        template contains. Otherwise we emit a default three-section
        layout.
        """
        summary_md = "\n".join(f"- {s}" for s in self.summary) if self.summary else "_(none)_"
        test_md = (
            "\n".join(f"- [ ] {s}" for s in self.test_plan)
            if self.test_plan
            else "- [ ] _(add test steps)_"
        )
        risks_md = "\n".join(f"- ⚠️ {s}" for s in self.risks) if self.risks else "_None._"

        if template:
            filled = self._fill_template(template, summary_md, test_md, risks_md)
            if filled is not None:
                return filled

        return f"## Summary\n{summary_md}\n\n## Test plan\n{test_md}\n\n## Risks\n{risks_md}\n"

    @staticmethod
    def _fill_template(
        template: str,
        summary_md: str,
        test_md: str,
        risks_md: str,
    ) -> str | None:
        """Fill known headers in a PR template. Returns None if no
        recognised headers were found (caller should fall back to the
        default layout).
        """
        import re

        # Map case-insensitive header text to the content we want under it.
        section_content = {
            "summary": summary_md,
            "description": summary_md,
            "changes": summary_md,
            "what changed": summary_md,
            "test plan": test_md,
            "testing": test_md,
            "how to test": test_md,
            "how has this been tested": test_md,
            "risks": risks_md,
            "risk": risks_md,
            "rollback plan": risks_md,
        }

        # Match `## Header` lines (any level 1-4). Capture the header and
        # everything until the next header or end of document.
        header_re = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.MULTILINE)
        matches = list(header_re.finditer(template))
        if not matches:
            return None

        replaced_any = False
        out_parts: list[str] = []
        cursor = 0
        for i, m in enumerate(matches):
            header_text = m.group(2).strip().lower()
            content = section_content.get(header_text)
            section_end = matches[i + 1].start() if i + 1 < len(matches) else len(template)
            if content is not None:
                # Keep everything before this header, then the header
                # itself, then our generated content.
                out_parts.append(template[cursor : m.end()])
                out_parts.append("\n")
                out_parts.append(content)
                out_parts.append("\n\n")
                cursor = section_end
                replaced_any = True

        if not replaced_any:
            return None
        out_parts.append(template[cursor:])
        return "".join(out_parts)

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.HIGH)

    @property
    def by_severity(self) -> dict[str, list[ReviewFinding]]:
        result: dict[str, list[ReviewFinding]] = {}
        for f in self.findings:
            result.setdefault(f.severity.value, []).append(f)
        return result

    @property
    def by_file(self) -> dict[str, list[ReviewFinding]]:
        result: dict[str, list[ReviewFinding]] = {}
        for f in self.findings:
            result.setdefault(f.file, []).append(f)
        return result
