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
    """Complete review result."""
    summary: str
    findings: list[ReviewFinding] = field(default_factory=list)
    files_reviewed: int = 0
    total_additions: int = 0
    total_deletions: int = 0
    model: str = ""
    provider: str = ""

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
