from __future__ import annotations

from typing import Protocol

from agent_core.domain.models import (
    EventRecord,
    Plan,
)


class PlanRepository(Protocol):
    async def save(self, plan: Plan) -> None:
        ...

    async def get(self, plan_id: str) -> Plan | None:
        ...


class MemoryRepository(Protocol):
    """Memory persistence boundary used by orchestrator and infra tools.

    Why this exists: orchestration logic should depend on an abstract memory contract,
    so locking and storage semantics can evolve without changing execution flow code.
    """

    async def write(
        self,
        tenant_id: str,
        session_id: str,
        task_id: str,
        key: str,
        value: dict,
        return_spec_shape: dict,
        scope: str = "session",
    ) -> str:
        ...

    async def read(self, namespaced_key: str, release_lock: bool = False) -> dict | None:
        ...

    async def search(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        query_text: str,
        scope: str,
        top_k: int,
    ) -> list[dict]:
        ...


class EventRepository(Protocol):
    async def append(self, event: EventRecord) -> None:
        ...

    async def list_by_plan(self, plan_id: str) -> list[EventRecord]:
        ...


class SoulRepository(Protocol):
    async def upsert(self, tenant_id: str, user_id: str | None, payload: dict) -> None:
        ...
