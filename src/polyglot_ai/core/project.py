"""Project manager — handles opening/closing project directories."""

from __future__ import annotations

import logging
from pathlib import Path

from polyglot_ai.constants import EVT_PROJECT_CLOSED, EVT_PROJECT_OPENED
from polyglot_ai.core.bridge import EventBus

logger = logging.getLogger(__name__)


class ProjectManager:
    """Manages the currently open project directory."""

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._root: Path | None = None

    @property
    def root(self) -> Path | None:
        return self._root

    def open_project(self, path: Path) -> bool:
        path = path.resolve()
        if not path.is_dir():
            logger.error("Not a directory: %s", path)
            return False

        if self._root is not None:
            self.close_project()

        self._root = path
        logger.info("Opened project: %s", path)
        self._event_bus.emit(EVT_PROJECT_OPENED, path=str(path))
        return True

    def close_project(self) -> None:
        if self._root is not None:
            logger.info("Closed project: %s", self._root)
            self._root = None
            self._event_bus.emit(EVT_PROJECT_CLOSED)

    def is_open(self) -> bool:
        return self._root is not None
