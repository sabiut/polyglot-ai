"""Lightweight action registry for command palette and keyboard shortcuts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Action:
    action_id: str
    label: str
    callback: Callable[[], Any]
    category: str = "General"
    shortcut: str = ""


class ActionRegistry:
    """Stores named actions that can be discovered and executed by the command palette."""

    def __init__(self) -> None:
        self._actions: dict[str, Action] = {}

    def register(
        self,
        action_id: str,
        label: str,
        callback: Callable[[], Any],
        category: str = "General",
        shortcut: str = "",
    ) -> None:
        self._actions[action_id] = Action(
            action_id=action_id,
            label=label,
            callback=callback,
            category=category,
            shortcut=shortcut,
        )

    def unregister(self, action_id: str) -> None:
        self._actions.pop(action_id, None)

    def get_all(self) -> list[Action]:
        return list(self._actions.values())

    def search(self, query: str) -> list[Action]:
        if not query:
            return self.get_all()
        q = query.lower()
        exact = []
        contains = []
        for action in self._actions.values():
            label_lower = action.label.lower()
            if label_lower.startswith(q):
                exact.append(action)
            elif q in label_lower or q in action.category.lower():
                contains.append(action)
        return exact + contains

    def execute(self, action_id: str) -> None:
        action = self._actions.get(action_id)
        if action:
            action.callback()
