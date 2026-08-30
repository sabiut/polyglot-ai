"""Tests for pure hunk segmentation and partial-apply composition."""

from __future__ import annotations

import pytest

from polyglot_ai.core.hunks import DEFAULT_CONTEXT, Hunk, apply_hunks, split_hunks


def _lines(*names: str) -> str:
    """Build newline-terminated content from line names."""
    return "".join(f"{n}\n" for n in names)


# Two edits separated by 20 unchanged lines — always two hunks at
# default context (gap 20 > 2 * 3).
MULTI_ORIG = _lines("a0", "a1", *[f"mid{i}" for i in range(20)], "z0", "z1")
MULTI_PROP = _lines("a0", "A1-CHANGED", *[f"mid{i}" for i in range(20)], "z0", "Z1-CHANGED")


class TestSplitHunks:
    def test_identical_contents_yield_no_hunks(self):
        content = _lines("a", "b", "c")
        assert split_hunks(content, content) == []

    def test_both_empty(self):
        assert split_hunks("", "") == []

    def test_single_hunk_for_one_change(self):
        orig = _lines("a", "b", "c", "d", "e")
        prop = _lines("a", "b", "X", "d", "e")
        hunks = split_hunks(orig, prop)
        assert len(hunks) == 1
        (h,) = hunks
        # Change at index 2, plus up to 3 lines of context each side.
        assert h.old_start == 0
        assert h.old_end == 5
        assert "-c" in h.lines
        assert "+X" in h.lines

    def test_multiple_disjoint_hunks(self):
        hunks = split_hunks(MULTI_ORIG, MULTI_PROP)
        assert len(hunks) == 2
        assert any("+A1-CHANGED" in line for line in hunks[0].lines)
        assert any("+Z1-CHANGED" in line for line in hunks[1].lines)
        # Hunks are ordered and non-overlapping.
        assert hunks[0].old_end <= hunks[1].old_start

    def test_nearby_edits_merge_into_one_hunk(self):
        # Edits separated by 4 equal lines: 4 <= 2 * context(3), so one hunk.
        orig = _lines("a", "k0", "k1", "k2", "k3", "b")
        prop = _lines("A", "k0", "k1", "k2", "k3", "B")
        hunks = split_hunks(orig, prop)
        assert len(hunks) == 1

    def test_far_edits_do_not_merge(self):
        # Separated by 7 equal lines: 7 > 2 * context(3), so two hunks.
        gap = [f"k{i}" for i in range(7)]
        orig = _lines("a", *gap, "b")
        prop = _lines("A", *gap, "B")
        hunks = split_hunks(orig, prop)
        assert len(hunks) == 2

    def test_context_parameter_controls_merging(self):
        gap = [f"k{i}" for i in range(7)]
        orig = _lines("a", *gap, "b")
        prop = _lines("A", *gap, "B")
        # With context 4, the 7-line gap (<= 8) keeps the edits together.
        assert len(split_hunks(orig, prop, context=4)) == 1
        assert len(split_hunks(orig, prop, context=3)) == 2

    def test_insertion_only(self):
        orig = _lines("a", "b")
        prop = _lines("a", "new1", "new2", "b")
        hunks = split_hunks(orig, prop)
        assert len(hunks) == 1
        (h,) = hunks
        assert "+new1" in h.lines
        assert "+new2" in h.lines
        assert not any(line.startswith("-") for line in h.lines)

    def test_deletion_only(self):
        orig = _lines("a", "gone1", "gone2", "b")
        prop = _lines("a", "b")
        hunks = split_hunks(orig, prop)
        assert len(hunks) == 1
        (h,) = hunks
        assert "-gone1" in h.lines
        assert "-gone2" in h.lines
        assert not any(line.startswith("+") for line in h.lines)

    def test_empty_original_new_file_is_one_hunk(self):
        prop = _lines("line1", "line2", "line3")
        hunks = split_hunks("", prop)
        assert len(hunks) == 1
        (h,) = hunks
        assert h.old_start == 0
        assert h.old_end == 0
        assert h.new_start == 0
        assert h.new_end == 3
        assert all(line.startswith("+") for line in h.lines)

    def test_delete_everything_is_one_hunk(self):
        orig = _lines("line1", "line2")
        hunks = split_hunks(orig, "")
        assert len(hunks) == 1
        assert all(line.startswith("-") for line in hunks[0].lines)

    def test_no_trailing_newline_handled(self):
        orig = "a\nb"
        prop = "a\nc"
        hunks = split_hunks(orig, prop)
        assert len(hunks) == 1
        assert apply_hunks(orig, prop, [0]) == prop

    def test_preview_contains_header_and_lines(self):
        hunks = split_hunks(MULTI_ORIG, MULTI_PROP)
        preview = hunks[0].preview
        assert preview.startswith("@@ -")
        assert "+A1-CHANGED" in preview

    def test_header_format(self):
        h = Hunk(old_start=2, old_end=5, new_start=2, new_end=6, lines=())
        assert h.header == "@@ -3,3 +3,4 @@"

    def test_header_zero_length_range(self):
        # Pure insertion at index 2 into original: zero-length old range
        # reports the line before, diff-style.
        h = Hunk(old_start=2, old_end=2, new_start=2, new_end=4, lines=())
        assert h.header == "@@ -2,0 +3,2 @@"


class TestApplyHunks:
    def test_apply_none_returns_original(self):
        assert apply_hunks(MULTI_ORIG, MULTI_PROP, []) == MULTI_ORIG

    def test_apply_all_returns_proposed_exactly(self):
        hunks = split_hunks(MULTI_ORIG, MULTI_PROP)
        result = apply_hunks(MULTI_ORIG, MULTI_PROP, list(range(len(hunks))))
        assert result == MULTI_PROP

    def test_apply_first_only(self):
        result = apply_hunks(MULTI_ORIG, MULTI_PROP, [0])
        assert "A1-CHANGED\n" in result
        assert "Z1-CHANGED\n" not in result
        assert "z1\n" in result

    def test_apply_last_only(self):
        result = apply_hunks(MULTI_ORIG, MULTI_PROP, [1])
        assert "A1-CHANGED\n" not in result
        assert "a1\n" in result
        assert "Z1-CHANGED\n" in result

    def test_partial_then_rest_reaches_proposed(self):
        # Applying hunk 0, then re-splitting against the intermediate
        # content and applying what remains, lands exactly on proposed.
        intermediate = apply_hunks(MULTI_ORIG, MULTI_PROP, [0])
        remaining = split_hunks(intermediate, MULTI_PROP)
        assert len(remaining) == 1
        assert apply_hunks(intermediate, MULTI_PROP, [0]) == MULTI_PROP

    def test_three_hunks_middle_subset(self):
        gap1 = [f"g1_{i}" for i in range(10)]
        gap2 = [f"g2_{i}" for i in range(10)]
        orig = _lines("a", *gap1, "b", *gap2, "c")
        prop = _lines("A", *gap1, "B", *gap2, "C")
        hunks = split_hunks(orig, prop)
        assert len(hunks) == 3
        result = apply_hunks(orig, prop, [1])
        assert "a\n" in result
        assert "B\n" in result
        assert "c\n" in result

    def test_empty_original_apply_all(self):
        prop = _lines("x", "y")
        assert apply_hunks("", prop, [0]) == prop

    def test_empty_original_apply_none(self):
        prop = _lines("x", "y")
        assert apply_hunks("", prop, []) == ""

    def test_duplicate_indices_are_harmless(self):
        result = apply_hunks(MULTI_ORIG, MULTI_PROP, [0, 0, 0])
        assert result == apply_hunks(MULTI_ORIG, MULTI_PROP, [0])

    def test_out_of_range_index_raises(self):
        with pytest.raises(IndexError):
            apply_hunks(MULTI_ORIG, MULTI_PROP, [5])
        with pytest.raises(IndexError):
            apply_hunks(MULTI_ORIG, MULTI_PROP, [-1])

    def test_identical_contents_any_empty_selection(self):
        content = _lines("same")
        assert apply_hunks(content, content, []) == content

    def test_custom_context_selection_alignment(self):
        gap = [f"k{i}" for i in range(7)]
        orig = _lines("a", *gap, "b")
        prop = _lines("A", *gap, "B")
        # context=4 merges into a single hunk, so index 0 takes both edits.
        assert apply_hunks(orig, prop, [0], context=4) == prop
        # context=3 keeps them separate.
        partial = apply_hunks(orig, prop, [0], context=3)
        assert "A\n" in partial
        assert "b\n" in partial

    def test_default_context_constant(self):
        assert DEFAULT_CONTEXT == 3
