import json

import pytest

from agent_core.infra.adk.tool_memory import save_action_memory, save_user_memory
from agent_core.infra.adk.tool_runtime_context import bind_tool_runtime_context, reset_tool_runtime_context


class _FakeMemoryRepo:
    def __init__(self) -> None:
        self.writes: list[dict] = []
        self.search_results: list[dict] = []

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
        namespaced_key = f"{tenant_id}:{session_id}:{task_id}:{key}"
        self.writes.append(
            {
                "tenant_id": tenant_id,
                "session_id": session_id,
                "task_id": task_id,
                "key": key,
                "value": value,
                "return_spec_shape": return_spec_shape,
                "scope": scope,
                "namespaced_key": namespaced_key,
            }
        )
        return namespaced_key

    async def read(self, namespaced_key: str, release_lock: bool = False) -> dict | None:
        _ = release_lock
        for record in self.writes:
            if record["namespaced_key"] == namespaced_key:
                return record["value"]
        return None

    async def search(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        query_text: str,
        scope: str,
        top_k: int,
    ) -> list[dict]:
        _ = tenant_id
        _ = user_id
        _ = session_id
        _ = query_text
        _ = scope
        _ = top_k
        return list(self.search_results)


@pytest.mark.asyncio
async def test_save_user_memory_skips_duplicate_payload() -> None:
    memory_repo = _FakeMemoryRepo()
    duplicate_memory = {
        "memory_text": "User prefers 7-day AWS cost report.",
        "domain": "aws_cost",
        "intent": "report_preference",
        "entities": ["7-day"],
        "query_hints": ["aws cost 7 day"],
        "source": "orchestrator",
    }
    memory_repo.search_results = [
        {
            "namespaced_key": "tenant_1:sess_1:task_1:aws_cost_report_preference",
            "value": duplicate_memory,
        }
    ]

    token = bind_tool_runtime_context(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="sess_1",
        plan_id="plan_adk_test",
        memory_repo=memory_repo,
        embedding_service=None,
        communication_config_path=None,
    )
    try:
        result = await save_user_memory(
            key="aws_cost_report_preference",
            memory_json=json.dumps(duplicate_memory),
        )
    finally:
        reset_tool_runtime_context(token)

    assert result["status"] == "duplicate_skipped"
    assert result["namespaced_key"] == "tenant_1:sess_1:task_1:aws_cost_report_preference"
    assert memory_repo.writes == []


@pytest.mark.asyncio
async def test_save_action_memory_writes_when_no_duplicate_found() -> None:
    memory_repo = _FakeMemoryRepo()
    payload = {
        "memory_text": "Generated and sent AWS cost report to Slack.",
        "domain": "aws_cost",
        "intent": "delivery_outcome",
        "entities": ["slack", "advance-analytics"],
        "query_hints": ["last delivered aws report"],
        "source": "orchestrator",
    }

    token = bind_tool_runtime_context(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="sess_1",
        plan_id="plan_adk_test",
        memory_repo=memory_repo,
        embedding_service=None,
        communication_config_path=None,
    )
    try:
        result = await save_action_memory(
            key="aws_cost_delivery_outcome",
            memory_json=json.dumps(payload),
        )
    finally:
        reset_tool_runtime_context(token)

    assert result["status"] == "ok"
    assert result["scope"] == "session"
    assert len(memory_repo.writes) == 1
