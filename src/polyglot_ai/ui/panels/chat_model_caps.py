"""Per-model capability metadata used by the chat panel.

Pure data — separated from ``chat_panel.py`` so the panel doesn't
carry a 100-line table and so the catalogue can be reused by future
code (e.g. a model picker elsewhere). Each entry describes what the
model supports so the UI can show/hide controls (vision upload,
reasoning indicator, fast badge) without hard-coding those decisions
at every call site.
"""

from __future__ import annotations

MODEL_CAPS: dict[str, dict] = {
    "gpt-5.5": {
        "vision": True,
        "tools": True,
        "reasoning": False,
        "fast": False,
        "desc": "Latest flagship — most capable",
    },
    "gpt-5.4": {
        "vision": True,
        "tools": True,
        "reasoning": False,
        "fast": False,
        "desc": "Previous flagship — still strong",
    },
    "o4-mini": {
        "vision": False,
        "tools": True,
        "reasoning": True,
        "fast": True,
        "desc": "Efficient reasoning model",
    },
    "claude-opus-4-7": {
        "vision": True,
        "tools": True,
        "reasoning": True,
        "fast": False,
        "desc": "Latest Claude flagship",
    },
    "claude-opus-4-6": {
        "vision": True,
        "tools": True,
        "reasoning": True,
        "fast": False,
        "desc": "Previous flagship — still strong",
    },
    "claude-sonnet-4-6": {
        "vision": True,
        "tools": True,
        "reasoning": False,
        "fast": False,
        "desc": "Most efficient for everyday tasks",
    },
    "gemini-3.1-pro-preview": {
        "vision": True,
        "tools": True,
        "reasoning": False,
        "fast": False,
        "desc": "Most capable Gemini model",
    },
    "gemini-3-flash-preview": {
        "vision": True,
        "tools": True,
        "reasoning": False,
        "fast": True,
        "desc": "Fast and efficient",
    },
    "gemini-3.1-flash-lite-preview": {
        "vision": True,
        "tools": False,
        "reasoning": False,
        "fast": True,
        "desc": "Lightweight and fast",
    },
    "deepseek-v4-pro": {
        "vision": False,
        "tools": True,
        "reasoning": False,
        "fast": False,
        "desc": "Flagship V4 — most capable",
    },
    "deepseek-v4-flash": {
        "vision": False,
        "tools": True,
        "reasoning": False,
        "fast": True,
        "desc": "Fast V4 — lower cost, lower latency",
    },
}
