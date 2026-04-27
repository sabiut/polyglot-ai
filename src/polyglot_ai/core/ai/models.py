"""Data models for AI conversations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ToolCall:
    id: str
    function_name: str
    arguments: str  # JSON string


@dataclass
class Attachment:
    """File or image attachment on a message."""

    path: str
    filename: str
    mime_type: str
    size: int = 0


@dataclass
class Message:
    role: str  # system, user, assistant, tool
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    attachments: list[Attachment] | None = None
    # ``reasoning_content`` carries the chain-of-thought emitted by
    # thinking-mode models (DeepSeek's deepseek-reasoner / V4-pro,
    # potentially others). DeepSeek's API REQUIRES this be echoed back
    # on the assistant message in subsequent turns — sending the
    # conversation without it causes the API to reject the request:
    #   "The reasoning_content in the thinking mode must be passed
    #    back to the API."
    # We never display it in the UI; it's purely a round-trip payload.
    reasoning_content: str | None = None
    created_at: datetime = field(default_factory=datetime.now)

    def to_api_dict(self, include_images: bool = False) -> dict:
        """Convert to OpenAI API message format.

        If include_images is True and there are image attachments,
        content becomes a list of content parts (text + image_url).
        """
        import base64

        msg: dict = {"role": self.role}

        # Build multimodal content if images are attached
        if include_images and self.attachments:
            image_atts = [a for a in self.attachments if a.mime_type.startswith("image/")]
            if image_atts:
                parts: list[dict] = []
                if self.content:
                    parts.append({"type": "text", "text": self.content})
                for att in image_atts:
                    try:
                        with open(att.path, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode("utf-8")
                        parts.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{att.mime_type};base64,{b64}",
                                    "detail": "auto",
                                },
                            }
                        )
                    except (OSError, IOError):
                        pass  # Skip unreadable images
                msg["content"] = parts if parts else self.content
            else:
                if self.content is not None:
                    msg["content"] = self.content
        elif self.content is not None:
            msg["content"] = self.content

        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function_name,
                        "arguments": tc.arguments,
                    },
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        # Echo back reasoning_content on assistant turns. Required by
        # DeepSeek's thinking mode; OpenAI silently ignores unknown
        # fields so this is safe to always include when present.
        # Anthropic uses a different shape (extended-thinking blocks
        # inside ``content``), handled by anthropic_client.py.
        if self.role == "assistant" and self.reasoning_content:
            msg["reasoning_content"] = self.reasoning_content
        return msg


@dataclass
class Conversation:
    id: int | None = None
    title: str = "New Conversation"
    model: str = "gpt-5.5"
    messages: list[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def get_api_messages(self, include_images: bool = False) -> list[dict]:
        """Get all messages in OpenAI API format."""
        return [m.to_api_dict(include_images=include_images) for m in self.messages]


@dataclass
class StreamChunk:
    delta_content: str | None = None
    # Delta of the model's chain-of-thought, when the provider exposes
    # one (e.g. DeepSeek's ``reasoning_content`` field). Accumulated
    # alongside ``delta_content`` in the agent loop and persisted on
    # the assistant ``Message`` so it can be echoed back on the next
    # turn — DeepSeek's API enforces this round-trip strictly.
    delta_reasoning: str | None = None
    tool_calls: list[dict] | None = None
    finish_reason: str | None = None
    usage: dict | None = None
