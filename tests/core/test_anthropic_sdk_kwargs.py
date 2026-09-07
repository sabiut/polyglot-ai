"""anthropic SDK 1.x removed ``temperature`` from messages.stream().

Passing it raised ``TypeError: AsyncMessages.stream() got an unexpected
keyword argument 'temperature'`` on the first Claude message. The
kwargs must be filtered against the SDK's real signature.
"""

from __future__ import annotations

from polyglot_ai.core.ai.claude_oauth import drop_unsupported_stream_kwargs


def _sdk_1x_stream(*, model, messages, max_tokens, system=None, thinking=None, tools=None):
    """Mimics anthropic 1.4.0: no ``temperature`` parameter, no **kwargs."""


def _sdk_0x_stream(*, model, messages, max_tokens, temperature=None, system=None, tools=None):
    """Mimics anthropic 0.x: ``temperature`` still accepted."""


def _sdk_with_var_kwargs(**kwargs):
    """A signature with **kwargs must be left untouched."""


def test_drops_temperature_for_sdk_1x():
    kwargs = {"model": "claude", "messages": [], "max_tokens": 10, "temperature": 0.7}
    out = drop_unsupported_stream_kwargs(_sdk_1x_stream, kwargs)
    assert "temperature" not in out
    assert out["model"] == "claude" and out["max_tokens"] == 10


def test_keeps_temperature_for_sdk_0x():
    kwargs = {"model": "claude", "messages": [], "max_tokens": 10, "temperature": 0.7}
    out = drop_unsupported_stream_kwargs(_sdk_0x_stream, kwargs)
    assert out == kwargs


def test_var_kwargs_signature_passes_through():
    kwargs = {"model": "claude", "temperature": 0.2, "anything": 1}
    assert drop_unsupported_stream_kwargs(_sdk_with_var_kwargs, kwargs) == kwargs


def test_unsignaturable_callable_passes_through():
    kwargs = {"model": "claude", "temperature": 0.2}
    assert drop_unsupported_stream_kwargs(object(), kwargs) == kwargs
