from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from agent_core.infra.adk.tool_runtime_context import get_tool_runtime_context


async def write_memory(key: str, data: dict[str, Any], return_spec: dict[str, Any]) -> dict[str, Any]:
    """Persist structured session-scoped memory data under a logical key.

    Use when a step needs durable intermediate state for the current session.
    `return_spec` should describe expected output shape for downstream readers.
    """
    context = get_tool_runtime_context()
    if context is None or context.memory_repo is None:
        return {
            "status": "not_configured",
            "reason": "memory_repository_not_configured",
            "key": key,
        }

    namespaced_key = await context.memory_repo.write(
        tenant_id=context.tenant_id,
        session_id=context.session_id,
        task_id=_new_task_id(context.plan_id),
        key=key,
        value=data,
        return_spec_shape=return_spec,
        scope="session",
    )
    return {
        "status": "ok",
        "namespaced_key": namespaced_key,
        "scope": "session",
        "data": data,
    }


async def read_memory(namespaced_key: str) -> dict[str, Any]:
    """Read previously stored memory by namespaced key.

    Use when a prior step returned `namespaced_key` and current execution needs
    the exact stored payload.
    """
    context = get_tool_runtime_context()
    if context is None or context.memory_repo is None:
        return {
            "status": "not_configured",
            "reason": "memory_repository_not_configured",
            "key": namespaced_key,
        }

    value = await context.memory_repo.read(namespaced_key=namespaced_key)
    return {
        "status": "ok" if value is not None else "not_found",
        "key": namespaced_key,
        "data": value,
    }


async def save_user_memory(
    key: str,
    memory_json: str,
    return_spec_json: str | None = None,
) -> dict[str, Any]:
    """Save durable cross-session user memory from JSON text.

    Use for preferences/facts likely needed in future sessions.
    `memory_json` must be a JSON object string.
    """
    context = get_tool_runtime_context()
    if context is None or context.memory_repo is None:
        return {
            "status": "not_configured",
            "reason": "memory_repository_not_configured",
            "key": key,
        }

    parsed_memory = _parse_json_object(memory_json)
    if parsed_memory is None:
        return {
            "status": "failed",
            "reason": "invalid_memory_json",
            "key": key,
        }

    parsed_spec = _parse_json_object(return_spec_json) if return_spec_json else None
    effective_spec = parsed_spec or _derive_return_spec(parsed_memory)
    duplicate = await _find_duplicate_memory(
        context=context,
        parsed_memory=parsed_memory,
        scope="user",
    )
    if duplicate is not None:
        return {
            "status": "duplicate_skipped",
            "memory_type": "user_memory",
            "scope": "user",
            "namespaced_key": duplicate,
            "reason": "similar_memory_exists",
        }
    namespaced_key = await context.memory_repo.write(
        tenant_id=context.tenant_id,
        session_id=context.session_id,
        task_id=_new_task_id(context.plan_id),
        key=key,
        value=parsed_memory,
        return_spec_shape=effective_spec,
        scope="user",
    )
    return {
        "status": "ok",
        "memory_type": "user_memory",
        "scope": "user",
        "namespaced_key": namespaced_key,
    }


async def save_action_memory(
    key: str,
    memory_json: str,
    return_spec_json: str | None = None,
) -> dict[str, Any]:
    """Save session-scoped action memory from JSON text.

    Use for execution outcomes relevant to current workflow, but not long-term
    user preferences.
    """
    context = get_tool_runtime_context()
    if context is None or context.memory_repo is None:
        return {
            "status": "not_configured",
            "reason": "memory_repository_not_configured",
            "key": key,
        }

    parsed_memory = _parse_json_object(memory_json)
    if parsed_memory is None:
        return {
            "status": "failed",
            "reason": "invalid_memory_json",
            "key": key,
        }

    parsed_spec = _parse_json_object(return_spec_json) if return_spec_json else None
    effective_spec = parsed_spec or _derive_return_spec(parsed_memory)
    duplicate = await _find_duplicate_memory(
        context=context,
        parsed_memory=parsed_memory,
        scope="session",
    )
    if duplicate is not None:
        return {
            "status": "duplicate_skipped",
            "memory_type": "action_memory",
            "scope": "session",
            "namespaced_key": duplicate,
            "reason": "similar_memory_exists",
        }
    namespaced_key = await context.memory_repo.write(
        tenant_id=context.tenant_id,
        session_id=context.session_id,
        task_id=_new_task_id(context.plan_id),
        key=key,
        value=parsed_memory,
        return_spec_shape=effective_spec,
        scope="session",
    )
    return {
        "status": "ok",
        "memory_type": "action_memory",
        "scope": "session",
        "namespaced_key": namespaced_key,
    }


async def search_relevant_memory(
    query: str,
    scope: str = "user",
    top_k: int = 5,
) -> dict[str, Any]:
    """Semantic search over stored memory by query text.

    Use to retrieve top-k relevant memories before planning/execution.
    `scope` controls retrieval domain (typically `user` or `session`).
    """
    context = get_tool_runtime_context()
    if context is None or context.memory_repo is None:
        return {
            "status": "not_configured",
            "reason": "memory_repository_not_configured",
            "query": query,
            "results": [],
        }

    results = await context.memory_repo.search(
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        session_id=context.session_id,
        query_text=query,
        scope=scope,
        top_k=top_k,
    )
    return {
        "status": "ok",
        "query": query,
        "scope": scope,
        "results": results,
        "count": len(results),
    }


def _new_task_id(plan_id: str) -> str:
    return f"{plan_id}:{uuid4().hex[:8]}"


def _derive_return_spec(data: dict[str, Any]) -> dict[str, Any]:
    return {field: _infer_type(value) for field, value in data.items()}


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _parse_json_object(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _memory_fingerprint(memory: dict[str, Any]) -> str:
    return json.dumps(memory, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


async def _find_duplicate_memory(
    context: Any,
    parsed_memory: dict[str, Any],
    scope: str,
) -> str | None:
    query_text = parsed_memory.get("memory_text")
    if not isinstance(query_text, str) or not query_text.strip():
        query_text = _memory_fingerprint(parsed_memory)

    try:
        candidates = await context.memory_repo.search(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            session_id=context.session_id,
            query_text=query_text,
            scope=scope,
            top_k=10,
        )
    except Exception:
        return None

    target_fp = _memory_fingerprint(parsed_memory)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        existing_value = candidate.get("value")
        if not isinstance(existing_value, dict):
            continue
        if _memory_fingerprint(existing_value) != target_fp:
            continue

        namespaced_key = candidate.get("namespaced_key")
        if isinstance(namespaced_key, str) and namespaced_key:
            return namespaced_key
    return None
