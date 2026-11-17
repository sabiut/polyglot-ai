"""The Arduino panel: layout, choosing a board, checking, problems, monitor.

No tool is run and nothing is downloaded: the service's runs are
replaced with scripted ones, and the download confirmation with a
recorded answer.
"""

from __future__ import annotations

import asyncio
import re

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import QBoxLayout, QPushButton, QWidget  # noqa: E402

from polyglot_ai.core.arduino import diagnose  # noqa: E402
from polyglot_ai.core.arduino.boards import Language, board_for_fqbn  # noqa: E402
from polyglot_ai.core.arduino.project import DetectedProject  # noqa: E402
from polyglot_ai.core.arduino.service import DetectedBoard, StepUpdate  # noqa: E402
from polyglot_ai.ui.panels import arduino_panel as panel_mod  # noqa: E402
from polyglot_ai.ui.panels.arduino_panel import ArduinoPanel, ArduinoWindow  # noqa: E402
from polyglot_ai.ui.panels.arduino_widgets import (  # noqa: E402
    FlowLayout,
    ProblemCard,
    SerialPlot,
    StarterTile,
)

UNO = board_for_fqbn("arduino:avr:uno")
NANO = board_for_fqbn("arduino:avr:nano")
ACM0 = DetectedBoard("/dev/ttyACM0", UNO, 0x2341, 0x0043, "Arduino Uno")
USB0 = DetectedBoard("/dev/ttyUSB0", NANO, 0x1A86, 0x7523, "USB Serial")
UNKNOWN = DetectedBoard("/dev/ttyUSB1", None, 0x1234, 0x5678, "Mystery UART")

NO_PLATFORM = (
    "Error during build: Platform 'arduino:avr' not found: platform not installed\n"
    "Try running `arduino-cli core install arduino:avr`"
)


class Tools:
    can_cpp = can_micropython = can_circuitpython = pyserial_ok = True
    arduino_cli = mpremote = "/bin/tool"


@pytest.fixture
def panel(qapp, qtbot, monkeypatch):
    # The panel scans for boards when shown; these tests say what is plugged in.
    monkeypatch.setattr(ArduinoPanel, "_kick_detect", lambda self: None)
    p = ArduinoPanel()
    qtbot.addWidget(p)
    p._poll_timer.stop()
    monkeypatch.setattr(p._service, "detect_toolchains", lambda: Tools())
    monkeypatch.setattr(p._service, "user_in_dialout_group", lambda: True)
    yield p
    p._poll_timer.stop()
    p._elapsed_timer.stop()


@pytest.fixture
def sketch(tmp_path):
    folder = tmp_path / "blink"
    folder.mkdir()
    entry = folder / "blink.ino"
    entry.write_text("void setup(){}\nvoid loop(){}\n")
    return DetectedProject(entry, folder, Language.CPP)


def settle(qapp, done=lambda: True) -> None:
    """Let a resize work its way through the layouts.

    Moving the output card between the columns takes several rounds of
    layout requests; how many one ``processEvents`` gets through
    differs between platforms.
    """
    for _ in range(50):
        qapp.processEvents()
        if done():
            break
    qapp.processEvents()


def feed(panel) -> str:
    return panel._status_view.toPlainText()


def script(panel, monkeypatch, name, *runs):
    """Replace a service run with ones that yield scripted updates.

    Each run is ``(updates, after)``: ``after(service)`` sets what the
    real run would have recorded. Returns the list of call arguments.
    """
    calls: list[tuple] = []
    pending = list(runs)

    def start(*args):
        calls.append(args)
        updates, after = pending.pop(0)

        async def go():
            # As the service does: record the outcome, then report it.
            after(panel._service)
            for update in updates:
                yield update

        return go()

    monkeypatch.setattr(panel._service, name, start)
    return calls


def failed(output, board_name="Arduino Uno"):
    def after(service):
        service.last_problem = diagnose.diagnose_compile(output, 1, board_name=board_name)
        service.last_error_detail = output

    return after


def fine(service):
    return None


def stopped(service):
    service.last_cancelled = True


def run_until_idle(panel, click, qapp):
    async def go():
        click()
        for _ in range(200):
            await asyncio.sleep(0)
            qapp.processEvents()
            if not panel._busy:
                break
        # one more turn for a follow-on run queued by a timer
        for _ in range(200):
            await asyncio.sleep(0)
            qapp.processEvents()
            if not panel._busy:
                break

    asyncio.run(go())
    qapp.processEvents()


# ── layout ─────────────────────────────────────────────────────────


def test_wide_puts_the_output_beside_the_steps_and_narrow_under_them(panel, qapp):
    panel.resize(1040, 740)
    panel.show()
    qapp.processEvents()
    assert panel._side.isVisible()
    assert panel._monitor_card.parentWidget() is panel._side
    # Beside the steps, the output is as tall as the window allows.
    assert panel._monitor_card.height() > 500

    panel.resize(600, 800)
    qapp.processEvents()
    assert not panel._side.isVisible()
    assert panel._monitor_card.parentWidget() is panel._column
    assert panel._col.indexOf(panel._monitor_card) == panel_mod._MONITOR_INDEX
    assert panel._monitor_card.isVisible()

    panel.resize(1040, 740)
    qapp.processEvents()
    assert panel._monitor_card.parentWidget() is panel._side


@pytest.mark.parametrize("width", [540, 700, 900, 1040, 1400])
def test_nothing_is_wider_than_the_window(panel, qapp, sketch, width):
    panel.resize(width, 740)
    panel.show()
    for load in (False, True):
        if load:
            panel._load_project(sketch)
            panel._on_boards_detected([ACM0, USB0])
            panel._advanced_toggle.setChecked(True)
        bar = panel._scroll.horizontalScrollBar()
        settle(qapp, lambda: not bar.isVisible())
        assert not bar.isVisible()
        column = panel._column
        assert column.width() <= panel._scroll.viewport().width()
        for child in column.findChildren(QWidget):
            if child.isVisible():
                right = child.mapTo(column, child.rect().topRight()).x()
                assert right <= column.width(), (width, load, child, child.objectName())


def test_starter_tiles_stack_when_the_column_is_narrow(panel, qapp):
    panel.resize(1400, 740)
    panel.show()
    qapp.processEvents()
    assert panel._tile_row.direction() == QBoxLayout.Direction.LeftToRight
    panel.resize(600, 740)
    settle(qapp, lambda: panel._tile_row.direction() == QBoxLayout.Direction.TopToBottom)
    assert panel._tile_row.direction() == QBoxLayout.Direction.TopToBottom


def test_a_loaded_project_takes_only_the_room_it_needs(panel, qapp, sketch):
    panel.resize(600, 900)
    panel.show()
    qapp.processEvents()
    assert panel._empty_page.isVisible() and not panel._loaded_page.isVisible()
    empty_height = panel._empty_page.height()

    panel._load_project(sketch)
    qapp.processEvents()
    assert panel._loaded_page.isVisible() and not panel._empty_page.isVisible()
    # It used to be stretched to the height of the starter tiles.
    assert panel._loaded_page.height() < empty_height
    assert panel._loaded_page.height() < 150
    # The language chip is a pill, not a tall block.
    assert panel._lang_chip.height() <= 30
    assert panel._lang_chip.text() == "C++"


def test_starter_tiles_have_no_emoji_and_load_on_click(panel, qapp, monkeypatch):
    tiles = panel.findChildren(StarterTile)
    assert len(tiles) == 3
    emoji = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf\u2b00-\u2bff\ufe0f]")
    for tile in tiles:
        for label in tile.findChildren(QWidget):
            assert not emoji.search(getattr(label, "text", lambda: "")())
    loaded = []
    monkeypatch.setattr(panel, "_load_starter_inline", loaded.append)
    tiles[0].clicked.emit()
    assert len(loaded) == 1 and loaded[0].name


def test_a_tile_is_clicked_with_the_mouse_or_the_keyboard(qapp, qtbot):
    tile = StarterTile("Blink a light", "Make the LED flash.")
    qtbot.addWidget(tile)
    tile.resize(200, 60)
    clicks = []
    tile.clicked.connect(lambda: clicks.append(1))
    qtbot.mouseClick(tile, Qt.MouseButton.LeftButton)
    qtbot.keyClick(tile, Qt.Key.Key_Return)
    qtbot.keyClick(tile, Qt.Key.Key_Space)
    qtbot.mouseClick(tile, Qt.MouseButton.RightButton)
    qtbot.keyClick(tile, Qt.Key.Key_A)
    assert clicks == [1, 1, 1]


def test_the_window_can_be_made_narrow_without_cutting_things_off(qapp, qtbot):
    panel = ArduinoPanel()
    panel._poll_timer.stop()
    window = ArduinoWindow(panel)
    qtbot.addWidget(window)
    assert window.minimumWidth() <= 560
    assert window.width() >= panel_mod._TWO_COLUMN_MIN_WIDTH


def test_a_row_of_buttons_wraps_when_out_of_room(qapp, qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    row = FlowLayout(spacing=10)
    host.setLayout(row)
    buttons = [QPushButton(f"Button number {i}") for i in range(4)]
    for button in buttons:
        button.setFixedSize(100, 30)
        row.addWidget(button)
    host.resize(500, 100)
    host.show()
    qapp.processEvents()
    assert {b.y() for b in buttons} == {0}
    assert [b.x() for b in buttons] == [0, 110, 220, 330]
    assert row.heightForWidth(500) == 30

    host.resize(230, 200)
    qapp.processEvents()
    assert [(b.x(), b.y()) for b in buttons] == [(0, 0), (110, 0), (0, 40), (110, 40)]
    assert row.heightForWidth(230) == 70
    assert row.heightForWidth(100) == 150
    # A hidden widget takes no place in the row.
    buttons[1].hide()
    qapp.processEvents()
    assert row.heightForWidth(230) == 70
    assert (buttons[2].x(), buttons[2].y()) == (110, 0)
    assert row.minimumSize().width() == 100


# ── which board ────────────────────────────────────────────────────


def test_one_board_is_used_without_asking(panel, sketch):
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0])
    assert (panel._board, panel._port) == (UNO, "/dev/ttyACM0")
    assert panel._detected_combo.isHidden()
    assert "Found:" in panel._detection_label.text()
    assert panel._upload_readiness() == (True, "")


def test_two_boards_offers_the_choice_and_keeps_it_across_scans(panel, sketch):
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0, USB0])
    combo = panel._detected_combo
    assert not combo.isHidden()
    assert [combo.itemText(i) for i in range(combo.count())] == [
        "Arduino Uno  —  /dev/ttyACM0",
        f"{NANO.display_name}  —  /dev/ttyUSB0",
    ]
    assert "Found 2 boards" in panel._detection_label.text()
    assert panel._port == "/dev/ttyACM0"

    combo.setCurrentIndex(1)
    combo.activated.emit(1)
    assert (panel._board, panel._port) == (NANO, "/dev/ttyUSB0")
    assert NANO.display_name in panel._detection_label.text()

    # The scan runs every few seconds; it must not undo the choice.
    panel._on_boards_detected([ACM0, USB0])
    assert (panel._board, panel._port) == (NANO, "/dev/ttyUSB0")
    assert combo.currentIndex() == 1

    # Unplug the chosen one: fall back to what is left, and stop asking.
    panel._on_boards_detected([ACM0])
    assert (panel._board, panel._port) == (UNO, "/dev/ttyACM0")
    assert combo.isHidden()


def test_a_recognised_board_is_preferred_to_an_unknown_one(panel):
    panel._on_boards_detected([UNKNOWN, ACM0])
    assert panel._port == "/dev/ttyACM0"
    assert panel._detected_combo.itemText(0) == "Mystery UART  —  /dev/ttyUSB1"


def test_an_advanced_board_choice_survives_the_next_scan(panel, sketch):
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0])
    combo = panel._board_combo
    assert combo.itemData(0) is None and combo.currentIndex() == 0

    combo.setCurrentIndex(combo.findData(NANO.fqbn))
    assert panel._board == NANO and panel._port == "/dev/ttyACM0"
    assert "Board set under <b>Advanced</b>, not the Arduino Uno" in panel._detection_label.text()

    panel._on_boards_detected([ACM0])
    assert panel._board == NANO

    combo.setCurrentIndex(0)
    assert panel._board == UNO
    assert "Advanced" not in panel._detection_label.text()


def test_an_advanced_port_survives_the_next_scan_and_can_be_cleared(panel, sketch):
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0])
    panel._port_combo.setEditText("/dev/ttyS7")
    assert panel._port == "/dev/ttyS7"

    panel._on_boards_detected([ACM0, USB0])
    assert panel._port == "/dev/ttyS7"
    assert panel._port_combo.currentText() == "/dev/ttyS7"
    assert [panel._port_combo.itemText(i) for i in range(2)] == ["/dev/ttyACM0", "/dev/ttyUSB0"]
    assert "Port set under <b>Advanced</b>." in panel._detection_label.text()

    panel._port_combo.setEditText("")
    assert panel._port == "/dev/ttyACM0"
    panel._on_boards_detected([ACM0])
    assert panel._port_combo.currentText() == ""


def test_an_unknown_device_asks_for_the_board_and_takes_it_from_advanced(panel, sketch):
    panel._load_project(sketch)
    panel._on_boards_detected([UNKNOWN])
    assert panel._board is None and panel._port == "/dev/ttyUSB1"
    assert "We don't recognise this one" in panel._detection_label.text()
    assert panel._upload_readiness()[0] is False

    panel._board_combo.setCurrentIndex(panel._board_combo.findData(UNO.fqbn))
    assert panel._board == UNO
    assert panel._upload_readiness() == (True, "")
    assert "Using <b>Arduino Uno</b>" in panel._detection_label.text()


def test_board_and_port_both_set_by_hand_need_nothing_detected(panel, sketch):
    panel._load_project(sketch)
    panel._on_boards_detected([])
    assert "Plug in your board" in panel._detection_label.text()
    panel._board_combo.setCurrentIndex(panel._board_combo.findData(UNO.fqbn))
    assert "Plug in your board" in panel._detection_label.text()
    panel._port_combo.setEditText("/dev/ttyS0")
    assert panel._upload_readiness() == (True, "")
    assert "Using <b>Arduino Uno</b>" in panel._detection_label.text()
    assert "/dev/ttyS0" in panel._detection_label.text()


# ── check code ─────────────────────────────────────────────────────


def test_check_is_offered_as_soon_as_there_is_a_project_board_or_not(panel, sketch):
    assert not panel._check_button.isEnabled()
    panel._load_project(sketch)
    panel._on_boards_detected([])
    assert panel._check_button.isEnabled()
    assert not panel._upload_button.isEnabled()


def test_checking_builds_for_the_plugged_in_board_and_uploads_nothing(
    panel, qapp, sketch, monkeypatch
):
    panel._load_project(sketch)
    panel._on_boards_detected([USB0])
    builds = script(
        panel, monkeypatch, "compile_cpp", ([StepUpdate("Code looks good!", "ok")], fine)
    )
    uploads = script(panel, monkeypatch, "upload_cpp")
    connects = []
    monkeypatch.setattr(panel._serial_monitor, "connect_to", lambda *a: connects.append(a))

    run_until_idle(panel, panel._check_button.click, qapp)

    assert builds == [(sketch.project_dir, NANO)]
    assert uploads == []
    assert "Code looks good!" in feed(panel)
    assert "as if for" not in feed(panel)
    # A check is not an upload: no "Upload again", no monitor started.
    assert panel._upload_button.text() == "Upload to Arduino"
    qapp.processEvents()
    assert connects == []
    assert panel._problem_card.isHidden()


def test_checking_with_no_board_uses_an_uno_and_says_so(panel, qapp, sketch, monkeypatch):
    panel._load_project(sketch)
    panel._on_boards_detected([])
    builds = script(panel, monkeypatch, "compile_cpp", ([], fine))
    run_until_idle(panel, panel._check_button.click, qapp)
    assert builds == [(sketch.project_dir, UNO)]
    assert "No board is plugged in, so this checks your code as if for Arduino Uno." in feed(panel)


def test_checking_python_reads_the_file_and_builds_nothing(panel, qapp, tmp_path, monkeypatch):
    entry = tmp_path / "main.py"
    entry.write_text("print('hi')\n")
    panel._load_project(DetectedProject(entry, tmp_path, Language.MICROPYTHON))
    builds = script(panel, monkeypatch, "compile_cpp")
    checks = script(panel, monkeypatch, "check_python", ([StepUpdate("No mistakes", "ok")], fine))
    run_until_idle(panel, panel._check_button.click, qapp)
    assert checks == [(entry,)] and builds == []


def test_while_checking_the_big_button_cancels_and_the_clock_runs(panel, sketch, monkeypatch):
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0])
    # The last run's problem is taken down while the new one is going.
    panel._problem_card.show_problem(diagnose.Problem(diagnose.CODE, "m", detail="old"))
    panel._begin_job("check")
    assert panel._problem_card.isHidden()
    assert panel._upload_button.text() == "✕  Cancel check"
    assert panel._upload_button.isEnabled() and not panel._check_button.isEnabled()
    assert not panel._busy_label.isHidden()
    assert panel._busy_label.text() == "Checking… 0:00"

    panel._busy_since -= 75
    panel._tick_elapsed()
    assert panel._busy_label.text().startswith("Checking… 1:15  — the first build for a board")

    cancelled = []
    monkeypatch.setattr(
        panel._service, "cancel_current_upload", lambda: cancelled.append(1) or True
    )
    panel._upload_button.click()
    assert cancelled == [1]

    panel._end_job()
    assert panel._busy_label.isHidden() and not panel._elapsed_timer.isActive()


def test_cancel_labels_say_what_is_being_cancelled(panel, sketch):
    panel._load_project(sketch)
    for action, label in [
        ("upload", "✕  Cancel upload"),
        ("install", "✕  Cancel download"),
        ("wipe", "✕  Cancel"),
    ]:
        panel._begin_job(action)
        assert panel._upload_button.text() == label
        panel._end_job()
    panel._begin_job("install")
    assert panel._busy_label.text() == "Downloading and installing… 0:00"
    panel._end_job()


# ── problems and the way out ───────────────────────────────────────


def test_a_mistake_in_the_code_shows_the_line_and_goes_to_it(panel, qapp, sketch, monkeypatch):
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0])
    output = f"{sketch.entry_file}:9:3: error: expected ';' before 'delay'"
    script(
        panel,
        monkeypatch,
        "compile_cpp",
        ([StepUpdate("There's a mistake in blink.ino, line 9.", "fail")], failed(output)),
    )
    uploads = script(panel, monkeypatch, "upload_cpp")
    run_until_idle(panel, panel._upload_button.click, qapp)

    assert uploads == []  # a build that failed is not uploaded
    card = panel._problem_card
    assert not card.isHidden()
    assert card._heading.text() == "A mistake in your code"
    assert card._detail.text() == "blink.ino, line 9:  expected ';' before 'delay'"
    assert card.goto_button.text() == "Go to line 9" and not card.goto_button.isHidden()
    assert card.install_button.isHidden()
    assert panel._ai_help_button.text().strip() == "Explain this error"

    opened = []

    class Editor(QWidget):
        def open_file_at(self, path, line):
            opened.append((path, line))

    editor = Editor()
    monkeypatch.setattr(panel, "_find_editor_panel", lambda: editor)
    card.goto_button.click()
    assert opened == [(sketch.entry_file, 9)]


def test_go_to_line_finds_the_project_file_when_the_compiler_names_a_copy(
    panel, sketch, monkeypatch
):
    panel._load_project(sketch)
    (sketch.project_dir / "helpers.h").write_text("int x\n")
    opened = []

    class Editor(QWidget):
        def open_file_at(self, path, line):
            opened.append((path, line))

    editor = Editor()
    monkeypatch.setattr(panel, "_find_editor_panel", lambda: editor)
    for named, expect in [
        ("/tmp/arduino/sketches/ABC123/sketch/helpers.h", sketch.project_dir / "helpers.h"),
        ("/tmp/arduino/sketches/ABC123/sketch/gone.cpp", sketch.entry_file),
    ]:
        panel._service.last_problem = diagnose.Problem(diagnose.CODE, "m", file=named, line=4)
        panel._on_problem_goto()
        assert opened[-1] == (expect, 4)

    monkeypatch.setattr(panel, "_find_editor_panel", lambda: None)
    panel._on_problem_goto()
    assert "the mistake is in blink.ino, line 4" in feed(panel)


def test_a_missing_board_package_offers_to_install_it_and_asks_first(
    panel, qapp, sketch, monkeypatch
):
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0])
    builds = script(
        panel,
        monkeypatch,
        "compile_cpp",
        ([StepUpdate("support for Arduino Uno isn't installed", "fail")], failed(NO_PLATFORM)),
        ([StepUpdate("Code looks good!", "ok")], fine),
    )
    installs = script(
        panel, monkeypatch, "install_platform", ([StepUpdate("Installed arduino:avr.", "ok")], fine)
    )
    asked = []
    answers = [False, True]
    monkeypatch.setattr(
        panel,
        "_confirm_download",
        lambda title, text: asked.append((title, text)) or answers.pop(0),
    )
    sent = []
    monkeypatch.setattr(panel, "_auto_ask_send", lambda: sent.append(1))
    monkeypatch.setattr(panel_mod, "_load_auto_ask_pref", lambda: True)

    run_until_idle(panel, panel._check_button.click, qapp)

    card = panel._problem_card
    assert card._heading.text() == "Board support isn't installed"
    assert card.install_button.text() == "Install support for Arduino Uno…"
    assert card.goto_button.isHidden()
    assert "Press the Install button under Upload" in feed(panel)
    # Not something the AI can fix by reading the code — even on auto-ask.
    assert sent == [] and "Explain this error' below" not in feed(panel)

    # Declined: nothing is downloaded.
    card.install_button.click()
    assert installs == [] and not panel._busy
    title, text = asked[0]
    assert title == "Install board support?"
    assert "“arduino:avr”" in text and "downloads.arduino.cc" in text and "Arduino Uno" in text

    # Accepted: installed, then the check that was asked for is run again.
    run_until_idle(panel, card.install_button.click, qapp)
    assert installs == [("arduino:avr",)]
    assert len(builds) == 2
    assert "Installed arduino:avr." in feed(panel) and "Code looks good!" in feed(panel)
    assert card.isHidden()


def test_a_third_party_package_names_where_it_comes_from(panel, sketch, monkeypatch):
    panel._load_project(sketch)
    panel._service.last_problem = diagnose.Problem(diagnose.PLATFORM, "m", platform="esp32:esp32")
    asked = []
    monkeypatch.setattr(panel, "_confirm_download", lambda t, text: asked.append(text) or False)
    panel._on_problem_install()
    assert "espressif.github.io" in asked[0]


def test_a_missing_library_offers_to_install_it(panel, qapp, sketch, monkeypatch):
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0])
    output = (
        f"{sketch.entry_file}:1:10: fatal error: Adafruit_NeoPixel.h: No such file or directory"
    )
    script(panel, monkeypatch, "compile_cpp", ([], failed(output)), ([], fine))
    uploads = script(panel, monkeypatch, "upload_cpp", ([StepUpdate("Done!", "ok")], fine))
    installs = script(panel, monkeypatch, "install_library", ([], fine))
    asked = []
    monkeypatch.setattr(panel, "_confirm_download", lambda t, text: asked.append((t, text)) or True)
    monkeypatch.setattr(panel._serial_monitor, "connect_to", lambda *a: None)

    run_until_idle(panel, panel._upload_button.click, qapp)
    card = panel._problem_card
    assert card._heading.text() == "A library is missing"
    assert card.install_button.text() == "Install the library for Adafruit_NeoPixel.h…"
    assert card.goto_button.text() == "Go to line 1"

    run_until_idle(panel, card.install_button.click, qapp)
    assert asked[0][0] == "Install the library?"
    assert "“Adafruit_NeoPixel” or “Adafruit NeoPixel”" in asked[0][1]
    assert installs == [("Adafruit_NeoPixel.h",)]
    # It was an upload that was interrupted, so the upload is what resumes.
    assert len(uploads) == 1


def test_a_failed_install_does_not_carry_on(panel, qapp, sketch, monkeypatch):
    panel._load_project(sketch)
    panel._last_action = "check"
    panel._service.last_problem = diagnose.Problem(diagnose.PLATFORM, "m", platform="arduino:avr")
    builds = script(panel, monkeypatch, "compile_cpp")

    def offline(service):
        service.last_error_detail = "no such host"

    script(panel, monkeypatch, "install_platform", ([StepUpdate("Couldn't", "fail")], offline))
    monkeypatch.setattr(panel, "_confirm_download", lambda t, text: True)
    run_until_idle(panel, panel._on_problem_install, qapp)
    assert builds == []

    panel._service.last_problem = diagnose.Problem(diagnose.PLATFORM, "m", platform="arduino:avr")
    script(panel, monkeypatch, "install_platform", ([], stopped))
    run_until_idle(panel, panel._on_problem_install, qapp)
    assert builds == []


def test_stopping_the_build_does_not_go_on_to_upload_or_celebrate(panel, qapp, sketch, monkeypatch):
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0])
    script(panel, monkeypatch, "compile_cpp", ([StepUpdate("Cancelled.", "fail")], stopped))
    uploads = script(panel, monkeypatch, "upload_cpp")
    connects = []
    monkeypatch.setattr(panel._serial_monitor, "connect_to", lambda *a: connects.append(a))

    run_until_idle(panel, panel._upload_button.click, qapp)

    assert uploads == []
    assert panel._upload_button.text() == "Upload to Arduino"
    assert "press Upload again to flash" not in feed(panel)
    assert panel._problem_card.isHidden()


def test_the_upload_goes_to_the_board_the_build_was_made_for(panel, qapp, sketch, monkeypatch):
    """Whatever is selected by the time the build finishes."""
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0, USB0])
    assert (panel._board, panel._port) == (UNO, "/dev/ttyACM0")

    def selection_changes_during_the_build(service):
        # The Uno drops off the scan (it does, when it resets), leaving
        # the Nano; and the project is swapped for another one.
        panel._on_boards_detected([USB0])
        panel._chosen_port = "/dev/ttyUSB0"
        panel._resolve_target()
        other = sketch.project_dir.parent / "other"
        other.mkdir()
        (other / "other.ino").write_text("void setup(){}\nvoid loop(){}\n")
        panel._project = DetectedProject(other / "other.ino", other, Language.CPP)
        assert (panel._board, panel._port) == (NANO, "/dev/ttyUSB0")

    builds = script(panel, monkeypatch, "compile_cpp", ([], selection_changes_during_the_build))
    uploads = script(panel, monkeypatch, "upload_cpp", ([StepUpdate("Done!", "ok")], fine))
    connects = []
    monkeypatch.setattr(panel._serial_monitor, "connect_to", lambda *a: connects.append(a))
    monkeypatch.setattr(panel_mod.QTimer, "singleShot", _run_now(panel_mod.QTimer.singleShot))

    run_until_idle(panel, panel._upload_button.click, qapp)

    assert builds == [(sketch.project_dir, UNO)]
    assert uploads == [(sketch.project_dir, UNO, "/dev/ttyACM0")]
    # …and its output is read from where it was sent.
    assert connects == [("/dev/ttyACM0", 115200)]


def _run_now(real):
    """``QTimer.singleShot`` without the wait, for the 800 ms reconnect."""

    def single_shot(ms, callback):
        real(0, callback)

    return staticmethod(single_shot)


def test_an_upload_keeps_the_project_it_started_with(panel, qapp, sketch, monkeypatch):
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0])
    builds = script(panel, monkeypatch, "compile_cpp", ([], stopped))

    async def go():
        panel._upload_button.click()
        panel._project = None  # swapped away before the build gets going
        for _ in range(50):
            await asyncio.sleep(0)

    asyncio.run(go())
    assert builds == [(sketch.project_dir, UNO)]
    assert "Something went wrong" not in feed(panel)


def test_micropython_and_circuitpython_uploads_use_what_they_started_with(
    panel, qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(panel._serial_monitor, "connect_to", lambda *a: None)
    pico = next(b for b in panel_mod.boards_for_language(Language.MICROPYTHON))
    entry = tmp_path / "main.py"
    entry.write_text("print('hi')\n")
    panel._load_project(DetectedProject(entry, tmp_path, Language.MICROPYTHON))
    panel._on_boards_detected([DetectedBoard("/dev/ttyACM3", pico, 1, 2, "Pico")])
    sent = script(panel, monkeypatch, "upload_micropython", ([], fine))
    run_until_idle(panel, panel._upload_button.click, qapp)
    assert sent == [(entry, "/dev/ttyACM3")]

    cp = next(b for b in panel_mod.boards_for_language(Language.CIRCUITPYTHON))
    code = tmp_path / "code.py"
    code.write_text("print('hi')\n")
    drive = tmp_path / "CIRCUITPY"
    drive.mkdir()
    panel._load_project(DetectedProject(code, tmp_path, Language.CIRCUITPYTHON))
    panel._on_boards_detected([DetectedBoard("/dev/ttyACM4", cp, 1, 2, "CP")])
    panel._cp_drive = drive
    panel._refresh_upload_button()
    copied = script(panel, monkeypatch, "upload_circuitpython", ([], fine))
    run_until_idle(panel, panel._upload_button.click, qapp)
    assert copied == [(code, drive)]


def test_the_board_cannot_be_changed_while_a_run_is_using_it(panel, sketch):
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0, USB0])
    choosers = (
        panel._detected_combo,
        panel._board_combo,
        panel._port_combo,
        panel._drive_pick_btn,
    )
    assert all(chooser.isEnabled() for chooser in choosers)
    panel._begin_job("upload")
    assert not any(chooser.isEnabled() for chooser in choosers)
    panel._end_job()
    panel._refresh_upload_button()
    assert all(chooser.isEnabled() for chooser in choosers)


def test_a_board_that_drops_off_during_an_upload_is_still_the_chosen_one(panel, sketch):
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0, USB0])
    panel._detected_combo.setCurrentIndex(1)
    panel._detected_combo.activated.emit(1)
    panel._begin_job("upload")
    panel._on_boards_detected([ACM0])  # the Nano resets for the upload…
    panel._on_boards_detected([ACM0, USB0])  # …and comes back
    panel._end_job()
    assert (panel._board, panel._port) == (NANO, "/dev/ttyUSB0")


def test_a_check_keeps_the_project_it_started_with(panel, qapp, sketch, monkeypatch):
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0])
    builds = script(panel, monkeypatch, "compile_cpp", ([], fine))

    async def go():
        panel._check_button.click()
        panel._project = None  # swapped away before the check gets going
        for _ in range(50):
            await asyncio.sleep(0)

    asyncio.run(go())
    assert builds == [(sketch.project_dir, UNO)]


def test_a_good_upload_still_celebrates_and_a_new_run_clears_the_old_problem(
    panel, qapp, sketch, monkeypatch
):
    panel._load_project(sketch)
    panel._on_boards_detected([ACM0])
    output = f"{sketch.entry_file}:2:1: error: 'x' was not declared in this scope"
    script(panel, monkeypatch, "compile_cpp", ([], failed(output)), ([], fine))
    uploads = script(panel, monkeypatch, "upload_cpp", ([StepUpdate("Done!", "ok")], fine))
    monkeypatch.setattr(panel._serial_monitor, "connect_to", lambda *a: None)

    run_until_idle(panel, panel._upload_button.click, qapp)
    assert not panel._problem_card.isHidden()
    panel._on_tool_output("stale line")

    run_until_idle(panel, panel._upload_button.click, qapp)
    assert uploads == [(sketch.project_dir, UNO, "/dev/ttyACM0")]
    assert panel._problem_card.isHidden()
    assert panel._upload_button.text() == "Upload again ↻"
    assert "stale line" not in panel._raw_view.toPlainText()
    assert panel._ai_help_button.text().strip() == "Ask AI for help"


def test_the_problem_card_shows_only_what_applies(qapp, qtbot):
    card = ProblemCard()
    qtbot.addWidget(card)
    assert card.isHidden()
    card.show_problem(diagnose.Problem(diagnose.NO_ANSWER, "m", detail="not in sync"))
    assert not card.isHidden()
    assert card._heading.text() == "The board didn't answer"
    assert card._detail.text() == "not in sync"
    assert card.goto_button.isHidden() and card.install_button.isHidden()

    card.show_problem(diagnose.Problem(diagnose.PLATFORM, "m", platform="a:b"))
    assert card.install_button.text() == "Install board support…"
    assert card._detail.isHidden()

    card.show_problem(diagnose.Problem("something-new", "m"))
    assert card._heading.text() == "Something went wrong"
    card.show_problem(None)
    assert card.isHidden()


def test_what_the_tools_printed_is_kept_folded_away(panel, qapp):
    panel.show()
    panel._on_tool_output("Compiling sketch…")
    panel._on_tool_output("Linking everything together…")
    assert panel._raw_view.isHidden()
    panel._details_btn.click()
    assert not panel._raw_view.isHidden()
    assert panel._details_btn.text() == "▾ Hide what the tools printed"
    assert panel._raw_view.toPlainText() == "Compiling sketch…\nLinking everything together…"
    panel._details_btn.click()
    assert panel._raw_view.isHidden()
    assert panel._details_btn.text() == "▸ Show what the tools printed"


def test_the_tools_output_reaches_the_panel_from_another_thread(panel, qapp, qtbot):
    import threading

    worker = threading.Thread(target=lambda: panel._service.on_output("from the reader thread"))
    worker.start()
    worker.join()
    qtbot.waitUntil(lambda: "from the reader thread" in panel._raw_view.toPlainText(), timeout=2000)


def test_the_ai_is_told_what_the_panel_worked_out(panel, sketch):
    panel._load_project(sketch)
    panel._last_action = "check"
    panel._service.last_error_detail = "raw output"
    panel._service.last_problem = diagnose.Problem(diagnose.CODE, "There's a mistake in a.ino.")
    message = panel._build_ask_ai_message()
    assert message.startswith("My Arduino check failed.")
    assert "The panel's reading of it: There's a mistake in a.ino." in message
    assert "raw output" in message
    panel._last_action = "upload"
    panel._service.last_problem = None
    message = panel._build_ask_ai_message()
    assert message.startswith("My Arduino upload failed.") and "reading of it" not in message


# ── serial monitor ─────────────────────────────────────────────────


def lines(panel) -> list[str]:
    return panel._serial_view.toPlainText().splitlines()


def test_sending_needs_a_connection(panel):
    assert not panel._serial_send_edit.isEnabled() and not panel._serial_send_btn.isEnabled()
    assert "Start the monitor" in panel._serial_send_edit.placeholderText()
    panel._on_serial_connected("/dev/ttyACM0", 115200)
    assert panel._serial_send_edit.isEnabled() and panel._serial_send_btn.isEnabled()
    assert panel._serial_connect_btn.text() == "■  Stop monitor"
    panel._on_serial_disconnected()
    assert not panel._serial_send_edit.isEnabled()
    assert lines(panel) == ["── connected to /dev/ttyACM0 @ 115200 ──", "── disconnected ──"]


def test_a_line_is_sent_with_the_chosen_ending_and_shown_in_the_output(panel, monkeypatch):
    sent = []
    monkeypatch.setattr(
        panel._serial_monitor, "send", lambda text, end: sent.append((text, end)) or True
    )
    panel._on_serial_connected("/dev/ttyACM0", 115200)
    panel._serial_send_edit.setText("led on")
    panel._serial_send_edit.returnPressed.emit()
    assert sent == [("led on", "\n")]
    assert lines(panel)[-1] == "» led on"
    assert panel._serial_send_edit.text() == ""

    panel._serial_ending_combo.setCurrentIndex(
        panel._serial_ending_combo.findText("Both (CR + LF)")
    )
    panel._serial_send_edit.setText("AT")
    panel._serial_send_btn.click()
    assert sent[-1] == ("AT", "\r\n")

    # Nothing typed still sends the ending — a bare Enter, for a prompt.
    panel._serial_send_btn.click()
    assert sent[-1] == ("", "\r\n")
    # …but nothing at all is not sent.
    panel._serial_ending_combo.setCurrentIndex(panel._serial_ending_combo.findText("Nothing"))
    panel._serial_send_btn.click()
    assert len(sent) == 3


def test_text_that_could_not_be_sent_stays_in_the_box(panel, monkeypatch):
    monkeypatch.setattr(panel._serial_monitor, "send", lambda text, end: False)
    panel._on_serial_connected("/dev/ttyACM0", 115200)
    panel._serial_send_edit.setText("keep me")
    panel._on_serial_send()
    assert panel._serial_send_edit.text() == "keep me"
    assert "» keep me" not in lines(panel)


def test_pausing_holds_the_display_and_resume_shows_what_was_missed(panel):
    panel._on_serial_line("one")
    panel._serial_pause_btn.click()
    assert panel._serial_pause_btn.text() == "Resume"
    panel._on_serial_line("two")
    panel._on_serial_line("three")
    assert lines(panel) == ["one"]
    assert panel._serial_pause_btn.text() == "Resume (2 new)"
    panel._serial_pause_btn.click()
    assert lines(panel) == ["one", "two", "three"]
    assert panel._serial_pause_btn.text() == "Pause"
    panel._on_serial_line("four")
    assert lines(panel)[-1] == "four"


def test_the_pause_buffer_is_bounded(panel, monkeypatch):
    monkeypatch.setattr(panel_mod, "_PAUSE_BUFFER_LINES", 3)
    panel._serial_pause_btn.click()
    panel._serial_pause_btn.click()  # resume: the buffer is rebuilt at the new size
    panel._serial_pause_btn.click()
    for i in range(10):
        panel._on_serial_line(str(i))
    panel._serial_pause_btn.click()
    assert lines(panel) == ["7", "8", "9"]


def test_the_time_can_be_put_on_each_line(panel):
    panel._on_serial_line("plain")
    panel._serial_stamp_box.setChecked(True)
    panel._on_serial_line("stamped")
    assert lines(panel)[0] == "plain"
    assert re.fullmatch(r"\d\d:\d\d:\d\d\.\d{3}  stamped", lines(panel)[1])


def test_saving_writes_what_is_shown(panel, sketch, tmp_path, monkeypatch):
    panel._load_project(sketch)
    asked = []
    target = tmp_path / "out.txt"
    monkeypatch.setattr(
        panel, "_ask_save_path", lambda suggested: asked.append(suggested) or target
    )

    panel._on_serial_save()
    assert asked == [] and "nothing in the monitor to save" in feed(panel)

    panel._on_serial_line("temp:21.5")
    panel._on_serial_line("ready")
    panel._on_serial_save()
    assert target.read_text() == "temp:21.5\nready\n"
    assert asked[0].parent == sketch.project_dir
    assert re.fullmatch(r"serial-\d{8}-\d{6}\.txt", asked[0].name)
    assert "Saved the monitor's output" in feed(panel)

    monkeypatch.setattr(panel, "_ask_save_path", lambda suggested: None)
    target.write_text("untouched")
    panel._on_serial_save()
    assert target.read_text() == "untouched"

    monkeypatch.setattr(
        panel, "_ask_save_path", lambda suggested: tmp_path / "no" / "dir" / "x.txt"
    )
    panel._on_serial_save()
    assert "Couldn't save to" in feed(panel)


def test_numbers_go_to_the_graph_and_text_does_not(panel, qapp):
    panel.show()
    plot = panel._serial_plot
    assert plot.isHidden()
    panel._on_serial_line("temp:21.5,humidity:40")
    panel._on_serial_line("Sensor ready")
    panel._on_serial_line("temp:22,humidity:41")
    assert plot.series_names() == ["temp", "humidity"]
    assert plot.readout() == (0, {"temp": 22.0, "humidity": 41.0})
    assert len(lines(panel)) == 3

    panel._serial_plot_btn.click()
    assert not plot.isHidden()
    # Timestamps are for reading; the graph gets the bare numbers.
    panel._serial_stamp_box.setChecked(True)
    panel._on_serial_line("temp:23,humidity:42")
    assert plot.readout()[1] == {"temp": 23.0, "humidity": 42.0}
    panel._serial_plot_btn.click()
    assert plot.isHidden()


def test_clear_empties_the_output_the_graph_and_what_was_held(panel):
    panel._on_serial_line("1")
    panel._serial_pause_btn.click()
    panel._on_serial_line("2")
    panel._on_serial_clear()
    assert lines(panel) == [] and panel._serial_plot.series_names() == []
    assert panel._serial_pause_btn.text() == "Resume"
    panel._serial_pause_btn.click()
    assert lines(panel) == []


# ── the graph ──────────────────────────────────────────────────────


@pytest.fixture
def plot(qapp, qtbot):
    widget = SerialPlot()
    qtbot.addWidget(widget)
    widget.resize(400, 220)
    return widget


def test_a_series_keeps_its_colour_whatever_else_comes_and_goes(plot):
    plot.add_sample([("temp", 20.0), ("humidity", 40.0)])
    temp, humidity = plot.colour_for("temp"), plot.colour_for("humidity")
    assert temp != humidity
    plot.add_sample([("humidity", 41.0)])
    plot.add_sample([("pressure", 1000.0), ("temp", 21.0)])
    assert (plot.colour_for("temp"), plot.colour_for("humidity")) == (temp, humidity)
    assert plot.colour_for("pressure") not in (temp, humidity)
    assert plot.series_names() == ["temp", "humidity", "pressure"]


def test_unnamed_values_are_named_by_position(plot):
    plot.add_sample([("", 5.0)])
    assert plot.series_names() == ["value"]
    plot.clear()
    plot.add_sample([("", 1.0), ("", 2.0)])
    assert plot.series_names() == ["value 1", "value 2"]


def test_only_the_last_readings_are_kept(plot):
    from polyglot_ai.ui.panels.arduino_widgets import PLOT_WINDOW

    plot.add_sample([("old", 1.0)])
    for i in range(PLOT_WINDOW):
        plot.add_sample([("new", float(i))])
    assert plot.series_names() == ["new"]
    assert plot.readout() == (0, {"new": float(PLOT_WINDOW - 1)})


@pytest.mark.parametrize(
    "samples",
    [
        [1e20],  # adding 1 to this changes nothing
        [-1e20],
        [1e20, 1e20],
        [1.7976931348623157e308],  # the largest float there is
        [-1.7976931348623157e308, 1.7976931348623157e308],
        [1e308, -1e308, 0.0],
        [5e-324],  # and the smallest
        [0.0],
    ],
)
def test_any_number_a_board_can_print_gets_a_scale_and_is_drawn(plot, qapp, samples):
    import math

    for value in samples:
        plot.add_sample([("a", value)])
    low, high = plot.value_range()
    assert math.isfinite(low) and math.isfinite(high)
    assert low < high
    for value in samples:
        share = plot.height_share(value)
        assert math.isfinite(share) and 0.0 <= share <= 1.0
    plot.show()
    plot._hover_x = 200.0
    assert not plot.grab().isNull()


def test_the_scale_fits_the_data_with_a_little_room(plot):
    assert plot.value_range() == (0.0, 1.0)
    plot.add_sample([("a", 5.0)])
    assert plot.value_range() == (4.0, 6.0)
    plot.add_sample([("a", 15.0), ("b", -5.0)])
    low, high = plot.value_range()
    assert low == pytest.approx(-6.2) and high == pytest.approx(16.2)
    assert plot.height_share(-6.2) == pytest.approx(0.0)
    assert plot.height_share(16.2) == pytest.approx(1.0)
    assert plot.height_share(5.0) == pytest.approx(0.5)


def test_pointing_at_the_graph_reads_that_moment(plot, qapp):
    for i in range(11):
        plot.add_sample([("a", float(i))])
    rect = plot._plot_rect()

    def point(x):
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(x, 100),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        plot.mouseMoveEvent(event)

    point(rect.left())
    assert plot.readout() == (10, {"a": 0.0})
    point(rect.left() + rect.width() * 0.5)
    assert plot.readout() == (5, {"a": 5.0})
    point(rect.right() + 500)
    assert plot.readout() == (0, {"a": 10.0})
    point(-50)
    assert plot.readout() == (10, {"a": 0.0})
    plot.leaveEvent(None)
    assert plot.readout() == (0, {"a": 10.0})


def test_it_draws_empty_with_one_reading_and_with_many(plot, qapp):
    plot.show()
    plot.grab()
    plot.add_sample([("a", 1.0)])
    plot.grab()
    for i in range(50):
        plot.add_sample([("a", float(i)), ("b", float(-i)), ("", 3.0)])
    plot._hover_x = 200.0
    image = plot.grab().toImage()
    colours = {image.pixelColor(x, y).name() for x in range(0, 400, 2) for y in range(30, 200, 2)}
    assert plot.colour_for("a") in colours and plot.colour_for("b") in colours
