from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import uuid4

from agent_core.application.ports import (
    EventRepository,
    MemoryRepository,
    PlanRepository,
    SoulRepository,
)
from agent_core.domain.exceptions import ContractViolationError, MemoryLockError
from agent_core.domain.models import (
    EventRecord,
    Plan,
    PlanStep,
    ReplanEvent,
    ReturnSpec,
    StepStatus,
)
from agent_core.infra.adapters.embedding import EmbeddingService
from agent_core.infra.adapters.opensearch_schemas import (
    ALL_INDEXES,
    EVENTS_ILM_POLICY,
    INDEX_AGENT_EVENTS,
    INDEX_AGENT_MEMORY,
    INDEX_AGENT_PLANS,
    INDEX_AGENT_SESSIONS,
    INDEX_AGENT_SOULS,
    build_events_ilm_policy,
    build_index_definition,
    resolve_index_name,
    validate_document_schema,
)


class OpenSearchIndexManager:
    def __init__(
        self,
        client: Any,
        index_prefix: str = "",
        embedding_dims: int = 768,
        events_retention_days: int = 30,
    ) -> None:
        self.client = client
        self.index_prefix = index_prefix
        self.embedding_dims = embedding_dims
        self.events_retention_days = events_retention_days

    def ensure_indices_and_policies(self) -> None:
        # Why policy first: event index creation references ILM policy name in its settings.
        self.client.transport.perform_request(
            "PUT",
            f"/_plugins/_ism/policies/{EVENTS_ILM_POLICY}",
            body=build_events_ilm_policy(self.events_retention_days),
        )

        for base_index in ALL_INDEXES:
            resolved = resolve_index_name(base_index, self.index_prefix)
            if self.client.indices.exists(index=resolved):
                continue

            definition = build_index_definition(
                index_name=base_index,
                embedding_dims=self.embedding_dims,
            )
            self.client.indices.create(index=resolved, body=definition)


class OpenSearchPlanRepository(PlanRepository):
    def __init__(self, client: Any, index_prefix: str = "") -> None:
        self.client = client
        self.index_name = resolve_index_name(INDEX_AGENT_PLANS, index_prefix)

    async def save(self, plan: Plan) -> None:
        document = _plan_to_document(plan)
        validate_document_schema(INDEX_AGENT_PLANS, document)
        self.client.index(index=self.index_name, id=plan.plan_id, body=document, refresh="wait_for")

    async def get(self, plan_id: str) -> Plan | None:
        result = self.client.get(index=self.index_name, id=plan_id, ignore=[404])
        if not isinstance(result, dict) or not result.get("found", False):
            return None
        source = result.get("_source")
        if not isinstance(source, dict):
            return None
        return _document_to_plan(source)


class OpenSearchMemoryRepository(MemoryRepository):
    """OpenSearch-backed memory repo with contract validation and lock semantics.

    Why we keep lock logic here: this preserves deterministic write behavior across
    adapter swaps until Redis lock orchestration is introduced in Section H.
    """

    def __init__(
        self,
        client: Any,
        index_prefix: str = "",
        embedding_service: EmbeddingService | None = None,
        expected_embedding_dims: int | None = None,
        lock_wait_timeout_seconds: float = 5.0,
        lock_ttl_seconds: float = 30.0,
    ) -> None:
        self.client = client
        self.index_name = resolve_index_name(INDEX_AGENT_MEMORY, index_prefix)
        self.embedding_service = embedding_service
        self.expected_embedding_dims = expected_embedding_dims
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
    ) -> str:
        self._validate_user_key_label(key)
        if not _matches_return_spec_contract(value, return_spec_shape):
            msg = "contract_violation"
            raise ContractViolationError(msg)

        namespaced_key = _build_namespaced_key(tenant_id, session_id, task_id, key)
        await self._acquire_write_lock(namespaced_key=namespaced_key, owner_task_id=task_id)

        if self.embedding_service is None:
            msg = "embedding_service_not_configured"
            raise RuntimeError(msg)

        embedding_text = _build_embedding_text(value)
        embedding_vector = await self.embedding_service.embed_text(embedding_text)
        if (
            self.expected_embedding_dims is not None
            and len(embedding_vector) != self.expected_embedding_dims
        ):
            msg = "embedding_dimension_mismatch"
            raise RuntimeError(msg)

        now = _utc_now_iso()
        document = {
            "namespaced_key": namespaced_key,
            "tenant_id": tenant_id,
            "session_id": session_id,
            "task_id": task_id,
            "scope": "session",
            "key": key,
            "value": value,
            "return_spec_shape": return_spec_shape,
            "created_at": now,
            "updated_at": now,
            "embedding": embedding_vector,
        }
        validate_document_schema(INDEX_AGENT_MEMORY, document)

        self.client.index(
            index=self.index_name,
            id=namespaced_key,
            body=document,
            refresh="wait_for",
        )
        return namespaced_key

    async def read(self, namespaced_key: str, release_lock: bool = False) -> dict | None:
        result = self.client.get(index=self.index_name, id=namespaced_key, ignore=[404])
        source = result.get("_source") if isinstance(result, dict) else None
        value = source.get("value") if isinstance(source, dict) else None
        if release_lock:
            self._locks.pop(namespaced_key, None)
        return value if isinstance(value, dict) else None

    async def knn_search(
        self,
        tenant_id: str,
        query_vector: list[float],
        top_k: int,
        scope: str = "session",
    ) -> list[dict[str, Any]]:
        query = build_agent_memory_knn_query(
            tenant_id=tenant_id,
            scope=scope,
            query_vector=query_vector,
            top_k=top_k,
        )
        result = self.client.search(index=self.index_name, body=query)
        hits = result.get("hits", {}).get("hits", []) if isinstance(result, dict) else []
        sources: list[dict[str, Any]] = []
        for hit in hits:
            source = hit.get("_source") if isinstance(hit, dict) else None
            if isinstance(source, dict):
                sources.append(source)
        return sources

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


class OpenSearchEventRepository(EventRepository):
    def __init__(self, client: Any, index_prefix: str = "") -> None:
        self.client = client
        self.index_name = resolve_index_name(INDEX_AGENT_EVENTS, index_prefix)

    async def append(self, event: EventRecord) -> None:
        document = {
            "event_type": event.event_type,
            "tenant_id": event.tenant_id,
            "session_id": event.session_id,
            "plan_id": event.plan_id,
            "task_id": event.task_id,
            "payload": event.payload,
            "ts": event.ts.isoformat(),
        }
        validate_document_schema(INDEX_AGENT_EVENTS, document)
        self.client.index(
            index=self.index_name,
            id=f"evt_{uuid4().hex}",
            body=document,
            refresh="wait_for",
        )

    async def list_by_plan(self, plan_id: str) -> list[EventRecord]:
        query = {
            "query": {
                "term": {
                    "plan_id": plan_id,
                }
            },
            "sort": [{"ts": {"order": "asc"}}],
            "size": 1000,
        }
        result = self.client.search(index=self.index_name, body=query)
        hits = result.get("hits", {}).get("hits", []) if isinstance(result, dict) else []
        output: list[EventRecord] = []
        for hit in hits:
            source = hit.get("_source") if isinstance(hit, dict) else None
            if not isinstance(source, dict):
                continue
            output.append(
                EventRecord(
                    event_type=source.get("event_type", "unknown"),
                    tenant_id=source.get("tenant_id", ""),
                    session_id=source.get("session_id", ""),
                    plan_id=source.get("plan_id"),
                    task_id=source.get("task_id"),
                    payload=source.get("payload", {}),
                    ts=_parse_iso_datetime(source.get("ts")),
                )
            )
        return output


class OpenSearchSoulRepository(SoulRepository):
    def __init__(self, client: Any, index_prefix: str = "") -> None:
        self.client = client
        self.index_name = resolve_index_name(INDEX_AGENT_SOULS, index_prefix)

    async def upsert(self, tenant_id: str, user_id: str | None, payload: dict) -> None:
        document = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "payload": payload,
            "updated_at": _utc_now_iso(),
        }
        validate_document_schema(INDEX_AGENT_SOULS, document)
        soul_id = f"{tenant_id}:{user_id or '*'}"
        self.client.index(index=self.index_name, id=soul_id, body=document, refresh="wait_for")


class OpenSearchSessionStore:
    """Session persistence helper for the `agent_sessions` index.

    Why this helper exists: Section G requires all five indices to be actively modeled,
    even before full ADK session backend migration is completed.
    """

    def __init__(self, client: Any, index_prefix: str = "") -> None:
        self.client = client
        self.index_name = resolve_index_name(INDEX_AGENT_SESSIONS, index_prefix)

    def upsert_session(
        self,
        session_id: str,
        tenant_id: str,
        user_id: str,
        state: dict[str, Any],
    ) -> None:
        now = _utc_now_iso()
        document = {
            "session_id": session_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "state": state,
            "created_at": now,
            "updated_at": now,
        }
        validate_document_schema(INDEX_AGENT_SESSIONS, document)
        self.client.index(index=self.index_name, id=session_id, body=document, refresh="wait_for")


def build_agent_memory_knn_query(
    tenant_id: str,
    scope: str,
    query_vector: list[float],
    top_k: int,
) -> dict[str, Any]:
    # Why bool/filter: tenant/scope constraints must narrow candidate set before ranking.
    return {
        "size": top_k,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"tenant_id": tenant_id}},
                    {"term": {"scope": scope}},
                ],
                "must": [
                    {
                        "knn": {
                            "embedding": {
                                "vector": query_vector,
                                "k": top_k,
                            }
                        }
                    }
                ],
            }
        },
    }


class _HeldLock:
    def __init__(self, owner_task_id: str, expires_at: float) -> None:
        self.owner_task_id = owner_task_id
        self.expires_at = expires_at


def _build_namespaced_key(tenant_id: str, session_id: str, task_id: str, key: str) -> str:
    return f"{tenant_id}:{session_id}:{task_id}:{key}"


def _matches_return_spec_contract(data: dict[str, Any], shape: dict[str, Any]) -> bool:
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


def _build_embedding_text(value: dict[str, Any]) -> str:
    # Why canonical JSON: stable ordering avoids embedding drift for semantically identical objects.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _plan_to_document(plan: Plan) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "tenant_id": plan.tenant_id,
        "session_id": plan.session_id,
        "user_id": plan.user_id,
        "status": plan.status.value,
        "replan_count": plan.replan_count,
        "steps": [_serialize_step(step) for step in plan.steps],
        "replan_history": [_serialize_replan(event) for event in plan.replan_history],
        "created_at": plan.created_at.isoformat(),
        "completed_at": plan.completed_at.isoformat() if plan.completed_at is not None else None,
    }


def _serialize_step(step: PlanStep) -> dict[str, Any]:
    return {
        "step_index": step.step_index,
        "task": step.task,
        "skills": step.skills,
        "return_spec": asdict(step.return_spec),
        "input_from_step": step.input_from_step,
        "status": step.status.value,
        "task_id": step.task_id,
        "memory_key": step.memory_key,
        "validated": step.validated,
        "failure_reason": step.failure_reason,
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "finished_at": step.finished_at.isoformat() if step.finished_at else None,
    }


def _serialize_replan(event: ReplanEvent) -> dict[str, Any]:
    return {
        "attempt": event.attempt,
        "trigger": event.trigger,
        "failed_step": event.failed_step,
        "reason": event.reason,
        "revised_at": event.revised_at.isoformat(),
    }


def _document_to_plan(document: dict[str, Any]) -> Plan:
    steps_data = document.get("steps", []) if isinstance(document.get("steps"), list) else []
    steps = [
        _deserialize_step(step_data)
        for step_data in steps_data
        if isinstance(step_data, dict)
    ]

    replan_data = (
        document.get("replan_history", [])
        if isinstance(document.get("replan_history"), list)
        else []
    )
    replan_history = [
        ReplanEvent(
            attempt=int(item.get("attempt", 0)),
            trigger=str(item.get("trigger", "")),
            failed_step=int(item.get("failed_step", 0)),
            reason=str(item.get("reason", "")),
            revised_at=_parse_iso_datetime(item.get("revised_at")),
        )
        for item in replan_data
        if isinstance(item, dict)
    ]

    completed_at_raw = document.get("completed_at")
    return Plan(
        plan_id=str(document.get("plan_id", "")),
        tenant_id=str(document.get("tenant_id", "")),
        session_id=str(document.get("session_id", "")),
        user_id=str(document.get("user_id", "")),
        status=document.get("status", "pending"),
        replan_count=int(document.get("replan_count", 0)),
        steps=steps,
        replan_history=replan_history,
        created_at=_parse_iso_datetime(document.get("created_at")),
        completed_at=_parse_iso_datetime(completed_at_raw) if completed_at_raw else None,
    )


def _deserialize_step(payload: dict[str, Any]) -> PlanStep:
    return_spec_raw = payload.get("return_spec", {})
    return_spec = ReturnSpec(
        shape=return_spec_raw.get("shape", {}) if isinstance(return_spec_raw, dict) else {},
        reason=return_spec_raw.get("reason", "") if isinstance(return_spec_raw, dict) else "",
    )

    started_at = payload.get("started_at")
    finished_at = payload.get("finished_at")

    return PlanStep(
        step_index=int(payload.get("step_index", 0)),
        task=str(payload.get("task", "")),
        skills=list(payload.get("skills", [])),
        return_spec=return_spec,
        input_from_step=payload.get("input_from_step"),
        status=StepStatus(payload.get("status", StepStatus.PENDING.value)),
        task_id=payload.get("task_id"),
        memory_key=payload.get("memory_key"),
        validated=bool(payload.get("validated", False)),
        failure_reason=payload.get("failure_reason"),
        started_at=_parse_iso_datetime(started_at) if started_at else None,
        finished_at=_parse_iso_datetime(finished_at) if finished_at else None,
    )


def _parse_iso_datetime(value: Any) -> datetime:
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return datetime.now(UTC)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()