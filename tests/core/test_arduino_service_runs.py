"""The Arduino service's runs: what it reports, records and asks the tools.

``arduino-cli`` and ``mpremote`` are never started here — ``_run`` is
replaced with a script of results — except in the tests of ``_run``
itself, which start this Python.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time

import pytest

from polyglot_ai.core.arduino import diagnose
from polyglot_ai.core.arduino import service as service_mod
from polyglot_ai.core.arduino.boards import board_for_fqbn
from polyglot_ai.core.arduino.service import ArduinoService

UNO = board_for_fqbn("arduino:avr:uno")
NO_PLATFORM = (
    "Error during build: Platform 'arduino:avr' not found: platform not installed\n"
    "Try running `arduino-cli core install arduino:avr`"
)


class Tools:
    """Stands in for ``_run``: records each command, answers from a script."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[float] = []

    async def __call__(self, *argv, timeout=180.0, service=None):
        self.calls.append(argv)
        self.timeouts.append(timeout)
        result = self.results.pop(0) if self.results else (0, "", "")
        if result[0] == -1 and service is not None:
            service._cancel_requested = True
        return result


@pytest.fixture
def tools(monkeypatch):
    def install(*results):
        fake = Tools(*results)
        monkeypatch.setattr(service_mod, "_run", fake)
        monkeypatch.setattr(service_mod, "find_executable", lambda name: f"/bin/{name}")
        return fake

    return install


def collect(updates):
    async def go():
        return [u async for u in updates]

    return asyncio.run(go())


@pytest.fixture
def sketch(tmp_path):
    folder = tmp_path / "blink"
    folder.mkdir()
    (folder / "blink.ino").write_text("void setup(){}\nvoid loop(){}\n")
    return folder


# ── compile ────────────────────────────────────────────────────────


def test_a_failed_build_reports_the_diagnosis_not_a_shrug(tools, sketch):
    out = f"{sketch}/blink.ino:9:3: error: expected ';' before 'delay'"
    tools((1, "", out))
    service = ArduinoService()
    updates = collect(service.compile_cpp(sketch, UNO))

    assert updates[-1].kind == "fail"
    assert updates[-1].message.startswith("There's a mistake in blink.ino, line 9.")
    assert service.last_problem.kind == diagnose.CODE
    assert service.last_problem.line == 9
    assert service.last_error_detail == out
    assert not service.last_cancelled


def test_a_missing_board_package_is_reported_as_that(tools, sketch):
    tools((1, "", NO_PLATFORM))
    service = ArduinoService()
    updates = collect(service.compile_cpp(sketch, UNO))
    assert service.last_problem.platform == "arduino:avr"
    assert "Arduino Uno isn't installed" in updates[-1].message


def test_the_error_is_found_when_the_tool_prints_it_on_stdout(tools, sketch):
    tools((1, NO_PLATFORM, "  \n"))
    service = ArduinoService()
    collect(service.compile_cpp(sketch, UNO))
    assert service.last_problem.kind == diagnose.PLATFORM


def test_a_failure_that_printed_nothing_still_counts_as_a_failure(tools, sketch):
    tools((1, "", ""))
    service = ArduinoService()
    collect(service.compile_cpp(sketch, UNO))
    # The panel reads "there is error detail" as "the run failed".
    assert service.last_error_detail


def test_a_build_gets_longer_than_an_upload_before_it_is_stopped(tools, sketch):
    fake = tools((0, "", ""), (0, "", ""))
    service = ArduinoService()
    collect(service.compile_cpp(sketch, UNO))
    collect(service.upload_cpp(sketch, UNO, "/dev/ttyACM0"))
    assert fake.timeouts == [service_mod.BUILD_TIMEOUT_SECONDS, 180.0]
    assert service_mod.BUILD_TIMEOUT_SECONDS >= 600


def test_a_build_that_ran_out_of_time_is_not_called_a_mistake(tools, sketch):
    tools((124, "", "timed out"))
    service = ArduinoService()
    updates = collect(service.compile_cpp(sketch, UNO))
    assert service.last_problem.kind == diagnose.TIMEOUT
    assert "took too long" in updates[-1].message


def test_success_says_how_much_of_the_board_is_used(tools, sketch):
    tools((0, "Sketch uses 924 bytes (2%) of program storage space. Maximum is 32256 bytes.", ""))
    service = ArduinoService()
    updates = collect(service.compile_cpp(sketch, UNO))
    assert updates[-1].kind == "ok"
    assert "2% of the board's program space" in updates[-1].message
    assert service.last_problem is None and service.last_error_detail == ""


def test_a_new_run_forgets_the_last_failure(tools, sketch):
    tools((1, "", NO_PLATFORM), (0, "", ""))
    service = ArduinoService()
    collect(service.compile_cpp(sketch, UNO))
    collect(service.compile_cpp(sketch, UNO))
    assert service.last_problem is None and service.last_error_detail == ""


def test_stopping_a_build_is_recorded_as_stopped_not_as_failed(tools, sketch):
    tools((-1, "", "Cancelled by user."))
    service = ArduinoService()
    updates = collect(service.compile_cpp(sketch, UNO))
    assert updates[-1].message == "Cancelled."
    assert service.last_cancelled
    assert service.last_error_detail == "" and service.last_problem is None


# ── upload ─────────────────────────────────────────────────────────


def test_a_failed_upload_says_why(tools, sketch):
    tools((1, "", "avrdude: stk500_recv(): programmer is not responding"))
    service = ArduinoService()
    updates = collect(service.upload_cpp(sketch, UNO, "/dev/ttyACM0"))
    assert service.last_problem.kind == diagnose.NO_ANSWER
    assert updates[-1].message.startswith("Arduino Uno didn't answer.")


def test_success_messages_carry_no_emoji(tools, sketch):
    tools((0, "", ""))
    updates = collect(ArduinoService().upload_cpp(sketch, UNO, "/dev/ttyACM0"))
    assert updates[-1].message == "Done! Your code is on the board."


def test_python_with_a_syntax_mistake_is_never_sent_to_the_board(tools, tmp_path):
    script = tmp_path / "main.py"
    script.write_text("while True\n    pass\n")
    fake = tools()
    service = ArduinoService()
    updates = collect(service.upload_micropython(script, "/dev/ttyACM0"))
    assert fake.calls == []
    assert updates[-1].kind == "fail" and "main.py, line 1" in updates[-1].message
    assert service.last_problem.line == 1


def test_circuitpython_with_a_syntax_mistake_is_not_copied(tools, tmp_path):
    script = tmp_path / "code.py"
    script.write_text("def broken(:\n")
    drive = tmp_path / "CIRCUITPY"
    drive.mkdir()
    tools()
    service = ArduinoService()
    collect(service.upload_circuitpython(script, drive))
    assert not (drive / "code.py").exists()
    assert service.last_problem.kind == diagnose.CODE


def test_a_micropython_copy_that_fails_for_no_known_reason_keeps_its_own_advice(tools, tmp_path):
    script = tmp_path / "main.py"
    script.write_text("print('hi')\n")
    tools((1, "", "mpremote: something unexpected"))
    service = ArduinoService()
    updates = collect(service.upload_micropython(script, "/dev/ttyACM0"))
    assert "Is the board running MicroPython" in updates[-1].message
    assert service.last_error_detail == "mpremote: something unexpected"


# ── check python ───────────────────────────────────────────────────


def test_checking_python_needs_no_tool_and_no_board(tools, tmp_path):
    script = tmp_path / "main.py"
    script.write_text("import machine\nprint('ok')\n")
    fake = tools()
    service = ArduinoService()
    updates = collect(service.check_python(script))
    assert fake.calls == []
    assert updates[-1].kind == "ok"

    script.write_text("print('oops'\n")
    updates = collect(service.check_python(script))
    assert updates[-1].kind == "fail" and service.last_problem.kind == diagnose.CODE


# ── installing ─────────────────────────────────────────────────────


def test_installing_arduinos_own_package_refreshes_the_list_then_installs(tools):
    fake = tools((0, "", ""), (0, "", ""))
    service = ArduinoService()
    updates = collect(service.install_platform("arduino:avr"))
    assert fake.calls == [
        ("/bin/arduino-cli", "core", "update-index"),
        ("/bin/arduino-cli", "core", "install", "arduino:avr"),
    ]
    assert fake.timeouts[1] == service_mod.INSTALL_TIMEOUT_SECONDS
    assert updates[-1].kind == "ok" and "arduino:avr" in updates[-1].message
    assert service.last_error_detail == ""


def test_a_third_party_package_is_installed_from_its_own_index(tools):
    fake = tools((0, "", ""), (0, "", ""))
    collect(ArduinoService().install_platform("esp32:esp32"))
    url = "https://espressif.github.io/arduino-esp32/package_esp32_index.json"
    assert fake.calls[0][-2:] == ("--additional-urls", url)
    assert fake.calls[1][2:] == ("install", "esp32:esp32", "--additional-urls", url)


def test_no_install_is_attempted_when_the_list_cannot_be_fetched(tools):
    fake = tools((1, "", "dial tcp: no such host"))
    service = ArduinoService()
    updates = collect(service.install_platform("arduino:avr"))
    assert len(fake.calls) == 1
    assert "Is this computer online?" in updates[-1].message
    assert service.last_error_detail == "dial tcp: no such host"


def test_a_failed_install_and_a_stopped_one(tools):
    tools((0, "", ""), (1, "", "checksum mismatch"))
    service = ArduinoService()
    updates = collect(service.install_platform("arduino:avr"))
    assert updates[-1].kind == "fail" and service.last_error_detail == "checksum mismatch"

    tools((0, "", ""), (-1, "", ""))
    service = ArduinoService()
    collect(service.install_platform("arduino:avr"))
    assert service.last_cancelled and service.last_error_detail == ""


def test_a_library_is_looked_for_under_each_likely_name_until_one_installs(tools):
    fake = tools((0, "", ""), (1, "", "Library 'Adafruit_NeoPixel' not found"), (0, "", ""))
    service = ArduinoService()
    updates = collect(service.install_library("Adafruit_NeoPixel.h"))
    assert [call[1:] for call in fake.calls] == [
        ("lib", "update-index"),
        ("lib", "install", "Adafruit_NeoPixel"),
        ("lib", "install", "Adafruit NeoPixel"),
    ]
    assert updates[-1].message == "Installed the Adafruit NeoPixel library."
    assert service.last_error_detail == ""


def test_a_library_nobody_has_heard_of(tools):
    tools((0, "", ""), (1, "", "not found"))
    service = ArduinoService()
    updates = collect(service.install_library("Mystery.h"))
    assert updates[-1].kind == "fail" and "Mystery.h" in updates[-1].message
    assert service.last_problem.kind == diagnose.LIBRARY
    assert service.last_error_detail == "not found"


def test_installing_without_the_cli_says_so(monkeypatch):
    monkeypatch.setattr(service_mod, "find_executable", lambda name: None)
    service = ArduinoService()
    assert collect(service.install_platform("arduino:avr"))[-1].kind == "fail"
    assert collect(service.install_library("Servo.h"))[-1].kind == "fail"


# ── the runner itself ──────────────────────────────────────────────


def run(*argv, **kwargs):
    return asyncio.run(service_mod._run(*argv, **kwargs))


def test_output_is_handed_over_line_by_line_while_the_tool_runs():
    service = ArduinoService()
    seen: list[tuple[str, float]] = []
    service.on_output = lambda line: seen.append((line, time.monotonic()))
    code = (
        "import sys, time\n"
        "print('one', flush=True)\n"
        "time.sleep(0.4)\n"
        "print('two', file=sys.stderr, flush=True)\n"
    )
    rc, out, err = run(sys.executable, "-c", code, service=service)
    assert (rc, out, err) == (0, "one\n", "two\n")
    assert [line for line, _ in seen] == ["one", "two"]
    # "one" arrived while the tool was still running, not at the end.
    assert seen[1][1] - seen[0][1] > 0.25


def test_a_listener_that_raises_does_not_lose_the_output():
    service = ArduinoService()
    service.on_output = lambda line: 1 / 0
    rc, out, _ = run(sys.executable, "-c", "print('a'); print('b')", service=service)
    assert (rc, out) == (0, "a\nb\n")


def test_a_lot_of_output_on_both_streams_does_not_stall():
    code = (
        "import sys\n"
        "for i in range(20000):\n"
        "    print('o' * 60)\n"
        "    print('e' * 60, file=sys.stderr)\n"
    )
    rc, out, err = run(sys.executable, "-c", code, timeout=30)
    assert rc == 0 and out.count("\n") == 20000 and err.count("\n") == 20000


def test_the_exit_status_and_a_missing_program():
    assert run(sys.executable, "-c", "raise SystemExit(3)")[0] == 3
    rc, _, err = run("/no/such/program-anywhere")
    assert rc == 127 and err


def test_a_tool_that_runs_out_of_time_is_stopped_and_reported_as_124():
    started = time.monotonic()
    rc, out, err = run(
        sys.executable, "-c", "import time; print('hi', flush=True); time.sleep(30)", timeout=0.5
    )
    assert rc == 124 and out == "hi\n" and "timed out" in err
    assert time.monotonic() - started < 10


def test_cancelling_stops_the_tool_and_is_reported_as_minus_one():
    service = ArduinoService()
    started = threading.Event()
    service.on_output = lambda line: started.set()

    def cancel_when_running():
        assert started.wait(10)
        assert service.cancel_current_upload()

    threading.Thread(target=cancel_when_running, daemon=True).start()
    began = time.monotonic()
    rc, out, err = run(
        sys.executable,
        "-c",
        "import time; print('go', flush=True); time.sleep(30)",
        service=service,
    )
    assert rc == -1 and "Cancelled by user." in err
    assert time.monotonic() - began < 10
    assert service._current_proc is None


def test_a_tool_that_ignores_being_asked_to_stop_is_killed(monkeypatch):
    monkeypatch.setattr(service_mod, "CANCEL_GRACE_SECONDS", 0.3)
    service = ArduinoService()
    started = threading.Event()
    service.on_output = lambda line: started.set()

    def cancel_when_running():
        assert started.wait(10)
        service.cancel_current_upload()

    threading.Thread(target=cancel_when_running, daemon=True).start()
    began = time.monotonic()
    code = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('go', flush=True)\n"
        "time.sleep(30)\n"
    )
    rc, _, _ = run(sys.executable, "-c", code, service=service)
    assert rc == -1
    assert time.monotonic() - began < 10


def test_nothing_to_cancel_when_nothing_is_running():
    assert ArduinoService().cancel_current_upload() is False
