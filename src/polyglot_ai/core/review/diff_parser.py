"""Parse unified diff output into structured DiffFile/DiffHunk objects."""

from __future__ import annotations

import re
from .models import DiffFile, DiffHunk, FileStatus


_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_FILE_MODE = re.compile(r"^(new|deleted) file mode")
_RENAME_FROM = re.compile(r"^rename from (.+)$")
_RENAME_TO = re.compile(r"^rename to (.+)$")


def parse_diff(diff_text: str) -> list[DiffFile]:
    """Parse unified diff text into a list of DiffFile objects."""
    files: list[DiffFile] = []
    current_file: DiffFile | None = None
    current_hunk: DiffHunk | None = None

    for line in diff_text.splitlines():
        # New file diff header
        m = _DIFF_HEADER.match(line)
        if m:
            if current_file is not None:
                _finalize_file(current_file)
                files.append(current_file)
            current_file = DiffFile(
                path=m.group(2),
                status=FileStatus.MODIFIED,
            )
            current_hunk = None
            continue

        if current_file is None:
            continue

        # File mode (new/deleted)
        m = _FILE_MODE.match(line)
        if m:
            if m.group(1) == "new":
                current_file.status = FileStatus.ADDED
            elif m.group(1) == "deleted":
                current_file.status = FileStatus.DELETED
            continue

        # Rename detection
        m = _RENAME_FROM.match(line)
        if m:
            current_file.old_path = m.group(1)
            current_file.status = FileStatus.RENAMED
            continue

        m = _RENAME_TO.match(line)
        if m:
            current_file.path = m.group(1)
            continue

        # Hunk header
        m = _HUNK_HEADER.match(line)
        if m:
            current_hunk = DiffHunk(
                old_start=int(m.group(1)),
                old_count=int(m.group(2) or 1),
                new_start=int(m.group(3)),
                new_count=int(m.group(4) or 1),
                header=m.group(5).strip(),
            )
            current_file.hunks.append(current_hunk)
            continue

        # Diff content lines
        if current_hunk is not None:
            if line.startswith("+") or line.startswith("-") or line.startswith(" "):
                current_hunk.lines.append(line)
            # Skip "\ No newline at end of file" etc.

    # Don't forget the last file
    if current_file is not None:
        _finalize_file(current_file)
        files.append(current_file)

    return files


def _finalize_file(f: DiffFile) -> None:
    """Count additions and deletions."""
    for hunk in f.hunks:
        for line in hunk.lines:
            if line.startswith("+"):
                f.additions += 1
            elif line.startswith("-"):
                f.deletions += 1


def format_diff_for_review(files: list[DiffFile], max_chars: int = 60000) -> str:
    """Format parsed diff files into a compact string for the AI prompt.

    Includes file paths, status, and the actual diff content.
    Truncates if the total exceeds max_chars.
    """
    parts: list[str] = []
    total = 0

    for f in files:
        header = f"### {f.path} ({f.status.value}) +{f.additions}/-{f.deletions}\n"
        hunks_text = ""
        for hunk in f.hunks:
            hunk_header = f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@ {hunk.header}\n"
            hunk_body = "\n".join(hunk.lines) + "\n"
            hunks_text += hunk_header + hunk_body

        section = header + hunks_text + "\n"
        if total + len(section) > max_chars:
            parts.append(f"... (truncated, {len(files) - len(parts)} more files)\n")
            break
        parts.append(section)
        total += len(section)

    return "".join(parts)
