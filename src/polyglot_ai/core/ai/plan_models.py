"""Plan data model — structured plans with step-by-step execution tracking."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class PlanStepStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    SKIPPED = "skipped"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class PlanStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    EXECUTING = "executing"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class PlanStep:
    index: int
    title: str
    description: str
    files_affected: list[str] = field(default_factory=list)
    status: PlanStepStatus = PlanStepStatus.PENDING
    result: str | None = None
    verification: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "description": self.description,
            "files_affected": self.files_affected,
            "status": self.status.value,
            "result": self.result,
            "verification": self.verification,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanStep:
        return cls(
            index=data["index"],
            title=data["title"],
            description=data.get("description", ""),
            files_affected=data.get("files_affected", []),
            status=PlanStepStatus(data.get("status", "pending")),
            result=data.get("result"),
            verification=data.get("verification"),
        )


@dataclass
class Plan:
    title: str
    summary: str
    steps: list[PlanStep]
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    status: PlanStatus = PlanStatus.DRAFT
    created_at: datetime = field(default_factory=datetime.now)
    conversation_id: int | None = None
    original_request: str = ""

    @property
    def progress(self) -> float:
        """0.0 to 1.0 progress based on completed/skipped steps."""
        if not self.steps:
            return 0.0
        done = sum(
            1 for s in self.steps if s.status in (PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED)
        )
        return done / len(self.steps)

    @property
    def current_step_index(self) -> int | None:
        """Index of the currently executing step, or None."""
        for s in self.steps:
            if s.status == PlanStepStatus.IN_PROGRESS:
                return s.index
        return None

    @property
    def completed_count(self) -> int:
        return sum(1 for s in self.steps if s.status == PlanStepStatus.COMPLETED)

    @property
    def total_count(self) -> int:
        return len(self.steps)

    def approve_all(self) -> None:
        """Mark all pending steps as approved."""
        for s in self.steps:
            if s.status == PlanStepStatus.PENDING:
                s.status = PlanStepStatus.APPROVED
        self.status = PlanStatus.APPROVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "conversation_id": self.conversation_id,
            "original_request": self.original_request,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        return cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            title=data["title"],
            summary=data.get("summary", ""),
            steps=[PlanStep.from_dict(s) for s in data.get("steps", [])],
            status=PlanStatus(data.get("status", "draft")),
            created_at=datetime.fromisoformat(data["created_at"])
            if "created_at" in data
            else datetime.now(),
            conversation_id=data.get("conversation_id"),
            original_request=data.get("original_request", ""),
        )
