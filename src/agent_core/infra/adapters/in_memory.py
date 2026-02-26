from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agent_core.application.ports import (
    EventRepository,
    MemoryRepository,
    PlanRepository,
    SoulRepository,
)
from agent_core.domain.exceptions import ContractViolationError
from agent_core.domain.models import EventRecord, Plan


class InMemoryPlanRepository(PlanRepository):
    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}

    async def save(self, plan: Plan) -> None:
        self._plans[plan.plan_id] = plan

    async def get(self, plan_id: str) -> Plan | None:
        return self._plans.get(plan_id)


class InMemoryMemoryRepository(MemoryRepository):
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    async def write(
        self,
        tenant_id: str,
        session_id: str,
        task_id: str,
        key: str,
        value: dict,
        return_spec_shape: dict,
    ) -> str:
        if not _has_required_shape(value, return_spec_shape):
            msg = "contract_violation"
            raise ContractViolationError(msg)

        namespaced = f"{tenant_id}:{session_id}:{task_id}:{key}"
        self._data[namespaced] = value
        return namespaced

    async def read(self, namespaced_key: str) -> dict | None:
        return self._data.get(namespaced_key)


class InMemoryEventRepository(EventRepository):
    def __init__(self) -> None:
        self._events: list[EventRecord] = []

    async def append(self, event: EventRecord) -> None:
        self._events.append(event)

    async def list_by_plan(self, plan_id: str) -> list[EventRecord]:
        return [event for event in self._events if event.plan_id == plan_id]


class InMemorySoulRepository(SoulRepository):
    def __init__(self) -> None:
        self._souls: dict[str, dict[str, Any]] = {}

    async def upsert(self, tenant_id: str, user_id: str | None, payload: dict) -> None:
        key = f"{tenant_id}:{user_id or '*'}"
        self._souls[key] = payload


def _has_required_shape(data: dict[str, Any], shape: dict[str, Any]) -> bool:
    required = set(shape.keys())
    present = set(data.keys())
    return required.issubset(present)


def event_to_dict(events: list[EventRecord]) -> list[dict[str, Any]]:
    return [asdict(event) for event in events]
