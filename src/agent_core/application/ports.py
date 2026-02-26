from __future__ import annotations

from typing import Any, Protocol

from agent_core.domain.models import (
    AgentRunRequest,
    EventRecord,
    Plan,
    PlannerOutput,
    PlanStep,
    StepExecutionResult,
)


class PlannerAgent(Protocol):
    async def create_plan(self, request: AgentRunRequest, max_steps: int = 10) -> PlannerOutput:
        ...

    async def replan(
        self,
        request: AgentRunRequest,
        completed_steps: list[PlanStep],
        failed_step: PlanStep,
        reason: str,
        max_steps: int = 10,
    ) -> PlannerOutput:
        ...


class ExecutorAgent(Protocol):
    async def execute_step(
        self,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
    ) -> StepExecutionResult:
        ...


class PlanRepository(Protocol):
    async def save(self, plan: Plan) -> None:
        ...

    async def get(self, plan_id: str) -> Plan | None:
        ...


class MemoryRepository(Protocol):
    async def write(
        self,
        tenant_id: str,
        session_id: str,
        task_id: str,
        key: str,
        value: dict,
        return_spec_shape: dict,
    ) -> str:
        ...

    async def read(self, namespaced_key: str) -> dict | None:
        ...


class EventRepository(Protocol):
    async def append(self, event: EventRecord) -> None:
        ...

    async def list_by_plan(self, plan_id: str) -> list[EventRecord]:
        ...


class MessageBusPublisher(Protocol):
    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        ...


class SoulRepository(Protocol):
    async def upsert(self, tenant_id: str, user_id: str | None, payload: dict) -> None:
        ...
