"""UI tests for per-hunk (partial) apply in the changeset panel.

Pins the partial-apply flow:

- the hunk checkbox section appears for a pending multi-hunk change
  (and stays hidden for single-hunk / non-pending ones)
- Apply Selected Hunks with a subset writes the composed partial
  content, keeps the change pending, and rebases ``original`` on the
  written content so only the unapplied hunks remain
- with every hunk checked it behaves exactly like whole-file Apply
- rollback after a partial + full apply restores the pristine file
  (the backup is taken once, on the first write)
"""

from __future__ import annotations

import pytest

from polyglot_ai.core.hunks import apply_hunks, split_hunks
from polyglot_ai.ui.panels.changeset_panel import ChangesetPanel


def _lines(*names: str) -> str:
    return "".join(f"{n}\n" for n in names)


# Two edits separated by 20 unchanged lines — two hunks at default context.
ORIGINAL = _lines("a0", "a1", *[f"mid{i}" for i in range(20)], "z0", "z1")
PROPOSED = _lines("a0", "A1-CHANGED", *[f"mid{i}" for i in range(20)], "z0", "Z1-CHANGED")


@pytest.fixture
def panel(qtbot, tmp_path):
    p = ChangesetPanel()
    qtbot.addWidget(p)
    p.show()
    p.set_project_root(tmp_path)
    return p


def _add_and_select(panel, path: str, original: str, proposed: str) -> None:
    panel.add_change(path, original, proposed)
    for i in range(panel._file_list.count()):
        item = panel._file_list.item(i)
        from PyQt6.QtCore import Qt

        if item.data(Qt.ItemDataRole.UserRole) == path:
            panel._file_list.setCurrentRow(i)
            return
    raise AssertionError(f"path {path} not in file list")


def test_hunk_section_appears_for_multi_hunk_change(panel):
    _add_and_select(panel, "src/app.py", ORIGINAL, PROPOSED)
    assert panel._hunk_section.isVisible() is True
    assert len(panel._hunk_checkboxes) == 2
    assert all(cb.isChecked() for cb in panel._hunk_checkboxes)
    assert panel._apply_hunks_btn.isEnabled() is True
    assert "2/2" in panel._apply_hunks_btn.text()


def test_hunk_section_hidden_for_single_hunk_change(panel):
    _add_and_select(panel, "one.py", _lines("a", "b"), _lines("a", "B"))
    assert panel._hunk_section.isVisible() is False
    assert panel._hunk_checkboxes == []


def test_apply_subset_writes_partial_content_and_stays_pending(panel, tmp_path):
    target = tmp_path / "src" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text(ORIGINAL, encoding="utf-8")

    _add_and_select(panel, "src/app.py", ORIGINAL, PROPOSED)
    panel._hunk_checkboxes[1].setChecked(False)
    assert "1/2" in panel._apply_hunks_btn.text()
    panel._apply_selected_hunks()

    expected = apply_hunks(ORIGINAL, PROPOSED, [0])
    assert target.read_text(encoding="utf-8") == expected

    change = panel._changes["src/app.py"]
    assert change.status == "pending"
    # Rebased: the written content is the new baseline...
    assert change.original == expected
    assert change.proposed == PROPOSED
    # ...and exactly one hunk remains.
    assert len(split_hunks(change.original, change.proposed)) == 1
    # A backup of the pristine file was taken.
    assert change.backup_path is not None


def test_apply_all_hunks_checked_behaves_like_whole_file_apply(panel, tmp_path, qtbot):
    target = tmp_path / "app.py"
    target.write_text(ORIGINAL, encoding="utf-8")

    _add_and_select(panel, "app.py", ORIGINAL, PROPOSED)
    applied = []
    panel.change_applied.connect(applied.append)
    panel._apply_selected_hunks()

    assert target.read_text(encoding="utf-8") == PROPOSED
    assert panel._changes["app.py"].status == "applied"
    assert applied == ["app.py"]
    # Hunk section disappears once the change is no longer pending.
    assert panel._hunk_section.isVisible() is False


def test_no_hunks_checked_is_a_noop(panel, tmp_path):
    target = tmp_path / "app.py"
    target.write_text(ORIGINAL, encoding="utf-8")

    _add_and_select(panel, "app.py", ORIGINAL, PROPOSED)
    for cb in panel._hunk_checkboxes:
        cb.setChecked(False)
    assert panel._apply_hunks_btn.isEnabled() is False
    panel._apply_selected_hunks()

    assert target.read_text(encoding="utf-8") == ORIGINAL
    assert panel._changes["app.py"].status == "pending"


def test_rollback_after_partial_then_full_apply_restores_pristine(panel, tmp_path):
    target = tmp_path / "app.py"
    target.write_text(ORIGINAL, encoding="utf-8")

    _add_and_select(panel, "app.py", ORIGINAL, PROPOSED)
    # Partial apply (first hunk only). One hunk remains, so the hunk
    # section hides and the rest goes through the whole-file Apply.
    panel._hunk_checkboxes[1].setChecked(False)
    panel._apply_selected_hunks()
    assert panel._hunk_section.isVisible() is False
    panel._apply_selected()

    assert target.read_text(encoding="utf-8") == PROPOSED
    assert panel._changes["app.py"].status == "applied"

    panel._rollback_selected()
    # Backup from the FIRST write is used, so the file is pristine.
    assert target.read_text(encoding="utf-8") == ORIGINAL
    assert panel._changes["app.py"].status == "rolled_back"
