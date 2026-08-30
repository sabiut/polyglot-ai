"""Tests for image/vision content conversion in the Anthropic and Google providers.

``Message.to_api_dict(include_images=True)`` produces OpenAI-shaped
content-part lists::

    [{"type": "text", "text": ...},
     {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}]

Each provider must translate those into its native shape:

* Anthropic: ``{"type": "image", "source": {"type": "base64", ...}}``
* Gemini:    ``types.Part.from_bytes(data=..., mime_type=...)``

Plain-string content must pass through unchanged, and malformed or
non-data image URLs must be dropped with a warning — never raise
mid-stream.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from polyglot_ai.core.ai.anthropic_client import (
    AnthropicClient,
    _convert_user_content,
)
from polyglot_ai.core.ai.anthropic_client import (
    _parse_data_url as _anthropic_parse_data_url,
)
from polyglot_ai.core.ai.google_client import (
    GoogleClient,
    _convert_content_parts,
)
from polyglot_ai.core.ai.google_client import (
    _parse_data_url as _google_parse_data_url,
)
from polyglot_ai.core.ai.provider import AIProvider
from polyglot_ai.core.bridge import EventBus

# 1x1 transparent PNG — small, valid base64.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode("ascii")
_PNG_DATA_URL = f"data:image/png;base64,{_PNG_B64}"


def _text_part(text):
    return {"type": "text", "text": text}


def _image_part(url):
    return {"type": "image_url", "image_url": {"url": url, "detail": "auto"}}


# ── data-URL parser (both providers) ────────────────────────────────


@pytest.mark.parametrize(
    "parse", [_anthropic_parse_data_url, _google_parse_data_url], ids=["anthropic", "google"]
)
class TestParseDataUrl:
    def test_valid_png_data_url(self, parse):
        assert parse(_PNG_DATA_URL) == ("image/png", _PNG_B64)

    def test_valid_jpeg_data_url(self, parse):
        assert parse("data:image/jpeg;base64,abcd") == ("image/jpeg", "abcd")

    @pytest.mark.parametrize(
        "bad",
        [
            "https://example.com/cat.png",  # plain http(s) URL
            "data:image/png,notbase64payload",  # missing ;base64 marker
            "data:;base64,abcd",  # missing mime type
            "data:image/png;base64,",  # missing payload
            "data:image/png;base64",  # no comma at all
            "",
            None,
            123,
        ],
    )
    def test_rejects_non_data_or_malformed(self, parse, bad):
        assert parse(bad) is None


# ── Anthropic conversion ────────────────────────────────────────────


class TestAnthropicConvertUserContent:
    def test_plain_string_passes_through_unchanged(self):
        assert _convert_user_content("hello") == "hello"
        assert _convert_user_content("") == ""

    def test_mixed_text_and_image_list(self):
        content = [_text_part("what is this?"), _image_part(_PNG_DATA_URL)]
        assert _convert_user_content(content) == [
            {"type": "text", "text": "what is this?"},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": _PNG_B64,
                },
            },
        ]

    def test_image_only_list(self):
        out = _convert_user_content([_image_part(_PNG_DATA_URL)])
        assert out == [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": _PNG_B64,
                },
            }
        ]

    def test_malformed_data_url_skipped_without_raising(self):
        content = [_text_part("hi"), _image_part("data:image/png,oops-not-base64")]
        assert _convert_user_content(content) == [{"type": "text", "text": "hi"}]

    def test_non_data_http_url_dropped(self):
        content = [_text_part("hi"), _image_part("https://example.com/cat.png")]
        assert _convert_user_content(content) == [{"type": "text", "text": "hi"}]

    def test_all_parts_dropped_falls_back_to_empty_string(self):
        assert _convert_user_content([_image_part("https://example.com/x.png")]) == ""

    def test_unknown_part_type_skipped(self):
        content = [{"type": "input_audio", "data": "zzz"}, _text_part("hi")]
        assert _convert_user_content(content) == [{"type": "text", "text": "hi"}]


class _FakeMessagesStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def __aiter__(self):
        return
        yield  # pragma: no cover

    async def get_final_message(self):
        return None


class _FakeMessages:
    def __init__(self):
        self.last_kwargs = None

    def stream(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakeMessagesStream()


@pytest.mark.asyncio
async def test_anthropic_stream_chat_sends_native_image_blocks():
    """End-to-end through stream_chat: an OpenAI-shaped multimodal user
    turn must reach the SDK as Anthropic-native text + image blocks."""
    bus = EventBus()
    client = AnthropicClient.__new__(AnthropicClient)
    AIProvider.__init__(client, bus)
    client._client = SimpleNamespace(messages=_FakeMessages())

    messages = [
        {
            "role": "user",
            "content": [_text_part("describe this"), _image_part(_PNG_DATA_URL)],
        }
    ]
    _ = [c async for c in client.stream_chat(messages=messages, model="claude-opus-4-6")]

    sent = client._client.messages.last_kwargs["messages"]
    assert sent == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe this"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": _PNG_B64,
                    },
                },
            ],
        }
    ]


@pytest.mark.asyncio
async def test_anthropic_stream_chat_plain_string_unchanged():
    bus = EventBus()
    client = AnthropicClient.__new__(AnthropicClient)
    AIProvider.__init__(client, bus)
    client._client = SimpleNamespace(messages=_FakeMessages())

    _ = [
        c
        async for c in client.stream_chat(
            messages=[{"role": "user", "content": "just text"}],
            model="claude-opus-4-6",
        )
    ]
    sent = client._client.messages.last_kwargs["messages"]
    assert sent == [{"role": "user", "content": "just text"}]


# ── Google conversion ───────────────────────────────────────────────


class TestGoogleConvertContentParts:
    def test_mixed_text_and_image_list(self):
        parts = _convert_content_parts([_text_part("what is this?"), _image_part(_PNG_DATA_URL)])
        assert len(parts) == 2
        assert parts[0].text == "what is this?"
        assert parts[1].inline_data is not None
        assert parts[1].inline_data.mime_type == "image/png"
        assert parts[1].inline_data.data == _PNG_BYTES

    def test_image_only_list(self):
        parts = _convert_content_parts([_image_part(_PNG_DATA_URL)])
        assert len(parts) == 1
        assert parts[0].inline_data.data == _PNG_BYTES

    def test_malformed_data_url_skipped_without_raising(self):
        parts = _convert_content_parts(
            [_text_part("hi"), _image_part("data:image/png,oops-not-base64")]
        )
        assert len(parts) == 1
        assert parts[0].text == "hi"

    def test_undecodable_base64_skipped_without_raising(self):
        parts = _convert_content_parts(
            [_text_part("hi"), _image_part("data:image/png;base64,!!!not-base64!!!")]
        )
        assert len(parts) == 1
        assert parts[0].text == "hi"

    def test_non_data_http_url_dropped(self):
        parts = _convert_content_parts(
            [_text_part("hi"), _image_part("https://example.com/cat.png")]
        )
        assert len(parts) == 1
        assert parts[0].text == "hi"

    def test_all_parts_dropped_falls_back_to_empty_text_part(self):
        parts = _convert_content_parts([_image_part("https://example.com/x.png")])
        assert len(parts) == 1
        assert parts[0].text == ""

    def test_unknown_part_type_skipped(self):
        parts = _convert_content_parts([{"type": "input_audio", "data": "z"}, _text_part("hi")])
        assert len(parts) == 1
        assert parts[0].text == "hi"


class _AIter:
    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        return
        yield  # pragma: no cover


class _FakeGenaiModels:
    def __init__(self):
        self.last_kwargs = None

    def generate_content_stream(self, **kwargs):
        self.last_kwargs = kwargs
        return _AIter()


def _make_google_client():
    bus = EventBus()
    client = GoogleClient.__new__(GoogleClient)
    AIProvider.__init__(client, bus)
    client._client = SimpleNamespace(aio=SimpleNamespace(models=_FakeGenaiModels()))
    return client


@pytest.mark.asyncio
async def test_google_stream_chat_sends_inline_image_parts():
    """End-to-end through stream_chat: an OpenAI-shaped multimodal user
    turn must reach the SDK as Gemini text + inline-bytes parts."""
    client = _make_google_client()

    messages = [
        {
            "role": "user",
            "content": [_text_part("describe this"), _image_part(_PNG_DATA_URL)],
        }
    ]
    _ = [c async for c in client.stream_chat(messages=messages, model="gemini-3.1-pro-preview")]

    sent = client._client.aio.models.last_kwargs["contents"]
    assert len(sent) == 1
    assert sent[0].role == "user"
    assert len(sent[0].parts) == 2
    assert sent[0].parts[0].text == "describe this"
    assert sent[0].parts[1].inline_data.mime_type == "image/png"
    assert sent[0].parts[1].inline_data.data == _PNG_BYTES


@pytest.mark.asyncio
async def test_google_stream_chat_plain_string_unchanged():
    client = _make_google_client()
    _ = [
        c
        async for c in client.stream_chat(
            messages=[{"role": "user", "content": "just text"}],
            model="gemini-3.1-pro-preview",
        )
    ]
    sent = client._client.aio.models.last_kwargs["contents"]
    assert len(sent) == 1
    assert sent[0].role == "user"
    assert len(sent[0].parts) == 1
    assert sent[0].parts[0].text == "just text"


@pytest.mark.asyncio
async def test_google_stream_chat_malformed_image_does_not_crash_stream():
    """A malformed data URL in the history must not raise — the stream
    completes and the text part still goes through."""
    client = _make_google_client()
    messages = [
        {
            "role": "user",
            "content": [_text_part("hi"), _image_part("data:image/png;base64,%%%")],
        }
    ]
    _ = [c async for c in client.stream_chat(messages=messages, model="gemini-3.1-pro-preview")]

    sent = client._client.aio.models.last_kwargs["contents"]
    assert len(sent[0].parts) == 1
    assert sent[0].parts[0].text == "hi"
