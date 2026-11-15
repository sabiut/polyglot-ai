"""The connection dialog's EC2 list for Session Manager, and the panel's connect error."""

from __future__ import annotations

import os
import threading
import time

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("POLYGLOT_AI_DISABLE_UPDATE_CHECK", "1")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from polyglot_ai.core import aws_resources  # noqa: E402
from polyglot_ai.core.db_dialects import build_url, form_values  # noqa: E402
from polyglot_ai.ui.panels.database_dialogs import AddConnectionDialog  # noqa: E402
from polyglot_ai.ui.panels.database_widgets import SchemaTree  # noqa: E402

INSTANCES = [
    ("i-0aaa  bastion", "i-0aaa"),
    ("i-0bbb  app  — not connected to Session Manager", "i-0bbb"),
]


def _pump_until(pred, timeout_s=3.0):
    app = QApplication.instance()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return pred()


class _Calls(list):
    """The ``(profile, region)`` pairs asked for, with the answers to give."""

    answers: dict


@pytest.fixture
def lookups(monkeypatch):
    """Stands in for AWS: records who was asked for, answers from ``answers``."""
    calls = _Calls()
    answers: dict = {"default": (INSTANCES, "")}

    def running_instances(profile="", region=""):
        calls.append((profile, region))
        answer = answers.get((profile, region), answers["default"])
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(aws_resources, "running_instances", running_instances)
    calls.answers = answers
    return calls


def _items(dialog) -> list[str]:
    return [dialog._ssm_target.itemText(i) for i in range(dialog._ssm_target.count())]


def _choose(dialog, route: str) -> None:
    dialog._route_combo.setCurrentIndex(dialog._route_combo.findData(route))


def _new_dialog(qtbot, prefill=None, **kwargs):
    dialog = AddConnectionDialog(prefill=prefill, **kwargs)
    qtbot.addWidget(dialog)
    if prefill is None:  # a file database has no route to choose
        dialog._type_combo.setCurrentIndex(dialog._type_combo.findData("mssql"))
    return dialog


def test_choosing_session_manager_lists_the_running_instances(qtbot, lookups):
    """The list was only ever filled when the dialog was opened from the AWS panel."""
    dialog = _new_dialog(qtbot)
    dialog._aws_profile.setText("work")
    dialog._aws_region.setText("ap-southeast-2")
    dialog._aws_region.editingFinished.emit()
    _pump_until(lambda: False, timeout_s=0.1)
    assert lookups == []  # connecting directly: AWS isn't asked anything

    _choose(dialog, "ssm")
    assert dialog._ssm_note.text() == "Looking for running instances…"
    assert _pump_until(lambda: _items(dialog) == [label for label, _id in INSTANCES])

    assert lookups == [("work", "ap-southeast-2")]
    assert dialog._ssm_note.text() == (
        "2 running instances in ap-southeast-2 (profile work). "
        "Choose one in the database's network, or type an ID."
    )
    assert dialog._ssm_target.currentText() == ""  # offered, not chosen for you

    dialog._ssm_target.setCurrentIndex(0)
    assert dialog._ssm_instance() == "i-0aaa"


def test_the_list_is_asked_for_once_per_profile_and_region(qtbot, lookups):
    dialog = _new_dialog(qtbot)
    _choose(dialog, "ssm")
    assert _pump_until(lambda: len(_items(dialog)) == 2)

    _choose(dialog, "ssh")
    _choose(dialog, "ssm")
    dialog._aws_region.editingFinished.emit()
    assert lookups == [("", "")]
    assert "in the profile's default region." in dialog._ssm_note.text()

    lookups.answers[("", "eu-west-1")] = ([("i-0eee  other", "i-0eee")], "")
    dialog._aws_region.setText("eu-west-1")
    dialog._aws_region.editingFinished.emit()
    assert _pump_until(lambda: _items(dialog) == ["i-0eee  other"])
    assert lookups == [("", ""), ("", "eu-west-1")]
    assert dialog._ssm_note.text().startswith("1 running instance in eu-west-1.")


def test_a_saved_instance_id_survives_the_list_arriving(qtbot, lookups):
    options = {
        "url_encoded": True,
        "ssl": "require",
        "tunnel": {"kind": "ssm", "target": "i-0bbb", "profile": "work", "region": "eu-west-1"},
    }
    url = build_url("mssql", "db.internal", 1433, "app", "pw", "shop")
    dialog = _new_dialog(
        qtbot, prefill=form_values("rds-db", "mssql", url, options), editing=True, taken_names=[]
    )
    assert dialog._ssm_target.currentText() == "i-0bbb"
    assert _pump_until(lambda: len(_items(dialog)) == 2)

    assert lookups == [("work", "eu-west-1")]
    # Shown as the list has it, saved as the ID it always was.
    assert dialog._ssm_target.currentText().startswith("i-0bbb  app")
    value = dialog.get_values()
    assert value.options == options and value.connection_string == url


def test_an_id_that_is_not_in_the_list_is_left_as_typed(qtbot, lookups):
    dialog = _new_dialog(qtbot)
    _choose(dialog, "ssm")
    dialog._ssm_target.setEditText("i-0f46361d534ea8069")
    assert _pump_until(lambda: len(_items(dialog)) == 2)
    assert dialog._ssm_target.currentText() == "i-0f46361d534ea8069"
    assert dialog._ssm_instance() == "i-0f46361d534ea8069"


@pytest.mark.parametrize(
    ("answer", "note"),
    [
        (
            ([], "Your AWS session has expired — run `aws sso login`."),
            "Couldn't list the instances: Your AWS session has expired — run `aws sso login`. "
            "You can still type an instance ID.",
        ),
        (
            RuntimeError("aws crashed"),
            "Couldn't list the instances: aws crashed You can still type an instance ID.",
        ),
        (
            ([], ""),
            "No running EC2 instances in ap-southeast-2 (profile work). Session Manager "
            "needs one in the database's network to connect through.",
        ),
    ],
)
def test_an_empty_list_says_why(qtbot, lookups, answer, note):
    lookups.answers["default"] = answer
    dialog = _new_dialog(qtbot)
    dialog._aws_profile.setText("work")
    dialog._aws_region.setText("ap-southeast-2")
    _choose(dialog, "ssm")
    dialog._ssm_target.setEditText("i-0typed")
    assert _pump_until(lambda: dialog._ssm_note.text() == note)
    assert _items(dialog) == [] and dialog._ssm_instance() == "i-0typed"


def test_instances_the_aws_panel_already_read_are_not_asked_for_again(qtbot, lookups):
    dialog = _new_dialog(
        qtbot,
        prefill={
            "db_type": "mssql",
            "host": "db.internal",
            "route": "ssm",
            "profile": "work",
            "region": "ap-southeast-2",
            "bastions": [("i-0abc  jump", "i-0abc")],
        },
    )
    _pump_until(lambda: False, timeout_s=0.15)
    assert lookups == []
    assert _items(dialog) == ["i-0abc  jump"]
    assert dialog._ssm_note.text().startswith("1 running instance in ap-southeast-2 (profile work)")


def test_an_answer_for_a_region_since_changed_is_dropped(qtbot, lookups, monkeypatch):
    """The slow answer for the old region must not replace the new region's list."""
    first_may_answer = threading.Event()
    answered: list[str] = []

    def running_instances(profile="", region=""):
        lookups.append((profile, region))
        if region == "us-east-1":
            first_may_answer.wait(5)
            answered.append(region)
            return INSTANCES, ""
        return [("i-0eee  other", "i-0eee")], ""

    monkeypatch.setattr(aws_resources, "running_instances", running_instances)
    dialog = _new_dialog(qtbot)
    dialog._aws_region.setText("us-east-1")
    _choose(dialog, "ssm")
    dialog._aws_region.setText("eu-west-1")
    dialog._aws_region.editingFinished.emit()
    assert _pump_until(lambda: _items(dialog) == ["i-0eee  other"])

    first_may_answer.set()
    assert _pump_until(lambda: answered == ["us-east-1"])
    _pump_until(lambda: False, timeout_s=0.2)
    assert _items(dialog) == ["i-0eee  other"]
    assert dialog._ssm_note.text().startswith("1 running instance in eu-west-1.")
    assert lookups == [("", "us-east-1"), ("", "eu-west-1")]


@pytest.mark.parametrize(("engine", "port"), [("mssql", "1433"), ("mysql", "3306")])
def test_the_first_engine_chosen_brings_its_own_port(qtbot, engine, port):
    """A new dialog opens on SQLite; SQL Server chosen from there kept PostgreSQL's 5432."""
    dialog = AddConnectionDialog()
    qtbot.addWidget(dialog)
    dialog._type_combo.setCurrentIndex(dialog._type_combo.findData(engine))
    assert dialog._port_input.text() == port

    dialog._port_input.setText("11433")  # a port of the user's own is theirs to keep
    dialog._type_combo.setCurrentIndex(dialog._type_combo.findData("postgresql"))
    assert dialog._port_input.text() == "11433"


# ── why a connection failed ─────────────────────────────────────────

LONG = (
    "Couldn't connect: No answer from db.example.internal (the connection timed out). "
    "If the database is in a private network, connect through an SSH bastion or Session "
    "Manager; otherwise check that its firewall or security group lets this machine in. "
    "(error 20009)"
)


def test_the_reason_is_shown_whole_wrapped_to_the_pane(qtbot):
    """One tree row showed the first few words and cut the advice off."""
    tree = SchemaTree()
    qtbot.addWidget(tree)
    tree.resize(300, 400)
    tree.show()
    tree.show_message(LONG)
    QApplication.processEvents()

    assert tree.message() == LONG
    item = tree.topLevelItem(0)
    label = tree.itemWidget(item, 0)
    assert label.wordWrap() and item.toolTip(0) == LONG
    one_line = label.fontMetrics().height()
    narrow = tree.visualItemRect(item).height()
    assert narrow >= 4 * one_line  # every line of it has room

    tree.resize(900, 400)
    QApplication.processEvents()
    assert tree.visualItemRect(item).height() < narrow  # and no more room than it needs


def test_the_reason_goes_when_the_tables_arrive(qtbot):
    tree = SchemaTree()
    qtbot.addWidget(tree)
    tree.show_message(LONG)
    tree.set_tables([])
    assert tree.message() == "" and tree.topLevelItemCount() == 0
    tree.resize(500, 300)  # nothing left to refit
