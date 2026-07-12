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


def test_marshaller_routes_delivery():
    """When a marshaller is installed, emit delegates delivery to it."""
    bus = EventBus()
    seen = []
    bus.subscribe("e", lambda **kw: seen.append(kw))

    calls = []

    def marshaller(deliver):
        calls.append(deliver)  # capture instead of running immediately

    bus.set_marshaller(marshaller)
    bus.emit("e", x=1)
    # Delivery was deferred to the marshaller, not run inline.
    assert seen == []
    assert len(calls) == 1
    calls[0]()  # simulate the GUI thread running it
    assert seen == [{"x": 1}]


def test_marshaller_not_invoked_when_no_subscribers():
    bus = EventBus()
    calls = []
    bus.set_marshaller(lambda deliver: calls.append(deliver))
    bus.emit("nobody_listening", x=1)
    assert calls == []  # no subscribers → no delivery closure at all


def test_clear_marshaller_restores_sync_delivery():
    bus = EventBus()
    seen = []
    bus.subscribe("e", lambda **kw: seen.append(kw))
    bus.set_marshaller(lambda deliver: None)  # swallow
    bus.emit("e", x=1)
    assert seen == []
    bus.set_marshaller(None)
    bus.emit("e", x=2)
    assert seen == [{"x": 2}]  # synchronous again
