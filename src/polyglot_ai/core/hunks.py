"""Pure hunk segmentation and partial-apply logic for changeset review.

Given an (original, proposed) pair of file contents, this module splits
the difference into contiguous change *hunks* — the same grouping that a
unified diff with N context lines would produce — and can compose the
partial result of applying only a chosen subset of those hunks.

Design notes:
  - Hunks come from ``difflib.SequenceMatcher.get_grouped_opcodes``, so
    each hunk includes up to ``context`` unchanged lines on either side.
    Groups are guaranteed disjoint and non-adjacent (difflib only splits
    on an equal run longer than 2*context), which makes subset
    application a simple ordered range substitution.
  - Line ranges are 0-based, end-exclusive indices into the
    ``splitlines(keepends=True)`` decomposition of each side, so
    re-joining reproduces content byte-for-byte (trailing-newline safe).
  - Applying *all* hunks reconstructs ``proposed`` exactly; applying
    *none* returns ``original`` exactly.

No Qt imports here — this module is deliberately UI-free so it can be
unit-tested headlessly.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

#: Unchanged lines of context included on each side of a hunk.
DEFAULT_CONTEXT = 3


@dataclass(frozen=True)
class Hunk:
    """One contiguous group of changes between original and proposed.

    ``old_start``/``old_end`` index into the original's lines and
    ``new_start``/``new_end`` into the proposed's lines (0-based,
    end-exclusive, including any context lines).
    """

    old_start: int
    old_end: int
    new_start: int
    new_end: int
    #: Unified-diff style lines (" ", "-", "+" prefixed), no header.
    lines: tuple[str, ...]

    @property
    def header(self) -> str:
        """A ``@@ -l,c +l,c @@`` header for this hunk (1-based, like diff)."""
        old_count = self.old_end - self.old_start
        new_count = self.new_end - self.new_start
        # Unified diff quirk: a zero-length range reports the line *before*.
        old_line = self.old_start + 1 if old_count else self.old_start
        new_line = self.new_start + 1 if new_count else self.new_start
        return f"@@ -{old_line},{old_count} +{new_line},{new_count} @@"

    @property
    def preview(self) -> str:
        """Header plus prefixed diff lines, as a single display string."""
        return "\n".join([self.header, *self.lines])


def split_hunks(original: str, proposed: str, context: int = DEFAULT_CONTEXT) -> list[Hunk]:
    """Split the difference between two contents into change hunks.

    Returns an empty list when the contents are identical. An empty
    ``original`` with non-empty ``proposed`` (new file) yields exactly
    one insertion hunk.
    """
    old_lines = original.splitlines(keepends=True)
    new_lines = proposed.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)

    hunks: list[Hunk] = []
    for group in matcher.get_grouped_opcodes(context):
        old_start, old_end = group[0][1], group[-1][2]
        new_start, new_end = group[0][3], group[-1][4]
        diff_lines: list[str] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                diff_lines.extend(" " + line.rstrip("\n") for line in old_lines[i1:i2])
                continue
            if tag in ("replace", "delete"):
                diff_lines.extend("-" + line.rstrip("\n") for line in old_lines[i1:i2])
            if tag in ("replace", "insert"):
                diff_lines.extend("+" + line.rstrip("\n") for line in new_lines[j1:j2])
        hunks.append(
            Hunk(
                old_start=old_start,
                old_end=old_end,
                new_start=new_start,
                new_end=new_end,
                lines=tuple(diff_lines),
            )
        )
    return hunks


def apply_hunks(
    original: str,
    proposed: str,
    selected_indices: list[int],
    context: int = DEFAULT_CONTEXT,
) -> str:
    """Compose the content that results from applying only some hunks.

    ``selected_indices`` index into ``split_hunks(original, proposed,
    context)`` — callers presenting hunks to a user must segment with
    the same ``context`` so indices line up. Selecting every hunk
    returns ``proposed`` exactly; selecting none returns ``original``.

    Raises ``IndexError`` for an index that names no hunk, so a stale
    selection can never silently corrupt a compose.
    """
    hunks = split_hunks(original, proposed, context)
    selected = set(selected_indices)
    for idx in selected:
        if not 0 <= idx < len(hunks):
            raise IndexError(f"hunk index {idx} out of range (have {len(hunks)} hunks)")

    old_lines = original.splitlines(keepends=True)
    new_lines = proposed.splitlines(keepends=True)

    result: list[str] = []
    pos = 0
    for idx, hunk in enumerate(hunks):
        result.extend(old_lines[pos : hunk.old_start])
        if idx in selected:
            result.extend(new_lines[hunk.new_start : hunk.new_end])
        else:
            result.extend(old_lines[hunk.old_start : hunk.old_end])
        pos = hunk.old_end
    result.extend(old_lines[pos:])
    return "".join(result)
