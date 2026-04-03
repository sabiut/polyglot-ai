"""Tests for EventBus."""

from polyglot_ai.core.bridge import EventBus


def test_subscribe_and_emit():
    bus = EventBus()
    received = []
    bus.subscribe("test", lambda **kw: received.append(kw))
    bus.emit("test", data="hello")
    assert received == [{"data": "hello"}]


def test_unsubscribe():
    bus = EventBus()
    received = []

    def handler(**kw):
        received.append(kw)

    bus.subscribe("test", handler)
    bus.unsubscribe("test", handler)
    bus.emit("test", data="hello")
    assert received == []


def test_multiple_subscribers():
    bus = EventBus()
    results = []
    bus.subscribe("test", lambda **kw: results.append("a"))
    bus.subscribe("test", lambda **kw: results.append("b"))
    bus.emit("test")
    assert results == ["a", "b"]


def test_emit_unknown_event():
    bus = EventBus()
    bus.emit("nonexistent")  # should not raise


def test_handler_error_does_not_stop_others():
    bus = EventBus()
    results = []

    def bad_handler(**kw):
        raise ValueError("boom")

    bus.subscribe("test", bad_handler)
    bus.subscribe("test", lambda **kw: results.append("ok"))
    bus.emit("test")
    assert results == ["ok"]


def test_clear():
    bus = EventBus()
    received = []
    bus.subscribe("test", lambda **kw: received.append(1))
    bus.clear()
    bus.emit("test")
    assert received == []
