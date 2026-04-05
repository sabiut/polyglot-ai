"""Structured JSON-lines audit logger."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class AuditLogger:
    """Appends JSON lines to daily rotated audit log files."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def log(self, event_type: str, detail: dict | None = None) -> None:
        now = datetime.now(timezone.utc)
        log_file = self._log_dir / f"audit-{now.strftime('%Y-%m-%d')}.jsonl"
        entry = {
            "timestamp": now.isoformat(),
            "event_type": event_type,
            "detail": detail or {},
        }
        try:
            is_new = not log_file.exists()
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            # Restrict permissions on new audit log files
            if is_new:
                try:
                    os.chmod(log_file, 0o600)
                except OSError:
                    pass
        except OSError:
            logger.exception("Failed to write audit log")
