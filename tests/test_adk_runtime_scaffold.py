import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent
from google.adk.tools.mcp_tool import McpToolset

from agent_core.domain.models import AgentRunRequest
from agent_core.infra.adk.agents import build_executor_agent, build_planner_agent
from agent_core.infra.adk.callbacks import (
    after_model_callback,
    after_tool_callback,
    before_model_callback,
    before_tool_callback,
    bind_trace_context,
    on_tool_error_callback,
    reset_trace_context,
)
from agent_core.infra.adk.runtime import AdkRuntimeScaffold
from agent_core.prompts import COORDINATOR_INSTRUCTION, MEMORY_INSTRUCTION, PLANNER_INSTRUCTION


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
    assert subagent_names == ["memory_subagent_c", "planner_subagent_a", "executor_subagent_b"]


def test_prompt_contract_routes_memory_via_coordinator_and_planner() -> None:
    assert "memory_subagent_c" in COORDINATOR_INSTRUCTION
    assert "persist durable memory" in COORDINATOR_INSTRUCTION
    assert "search_relevant_memory" in PLANNER_INSTRUCTION
    assert "intent-rich memory queries" in PLANNER_INSTRUCTION
    assert "save_user_memory" in MEMORY_INSTRUCTION
    assert "save_action_memory" in MEMORY_INSTRUCTION
    assert "memory_text" in MEMORY_INSTRUCTION
    assert "domain" in MEMORY_INSTRUCTION
    assert "intent" in MEMORY_INSTRUCTION
    assert "entities" in MEMORY_INSTRUCTION
    assert "query_hints" in MEMORY_INSTRUCTION


def _write_mcp_config(path: Path) -> None:
    payload = {
        "planner_endpoint": "skill_service",
        "endpoints": [
            {
                "name": "skill_service",
                "url": "http://localhost:8081/mcp",
                "planner_tool_filter": ["find_relevant_skill", "load_instructions"],
                "auth_headers": [
                    {
                        "name": "x-api-key",
                        "request_header": "x-skill-service-key",
                        "env": "AGENT_SKILL_SERVICE_KEY",
                    }
                ],
            },
            {
                "name": "aws_cost_explorer",
                "transport": "stdio",
                "command": "uvx",
                "args": ["awslabs.cost-explorer-mcp-server@latest"],
                "stdio_env": {"FASTMCP_LOG_LEVEL": "ERROR", "AWS_PROFILE": "default"},
            }
        ],
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def test_adk_runtime_wires_mcp_toolset_filters_for_planner_and_executor_step(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "mcp_config.json"
    _write_mcp_config(config_path)

    runtime = AdkRuntimeScaffold(
        app_name="test-app",
        max_replans=3,
        mcp_config_path=config_path.as_posix(),
        skill_service_key="fallback-key",
    )

    runtime.configure_mcp_for_request({"x-skill-service-key": "request-key"})

    assert isinstance(runtime.planner_mcp_toolset, McpToolset)
    assert runtime.planner_mcp_toolset.tool_filter == ["find_relevant_skill", "load_instructions"]
    assert runtime._resolved_planner_endpoint is not None
    assert runtime._resolved_planner_endpoint.headers == {"x-api-key": "request-key"}
    assert len(runtime._resolved_executor_endpoints) == 2

    runtime.configure_executor_step_tools(["skill_sales", "skill_inventory"])

    assert len(runtime.executor_mcp_toolsets) == 2
    assert all(
        toolset.tool_filter == ["skill_sales", "skill_inventory"]
        for toolset in runtime.executor_mcp_toolsets
    )


def test_adk_subagents_always_include_infra_tool_suite() -> None:
    planner = build_planner_agent(mcp_toolset=None)
    executor = build_executor_agent(mcp_toolsets=None)

    planner_tools = {getattr(tool, "__name__", "") for tool in planner.tools}
    executor_tools = {getattr(tool, "__name__", "") for tool in executor.tools}
    expected = {
        "write_memory",
        "read_memory",
        "save_user_memory",
        "save_action_memory",
        "search_relevant_memory",
        "write_temp",
        "read_lines",
        "exec_python",
    }

    assert expected.issubset(planner_tools)
    assert expected.issubset(executor_tools)


def test_adk_runtime_uses_env_fallback_key_when_request_header_missing(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "mcp_config.json"
    _write_mcp_config(config_path)

    runtime = AdkRuntimeScaffold(
        app_name="test-app",
        max_replans=3,
        mcp_config_path=config_path.as_posix(),
        skill_service_key="fallback-key",
    )

    runtime.configure_mcp_for_request({})

    assert isinstance(runtime.planner_mcp_toolset, McpToolset)
    assert runtime._resolved_planner_endpoint is not None
    assert runtime._resolved_planner_endpoint.headers == {"x-api-key": "fallback-key"}


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

    is_first_turn = await runtime._ensure_session(request)

    assert is_first_turn is True
    assert fake_service.get_calls == 1
    assert fake_service.create_calls == 1

    is_first_turn_again = await runtime._ensure_session(request)
    assert is_first_turn_again is False


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


@pytest.mark.asyncio
async def test_before_tool_callback_logs_tool_args_without_logrecord_collision(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tool = SimpleNamespace(name="read_memory")
    payload = {"key": "summary"}

    with caplog.at_level("INFO"):
        result = await before_tool_callback(tool=tool, args=payload, tool_context=None)

    assert result is None
    record = next(entry for entry in caplog.records if entry.msg == "tool_call_start")
    assert getattr(record, "tool_args") == payload


@pytest.mark.asyncio
async def test_after_tool_callback_accepts_tool_response_keyword() -> None:
    tool = SimpleNamespace(name="read_memory")

    result = await after_tool_callback(
        tool=tool,
        args={"key": "summary"},
        tool_context=None,
        tool_response={"status": "ok", "value": {"summary": "ready"}},
    )

    assert result is not None
    assert result["status"] == "ok"
    assert result["tool_name"] == "read_memory"


@pytest.mark.asyncio
async def test_model_callbacks_persist_prompt_and_response_events_with_trace_context() -> None:
    fake_event_repo = _FakeEventRepository()
    token = bind_trace_context(
        event_repo=fake_event_repo,  # type: ignore[arg-type]
        tenant_id="tenant_1",
        session_id="session_1",
        plan_id="plan_adk_trace_1",
        require_planner_first_transfer=False,
    )
    try:
        callback_context = SimpleNamespace(agent_name="planner_subagent_a", invocation_id="task_1")

        llm_request = SimpleNamespace(
            model="gemini-2.5-flash",
            contents=[SimpleNamespace(parts=[SimpleNamespace(text="what is aws bill?")])],
            config=None,
            tools_dict={"find_relevant_skill": object()},
        )
        await before_model_callback(callback_context, llm_request)

        llm_response = SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text="bill is 137.13")]),
            model_version="gemini-2.5-flash",
            finish_reason=None,
            error_code=None,
            error_message=None,
        )
        await after_model_callback(callback_context, llm_response)
    finally:
        reset_trace_context(token)

    event_types = [event["event_type"] for event in fake_event_repo.events]
    assert "adk.prompt" in event_types
    assert "adk.llm_response" in event_types
    for event in fake_event_repo.events:
        assert event["plan_id"] == "plan_adk_trace_1"
        assert event["session_id"] == "session_1"


@pytest.mark.asyncio
async def test_before_tool_callback_blocks_executor_transfer_on_first_turn() -> None:
    token = bind_trace_context(
        event_repo=_FakeEventRepository(),  # type: ignore[arg-type]
        tenant_id="tenant_1",
        session_id="session_1",
        plan_id="plan_adk_trace_2",
        require_planner_first_transfer=True,
    )
    try:
        blocked = await before_tool_callback(
            tool=SimpleNamespace(name="transfer_to_agent"),
            args={"agent_name": "executor_subagent_b"},
            tool_context=SimpleNamespace(agent_name="orchestrator_manager"),
        )
    finally:
        reset_trace_context(token)

    assert blocked is not None
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "planner_required_before_executor_first_turn"


@pytest.mark.asyncio
async def test_before_tool_callback_allows_executor_after_planner_transfer() -> None:
    token = bind_trace_context(
        event_repo=_FakeEventRepository(),  # type: ignore[arg-type]
        tenant_id="tenant_1",
        session_id="session_1",
        plan_id="plan_adk_trace_3",
        require_planner_first_transfer=True,
    )
    try:
        planner_result = await before_tool_callback(
            tool=SimpleNamespace(name="transfer_to_agent"),
            args={"agent_name": "planner_subagent_a"},
            tool_context=SimpleNamespace(agent_name="orchestrator_manager"),
        )

        planner_find_result = await before_tool_callback(
            tool=SimpleNamespace(name="find_relevant_skill"),
            args={"query": "aws cost compare"},
            tool_context=SimpleNamespace(agent_name="planner_subagent_a"),
        )
        await after_tool_callback(
            tool=SimpleNamespace(name="find_relevant_skill"),
            args={"query": "aws cost compare"},
            tool_context=SimpleNamespace(agent_name="planner_subagent_a"),
            tool_response={"results": [{"skill_id": "skill_aws_cost"}]},
        )

        planner_load_result = await before_tool_callback(
            tool=SimpleNamespace(name="load_instruction"),
            args={"skill_id": "skill_aws_cost"},
            tool_context=SimpleNamespace(agent_name="planner_subagent_a"),
        )

        executor_result = await before_tool_callback(
            tool=SimpleNamespace(name="transfer_to_agent"),
            args={"agent_name": "executor_subagent_b"},
            tool_context=SimpleNamespace(agent_name="orchestrator_manager"),
        )
    finally:
        reset_trace_context(token)

    assert planner_result is None
    assert planner_find_result is None
    assert planner_load_result is None
    assert executor_result is None


@pytest.mark.asyncio
async def test_before_tool_callback_blocks_executor_when_planner_skips_find_skill() -> None:
    token = bind_trace_context(
        event_repo=_FakeEventRepository(),  # type: ignore[arg-type]
        tenant_id="tenant_1",
        session_id="session_1",
        plan_id="plan_adk_trace_4",
        require_planner_first_transfer=True,
    )
    try:
        await before_tool_callback(
            tool=SimpleNamespace(name="transfer_to_agent"),
            args={"agent_name": "planner_subagent_a"},
            tool_context=SimpleNamespace(agent_name="orchestrator_manager"),
        )

        blocked = await before_tool_callback(
            tool=SimpleNamespace(name="transfer_to_agent"),
            args={"agent_name": "executor_subagent_b"},
            tool_context=SimpleNamespace(agent_name="orchestrator_manager"),
        )
    finally:
        reset_trace_context(token)

    assert blocked is not None
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "planner_must_discover_skills_before_executor"


@pytest.mark.asyncio
async def test_before_tool_callback_blocks_executor_when_planner_skips_load_skill() -> None:
    token = bind_trace_context(
        event_repo=_FakeEventRepository(),  # type: ignore[arg-type]
        tenant_id="tenant_1",
        session_id="session_1",
        plan_id="plan_adk_trace_5",
        require_planner_first_transfer=True,
    )
    try:
        await before_tool_callback(
            tool=SimpleNamespace(name="transfer_to_agent"),
            args={"agent_name": "planner_subagent_a"},
            tool_context=SimpleNamespace(agent_name="orchestrator_manager"),
        )
        await before_tool_callback(
            tool=SimpleNamespace(name="find_relevant_skill"),
            args={"query": "cost"},
            tool_context=SimpleNamespace(agent_name="planner_subagent_a"),
        )
        await after_tool_callback(
            tool=SimpleNamespace(name="find_relevant_skill"),
            args={"query": "cost"},
            tool_context=SimpleNamespace(agent_name="planner_subagent_a"),
            tool_response={"results": [{"skill_id": "skill_a"}]},
        )

        blocked = await before_tool_callback(
            tool=SimpleNamespace(name="transfer_to_agent"),
            args={"agent_name": "executor_subagent_b"},
            tool_context=SimpleNamespace(agent_name="orchestrator_manager"),
        )
    finally:
        reset_trace_context(token)

    assert blocked is not None
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "planner_must_load_skills_before_executor"


@pytest.mark.asyncio
async def test_before_tool_callback_allows_executor_when_no_skills_found() -> None:
    token = bind_trace_context(
        event_repo=_FakeEventRepository(),  # type: ignore[arg-type]
        tenant_id="tenant_1",
        session_id="session_1",
        plan_id="plan_adk_trace_6",
        require_planner_first_transfer=True,
    )
    try:
        await before_tool_callback(
            tool=SimpleNamespace(name="transfer_to_agent"),
            args={"agent_name": "planner_subagent_a"},
            tool_context=SimpleNamespace(agent_name="orchestrator_manager"),
        )
        await before_tool_callback(
            tool=SimpleNamespace(name="find_relevant_skill"),
            args={"query": "cost"},
            tool_context=SimpleNamespace(agent_name="planner_subagent_a"),
        )
        await after_tool_callback(
            tool=SimpleNamespace(name="find_relevant_skill"),
            args={"query": "cost"},
            tool_context=SimpleNamespace(agent_name="planner_subagent_a"),
            tool_response={"results": []},
        )

        allowed = await before_tool_callback(
            tool=SimpleNamespace(name="transfer_to_agent"),
            args={"agent_name": "executor_subagent_b"},
            tool_context=SimpleNamespace(agent_name="orchestrator_manager"),
        )
    finally:
        reset_trace_context(token)

    assert allowed is None


@pytest.mark.asyncio
async def test_on_tool_error_callback_accepts_error_keyword() -> None:
    result = await on_tool_error_callback(
        tool=SimpleNamespace(name="save_user_memory"),
        args={"key": "pref"},
        tool_context=SimpleNamespace(agent_name="memory_subagent_c"),
        error=RuntimeError("embedding_failed"),
    )

    assert result["status"] == "failed"
    assert result["tool_name"] == "save_user_memory"
    assert "embedding_failed" in result["reason"]
