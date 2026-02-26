from __future__ import annotations

from typing import Any
from uuid import uuid4

from google.adk.agents import LoopAgent, SequentialAgent
from google.adk.memory import BaseMemoryService
from google.adk.runners import InMemoryRunner
from google.adk.sessions import BaseSessionService
from google.adk.tools.mcp_tool import McpToolset
from google.genai import types

from agent_core.application.ports import EventRepository
from agent_core.domain.models import AgentRunRequest, AgentRunResponse, EventRecord
from agent_core.infra.adk.agents import (
    build_coordinator_agent,
    build_executor_agent,
    build_planner_agent,
)
from agent_core.infra.adk.mcp import build_executor_mcp_toolset, build_planner_mcp_toolset


class AdkRuntimeScaffold:
    def __init__(
        self,
        app_name: str = "agent-core",
        max_replans: int = 3,
        mcp_server_url: str | None = None,
        event_repo: EventRepository | None = None,
    ) -> None:
        self.app_name = app_name
        self.max_replans = max_replans
        self.mcp_server_url = mcp_server_url
        self.event_repo = event_repo
        self.executor_allowed_skills: list[str] = []
        self.planner_mcp_toolset: McpToolset | None = None
        self.executor_mcp_toolset: McpToolset | None = None
        self._rebuild_runtime_graph()

    def configure_executor_step_tools(self, allowed_skills: list[str]) -> None:
        self.executor_allowed_skills = list(allowed_skills)
        self._rebuild_runtime_graph()

    def _rebuild_runtime_graph(self) -> None:
        if self.mcp_server_url:
            self.planner_mcp_toolset = build_planner_mcp_toolset(self.mcp_server_url)
            self.executor_mcp_toolset = build_executor_mcp_toolset(
                self.mcp_server_url,
                self.executor_allowed_skills,
            )
        else:
            self.planner_mcp_toolset = None
            self.executor_mcp_toolset = None

        self.planner_agent = build_planner_agent(self.planner_mcp_toolset)
        self.executor_agent = build_executor_agent(self.executor_mcp_toolset)
        self.coordinator_agent = build_coordinator_agent(
            planner=self.planner_agent,
            executor=self.executor_agent,
        )
        self.replan_loop_agent = LoopAgent(
            name="agent_core_replan_loop",
            description="Bounded scaffold replan loop",
            sub_agents=[self.coordinator_agent],
            max_iterations=self.max_replans,
        )
        self.root_agent = SequentialAgent(
            name="agent_core_sequential_shell",
            description="Deterministic scaffold shell",
            sub_agents=[self.replan_loop_agent],
        )
        self.runner = InMemoryRunner(agent=self.root_agent, app_name=self.app_name)
        self.session_service: BaseSessionService = self.runner.session_service
        self.memory_service: BaseMemoryService | None = self.runner.memory_service

    async def run(self, request: AgentRunRequest) -> AgentRunResponse:
        await self._ensure_session(request)
        plan_id = f"plan_adk_{uuid4().hex[:12]}"
        events = self.runner.run_async(
            user_id=request.user_id,
            session_id=request.session_id,
            new_message=types.Content(role="user", parts=[types.Part(text=request.message)]),
        )

        texts: list[str] = []
        async for event in events:
            text = _extract_event_text(event)
            if text:
                texts.append(text)
            await self._mirror_adk_event(request=request, plan_id=plan_id, event=event)

        response = texts[-1] if texts else "adk_scaffold_response: no output"
        await self._index_session_in_memory(request)
        return AgentRunResponse(
            status="complete",
            response=response,
            plan_id=plan_id,
        )

    async def search_cross_session_memory(self, user_id: str, query: str) -> Any:
        if self.memory_service is None:
            return None
        return await self.memory_service.search_memory(
            app_name=self.app_name,
            user_id=user_id,
            query=query,
        )

    async def _ensure_session(self, request: AgentRunRequest) -> None:
        session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=request.user_id,
            session_id=request.session_id,
        )
        if session is not None:
            return
        await self.session_service.create_session(
            app_name=self.app_name,
            user_id=request.user_id,
            session_id=request.session_id,
            state=_build_initial_session_state(request),
        )

    async def _index_session_in_memory(self, request: AgentRunRequest) -> None:
        if self.memory_service is None:
            return
        session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=request.user_id,
            session_id=request.session_id,
        )
        if session is None:
            return
        await self.memory_service.add_session_to_memory(session)

    async def _mirror_adk_event(self, request: AgentRunRequest, plan_id: str, event: Any) -> None:
        if self.event_repo is None:
            return

        task_id = _to_optional_str(getattr(event, "invocation_id", None))
        payload = {
            "author": _to_optional_str(getattr(event, "author", None)),
            "event_id": _to_optional_str(getattr(event, "id", None)),
            "text_preview": _extract_event_text(event),
            "is_final_response": bool(getattr(event, "is_final_response", False)),
        }
        await self.event_repo.append(
            EventRecord(
                event_type="adk.event",
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                plan_id=plan_id,
                task_id=task_id,
                payload=payload,
            )
        )


def _build_initial_session_state(request: AgentRunRequest) -> dict[str, Any]:
    return {
        "tenant_id": request.tenant_id,
        "user_id": request.user_id,
        "session_id": request.session_id,
    }


def _extract_event_text(event: Any) -> str:
    content = getattr(event, "content", None)
    if content is None:
        return ""
    parts = getattr(content, "parts", None)
    if not parts:
        return ""
    first_part = parts[0]
    text = getattr(first_part, "text", None)
    return text if isinstance(text, str) else ""


def _to_optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None
