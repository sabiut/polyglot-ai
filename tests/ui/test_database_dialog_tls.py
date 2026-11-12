"""The connection dialog offers only the encryption modes an engine can honour."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from polyglot_ai.ui.panels.database_dialogs import AddConnectionDialog  # noqa: E402


def _modes(dialog) -> list[str]:
    combo = dialog._ssl_combo
    return [combo.itemData(i) for i in range(combo.count())]


def _choose_engine(dialog, label: str) -> None:
    dialog._type_combo.setCurrentIndex(dialog._type_combo.findText(label))


def test_sql_server_does_not_offer_certificate_verification(qtbot):
    dialog = AddConnectionDialog()
    qtbot.addWidget(dialog)

    _choose_engine(dialog, "PostgreSQL")
    assert "verify" in _modes(dialog)
    dialog._ssl_combo.setCurrentIndex(dialog._ssl_combo.findData("verify"))

    _choose_engine(dialog, "SQL Server")
    assert "verify" not in _modes(dialog)
    # Was "verify": falls to encryption without the check, never to none.
    assert dialog._ssl_combo.currentData() == "require"
    assert dialog._ca_block.isHidden()

    _choose_engine(dialog, "Oracle")
    assert "verify" in _modes(dialog) and dialog._ssl_combo.currentData() == "require"


def test_a_choice_the_new_engine_supports_is_kept(qtbot):
    dialog = AddConnectionDialog()
    qtbot.addWidget(dialog)
    _choose_engine(dialog, "MySQL")
    dialog._ssl_combo.setCurrentIndex(dialog._ssl_combo.findData("disable"))
    _choose_engine(dialog, "SQL Server")
    assert dialog._ssl_combo.currentData() == "disable"


def test_prefill_for_sql_server_never_selects_verify(qtbot):
    dialog = AddConnectionDialog(
        prefill={"db_type": "mssql", "host": "db.example.com", "ssl": "verify"}
    )
    qtbot.addWidget(dialog)
    assert dialog._ssl_combo.currentData() == "require"

    postgres = AddConnectionDialog(
        prefill={"db_type": "postgresql", "host": "db.example.com", "ssl": "verify"}
    )
    qtbot.addWidget(postgres)
    assert postgres._ssl_combo.currentData() == "verify"
