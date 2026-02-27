from __future__ import annotations

import asyncio
from dataclasses import asdict
from time import monotonic
from typing import Any

from agent_core.application.ports import (
    EventRepository,
    MemoryRepository,
    PlanRepository,
    SoulRepository,
)
from agent_core.domain.exceptions import ContractViolationError, MemoryLockError
from agent_core.domain.models import EventRecord, Plan


class InMemoryPlanRepository(PlanRepository):
    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}

    async def save(self, plan: Plan) -> None:
        self._plans[plan.plan_id] = plan

    async def get(self, plan_id: str) -> Plan | None:
        return self._plans.get(plan_id)


class InMemoryMemoryRepository(MemoryRepository):
    """In-memory memory store with lock semantics mirroring planned production behavior.

    Why this logic is here: even in scaffold mode, enforcing contracts + lock behavior
    catches orchestration issues early before OpenSearch/Redis adapters are wired.
    """

    def __init__(
        self,
        lock_wait_timeout_seconds: float = 5.0,
        lock_ttl_seconds: float = 30.0,
    ) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, _HeldLock] = {}
        self._lock_wait_timeout_seconds = lock_wait_timeout_seconds
        self._lock_ttl_seconds = lock_ttl_seconds

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
        self._validate_user_key_label(key)
        if not _matches_return_spec_contract(value, return_spec_shape):
            msg = "contract_violation"
            raise ContractViolationError(msg)

        namespaced = _build_namespaced_key(tenant_id, session_id, task_id, key)
        await self._acquire_write_lock(namespaced_key=namespaced, owner_task_id=task_id)
        self._data[namespaced] = {
            "tenant_id": tenant_id,
            "session_id": session_id,
            "task_id": task_id,
            "scope": scope,
            "key": key,
            "value": value,
            "return_spec_shape": return_spec_shape,
        }
        return namespaced

    async def read(self, namespaced_key: str, release_lock: bool = False) -> dict | None:
        record = self._data.get(namespaced_key)
        if release_lock:
            # Why release on orchestrator read: it acts as explicit confirmation that
            # step output was consumed, matching the intended lock lifecycle contract.
            self._locks.pop(namespaced_key, None)
        if not isinstance(record, dict):
            return None
        value = record.get("value")
        return value if isinstance(value, dict) else None

    async def search(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        query_text: str,
        scope: str,
        top_k: int,
    ) -> list[dict]:
        _ = user_id
        lowered_query = query_text.lower().strip()
        results: list[dict[str, Any]] = []
        for namespaced_key, record in self._data.items():
            if not isinstance(record, dict):
                continue
            if record.get("tenant_id") != tenant_id:
                continue
            if record.get("scope") != scope:
                continue
            if scope == "session" and record.get("session_id") != session_id:
                continue

            key = str(record.get("key", ""))
            value = record.get("value")
            haystack = f"{key} {value}".lower()
            if lowered_query and lowered_query not in haystack:
                continue

            results.append(
                {
                    "namespaced_key": namespaced_key,
                    "tenant_id": tenant_id,
                    "session_id": record.get("session_id"),
                    "scope": scope,
                    "key": key,
                    "value": value,
                }
            )

        return results[: max(top_k, 0)]

    async def _acquire_write_lock(self, namespaced_key: str, owner_task_id: str) -> None:
        deadline = monotonic() + self._lock_wait_timeout_seconds
        while True:
            self._evict_expired_lock(namespaced_key)
            held_lock = self._locks.get(namespaced_key)

            if held_lock is None or held_lock.owner_task_id == owner_task_id:
                self._locks[namespaced_key] = _HeldLock(
                    owner_task_id=owner_task_id,
                    expires_at=monotonic() + self._lock_ttl_seconds,
                )
                return

            if monotonic() >= deadline:
                msg = "memory_lock_timeout"
                raise MemoryLockError(msg)
            await asyncio.sleep(0.01)

    def _evict_expired_lock(self, namespaced_key: str) -> None:
        held_lock = self._locks.get(namespaced_key)
        if held_lock is not None and held_lock.expires_at <= monotonic():
            self._locks.pop(namespaced_key, None)

    def _validate_user_key_label(self, key: str) -> None:
        if ":" in key:
            msg = "subagents must pass short key labels, not namespaced keys"
            raise ValueError(msg)


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


class _HeldLock:
    def __init__(self, owner_task_id: str, expires_at: float) -> None:
        self.owner_task_id = owner_task_id
        self.expires_at = expires_at


def _build_namespaced_key(tenant_id: str, session_id: str, task_id: str, key: str) -> str:
    return f"{tenant_id}:{session_id}:{task_id}:{key}"


def _matches_return_spec_contract(data: dict[str, Any], shape: dict[str, Any]) -> bool:
    """Validate minimal schema contract using required keys + common scalar type hints.

    Why not full JSONSchema here: keep scaffold lightweight while still enforcing the
    return-spec contract and preventing silently malformed memory writes.
    """

    for field, expected in shape.items():
        if field not in data:
            return False
        if not _matches_expected_type(data[field], expected):
            return False
    return True


def _matches_expected_type(value: Any, expected: Any) -> bool:
    if not isinstance(expected, str):
        return True

    normalized = expected.lower().strip()
    if normalized == "string":
        return isinstance(value, str)
    if normalized in {"int", "integer"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if normalized in {"float", "number"}:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if normalized in {"bool", "boolean"}:
        return isinstance(value, bool)
    if normalized.startswith("array"):
        return isinstance(value, list)
    if normalized in {"object", "dict", "map"}:
        return isinstance(value, dict)
    return True


def event_to_dict(events: list[EventRecord]) -> list[dict[str, Any]]:
    return [asdict(event) for event in events]
