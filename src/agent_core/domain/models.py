from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


class PlanStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    REPLANNING = "replanning"
    COMPLETE = "complete"
    FAILED = "failed"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(slots=True)
class ReturnSpec:
    shape: dict[str, Any]
    reason: str


@dataclass(slots=True)
class PlanStep:
    step_index: int
    task: str
    skills: list[str]
    return_spec: ReturnSpec
    input_from_step: int | None = None
    status: StepStatus = StepStatus.PENDING
    task_id: str | None = None
    memory_key: str | None = None
    validated: bool = False
    failure_reason: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(slots=True)
class ReplanEvent:
    attempt: int
    trigger: str
    failed_step: int
    reason: str
    revised_at: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class Plan:
    session_id: str
    tenant_id: str
    user_id: str
    steps: list[PlanStep]
    plan_id: str = field(default_factory=lambda: f"plan_{uuid4().hex[:12]}")
    status: PlanStatus = PlanStatus.PENDING
    replan_count: int = 0
    created_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None
    replan_history: list[ReplanEvent] = field(default_factory=list)


@dataclass(slots=True)
class AgentRunRequest:
    tenant_id: str
    user_id: str
    session_id: str
    message: str


@dataclass(slots=True)
class AgentRunResponse:
    status: str
    response: str
    plan_id: str


@dataclass(slots=True)
class StepExecutionResult:
    status: str
    data: dict[str, Any] | None
    reason: str | None = None
    suggestion: str | None = None


@dataclass(slots=True)
class PlannerOutput:
    steps: list[PlanStep]


@dataclass(slots=True)
class EventRecord:
    event_type: str
    tenant_id: str
    session_id: str
    plan_id: str | None
    task_id: str | None
    payload: dict[str, Any]
    ts: datetime = field(default_factory=utc_now)
