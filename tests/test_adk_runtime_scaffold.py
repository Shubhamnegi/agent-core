from types import SimpleNamespace
from typing import Any

import pytest
from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent
from google.adk.tools.mcp_tool import McpToolset

from agent_core.domain.models import AgentRunRequest
from agent_core.infra.adk.runtime import AdkRuntimeScaffold


def test_adk_runtime_uses_llm_coordinator_with_planner_executor_subagents() -> None:
    runtime = AdkRuntimeScaffold(app_name="test-app", max_replans=3)

    assert isinstance(runtime.root_agent, SequentialAgent)
    assert len(runtime.root_agent.sub_agents) == 1
    assert isinstance(runtime.replan_loop_agent, LoopAgent)
    assert runtime.replan_loop_agent.max_iterations == 3

    loop_subagents = runtime.replan_loop_agent.sub_agents
    assert len(loop_subagents) == 1
    assert loop_subagents[0] is runtime.coordinator_agent

    coordinator = runtime.coordinator_agent
    assert isinstance(coordinator, LlmAgent)

    subagent_names = [agent.name for agent in coordinator.sub_agents]
    assert subagent_names == ["planner_subagent_a", "executor_subagent_b"]


def test_adk_runtime_wires_mcp_toolset_filters_for_planner_and_executor_step() -> None:
    runtime = AdkRuntimeScaffold(
        app_name="test-app",
        max_replans=3,
        mcp_server_url="http://localhost:8081/mcp",
    )

    assert isinstance(runtime.planner_mcp_toolset, McpToolset)
    assert runtime.planner_mcp_toolset.tool_filter == ["find_relevant_skills", "load_skill"]

    runtime.configure_executor_step_tools(["skill_sales", "skill_inventory"])

    assert isinstance(runtime.executor_mcp_toolset, McpToolset)
    assert runtime.executor_mcp_toolset.tool_filter == ["skill_sales", "skill_inventory"]


class _FakeSessionService:
    def __init__(self) -> None:
        self.get_calls = 0
        self.create_calls = 0
        self._sessions: dict[tuple[str, str, str], object] = {}

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Any = None,
    ) -> object | None:
        key = (app_name, user_id, session_id)
        self.get_calls += 1
        _ = config
        return self._sessions.get(key)

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Any = None,
        session_id: str | None = None,
    ) -> object:
        self.create_calls += 1
        key = (app_name, user_id, session_id or "")
        session = {"state": state}
        self._sessions[key] = session
        return session


class _FakeMemoryService:
    def __init__(self) -> None:
        self.index_calls = 0
        self.search_calls = 0
        self.last_search_args: dict[str, str] = {}

    async def add_session_to_memory(self, session: object) -> None:
        _ = session
        self.index_calls += 1

    async def search_memory(self, *, app_name: str, user_id: str, query: str) -> dict[str, Any]:
        self.search_calls += 1
        self.last_search_args = {
            "app_name": app_name,
            "user_id": user_id,
            "query": query,
        }
        return {"memories": []}


class _FakeEventRepository:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def append(self, event: Any) -> None:
        self.events.append(
            {
                "event_type": event.event_type,
                "tenant_id": event.tenant_id,
                "session_id": event.session_id,
                "plan_id": event.plan_id,
                "task_id": event.task_id,
                "payload": event.payload,
            }
        )

    async def list_by_plan(self, plan_id: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["plan_id"] == plan_id]


@pytest.mark.asyncio
async def test_adk_runtime_ensures_session_via_session_service() -> None:
    runtime = AdkRuntimeScaffold(app_name="test-app", max_replans=3)
    fake_service = _FakeSessionService()
    runtime.session_service = fake_service  # type: ignore[assignment]

    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="hello",
    )

    await runtime._ensure_session(request)

    assert fake_service.get_calls == 1
    assert fake_service.create_calls == 1


@pytest.mark.asyncio
async def test_adk_runtime_indexes_session_in_memory_service() -> None:
    runtime = AdkRuntimeScaffold(app_name="test-app", max_replans=3)
    fake_session_service = _FakeSessionService()
    fake_memory_service = _FakeMemoryService()
    runtime.session_service = fake_session_service  # type: ignore[assignment]
    runtime.memory_service = fake_memory_service  # type: ignore[assignment]

    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="hello",
    )

    await runtime._ensure_session(request)
    await runtime._index_session_in_memory(request)

    assert fake_memory_service.index_calls == 1


@pytest.mark.asyncio
async def test_adk_runtime_uses_memory_service_for_cross_session_search() -> None:
    runtime = AdkRuntimeScaffold(app_name="test-app", max_replans=3)
    fake_memory_service = _FakeMemoryService()
    runtime.memory_service = fake_memory_service  # type: ignore[assignment]

    result = await runtime.search_cross_session_memory(user_id="user_1", query="outlet")

    assert result == {"memories": []}
    assert fake_memory_service.search_calls == 1
    assert fake_memory_service.last_search_args == {
        "app_name": "test-app",
        "user_id": "user_1",
        "query": "outlet",
    }


@pytest.mark.asyncio
async def test_adk_runtime_mirrors_event_stream_with_lineage_fields() -> None:
    fake_event_repo = _FakeEventRepository()
    runtime = AdkRuntimeScaffold(
        app_name="test-app",
        max_replans=3,
        event_repo=fake_event_repo,  # type: ignore[arg-type]
    )
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="hello",
    )
    adk_event = SimpleNamespace(
        id="evt_1",
        author="orchestrator_manager",
        invocation_id="task_123",
        is_final_response=False,
        content=SimpleNamespace(parts=[SimpleNamespace(text="chunk")]),
    )

    await runtime._mirror_adk_event(request=request, plan_id="plan_adk_123", event=adk_event)

    assert len(fake_event_repo.events) == 1
    mirrored = fake_event_repo.events[0]
    assert mirrored["event_type"] == "adk.event"
    assert mirrored["tenant_id"] == "tenant_1"
    assert mirrored["session_id"] == "session_1"
    assert mirrored["plan_id"] == "plan_adk_123"
    assert mirrored["task_id"] == "task_123"
    assert mirrored["payload"]["author"] == "orchestrator_manager"
    assert mirrored["payload"]["text_preview"] == "chunk"
