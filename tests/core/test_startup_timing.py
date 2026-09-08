"""Startup phase timer used by app.main()."""

from __future__ import annotations

from polyglot_ai.startup import timing


def test_phases_are_deltas_and_total_spans_all(monkeypatch):
    clock = iter([10.5, 10.7, 11.7])
    monkeypatch.setattr(timing.time, "perf_counter", lambda: next(clock))
    t = timing.StartupTimer()
    t._t0 = 10.0
    t.mark("a")
    t.mark("b")
    t.mark("c")
    assert [(n, round(s, 3)) for n, s in t.phases()] == [("a", 0.5), ("b", 0.2), ("c", 1.0)]
    assert round(t.total(), 3) == 1.7
    report = t.report()
    assert "  500.0 ms  a" in report
    assert "1700.0 ms  total since app import" in report


def test_empty_timer_reports_zero():
    t = timing.StartupTimer()
    assert t.total() == 0.0
    assert "0.0 ms  total" in t.report()


def test_env_switches(monkeypatch):
    monkeypatch.delenv("POLYGLOT_AI_STARTUP_TIMING", raising=False)
    monkeypatch.delenv("POLYGLOT_AI_EXIT_AFTER_STARTUP", raising=False)
    assert timing.timing_enabled() is False
    assert timing.exit_after_startup() is False
    monkeypatch.setenv("POLYGLOT_AI_STARTUP_TIMING", "1")
    monkeypatch.setenv("POLYGLOT_AI_EXIT_AFTER_STARTUP", "0")
    assert timing.timing_enabled() is True
    assert timing.exit_after_startup() is False


def test_process_age_is_positive_on_linux():
    age = timing.process_age_seconds()
    assert age is None or age > 0
