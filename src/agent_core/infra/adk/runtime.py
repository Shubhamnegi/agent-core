from __future__ import annotations
"""ADK runtime orchestrator.

Why this file stays thin:
- It is the only place that wires request lifecycle, tracing, and agent graph.
- Parsing, mapping, memory post-processing, and config resolution live in helper modules
    so orchestration remains readable and easier to test.
"""

import logging
from typing import Any
from uuid import uuid4

from google.adk.agents import LoopAgent, SequentialAgent
from google.adk.memory import BaseMemoryService
from google.adk.runners import InMemoryRunner
from google.adk.sessions import BaseSessionService
from google.adk.tools.mcp_tool import McpToolset
from google.genai import types

from agent_core.application.ports import EventRepository, MemoryRepository
from agent_core.domain.models import AgentRunRequest, AgentRunResponse, EventRecord
from agent_core.infra.adk.agents import (
    build_coordinator_agent,
    build_executor_agent,
    build_memory_agent,
    build_planner_agent,
)
from agent_core.infra.adk.callbacks import bind_trace_context, reset_trace_context
from agent_core.infra.adk.tools import bind_tool_runtime_context, reset_tool_runtime_context
from agent_core.infra.adapters.embedding import EmbeddingService
from agent_core.infra.adk.mcp import (
    ResolvedMcpEndpoint,
    build_executor_mcp_toolsets,
    build_planner_mcp_toolset,
    resolve_mcp_endpoint,
    resolve_mcp_endpoints,
)
from agent_core.infra.adk.runtime_event_mapper import (
    _extract_event_function_calls,
    _extract_event_function_responses,
    _extract_event_text,
    _to_optional_str,
)
from agent_core.infra.adk.runtime_mcp_resolver import (
    _build_runtime_env_overrides,
    _endpoint_debug,
    _load_mcp_config_or_fallback,
    _normalize_headers,
    _select_endpoint_config,
)
from agent_core.infra.adk.runtime_memory_metadata import (
    _MemoryUsageMetadata,
    _apply_memory_disclosure,
    _extract_memory_usage_metadata,
    _merge_memory_metadata,
)
from agent_core.infra.adk.runtime_message_policy import (
    _message_disables_memory_usage,
    _message_requests_memory_lookup,
    _sanitize_user_response,
)
from agent_core.infra.adk.runtime_model_config import (
    _load_agent_model_overrides,
    _resolve_agent_models,
)
from agent_core.infra.adk.runtime_session import (
    _ensure_session as _ensure_runtime_session,
    _index_session_in_memory as _index_runtime_session_in_memory,
)

logger = logging.getLogger(__name__)


class AdkRuntimeScaffold:
    """Runtime coordinator for the ADK scaffold.

    Why this class exists: to keep a single integration seam for API -> ADK execution,
    while delegating business-adjacent helper logic to focused modules.
    """

    def __init__(
        self,
        app_name: str = "agent-core",
        max_replans: int = 3,
        model_name: str = "models/gemini-flash-lite-latest",
        mcp_config_path: str | None = None,
        skill_service_url: str | None = None,
        skill_service_key: str | None = None,
        agent_models_config_path: str | None = None,
        event_repo: EventRepository | None = None,
        memory_repo: MemoryRepository | None = None,
        embedding_service: EmbeddingService | None = None,
        mcp_session_timeout: float = 60.0,
    ) -> None:
        self.app_name = app_name
        self.max_replans = max_replans
        self.model_name = model_name
        self.mcp_config_path = mcp_config_path
        self.skill_service_url = skill_service_url
        self.skill_service_key = skill_service_key
        self.agent_models_config_path = agent_models_config_path
        self.event_repo = event_repo
        self.memory_repo = memory_repo
        self.embedding_service = embedding_service
        self.mcp_session_timeout = mcp_session_timeout
        self.default_model_name = model_name
        self.agent_models = _resolve_agent_models(
            default_model_name=model_name,
            config_path=agent_models_config_path,
        )
        self.executor_allowed_skills: list[str] = []
        self.planner_mcp_toolset: McpToolset | None = None
        self.executor_mcp_toolsets: list[McpToolset] = []
        self._resolved_planner_endpoint: ResolvedMcpEndpoint | None = None
        self._resolved_executor_endpoints: list[ResolvedMcpEndpoint] = []
        self._rebuild_runtime_graph()

    def configure_executor_step_tools(self, allowed_skills: list[str]) -> None:
        self.executor_allowed_skills = list(allowed_skills)
        logger.info(
            "adk_runtime_executor_tools_configured",
            extra={
                "executor_allowed_skills": self.executor_allowed_skills,
            },
        )
        self._rebuild_runtime_graph()

    def configure_mcp_for_request(self, request_headers: dict[str, str]) -> None:
        self._resolved_planner_endpoint = self._resolve_planner_endpoint(request_headers)
        self._resolved_executor_endpoints = self._resolve_executor_endpoints(request_headers)
        logger.info(
            "adk_runtime_mcp_resolved",
            extra={
                "planner_endpoint": _endpoint_debug(self._resolved_planner_endpoint),
                "executor_endpoints": [
                    _endpoint_debug(endpoint) for endpoint in self._resolved_executor_endpoints
                ],
            },
        )
        self._rebuild_runtime_graph()

    def _rebuild_runtime_graph(self) -> None:
        self.planner_mcp_toolset = (
            build_planner_mcp_toolset(
                self._resolved_planner_endpoint,
                timeout=self.mcp_session_timeout,
            )
            if self._resolved_planner_endpoint is not None
            else None
        )
        self.executor_mcp_toolsets = build_executor_mcp_toolsets(
            self._resolved_executor_endpoints,
            self.executor_allowed_skills,
            timeout=self.mcp_session_timeout,
        )
        logger.info(
            "adk_runtime_graph_rebuilt",
            extra={
                "planner_toolset_enabled": self.planner_mcp_toolset is not None,
                "executor_toolset_count": len(self.executor_mcp_toolsets),
                "agent_models": self.agent_models,
            },
        )

        self.memory_agent = build_memory_agent(model_name=self.agent_models["memory"])
        self.planner_agent = build_planner_agent(
            mcp_toolset=self.planner_mcp_toolset,
            model_name=self.agent_models["planner"],
        )
        self.executor_agent = build_executor_agent(
            mcp_toolsets=self.executor_mcp_toolsets,
            model_name=self.agent_models["executor"],
        )
        self.coordinator_agent = build_coordinator_agent(
            memory=self.memory_agent,
            planner=self.planner_agent,
            executor=self.executor_agent,
            model_name=self.agent_models["coordinator"],
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
        # Why: compute these upfront to keep trace policy deterministic for this turn.
        is_first_turn = await self._ensure_session(request)
        plan_id = f"plan_adk_{uuid4().hex[:12]}"
        memory_disabled_by_user = _message_disables_memory_usage(request.message)
        requires_memory_precheck = is_first_turn or _message_requests_memory_lookup(
            request.message
        )
        if memory_disabled_by_user:
            requires_memory_precheck = False
        trace_token = None
        tool_context_token = bind_tool_runtime_context(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            session_id=request.session_id,
            plan_id=plan_id,
            memory_repo=self.memory_repo,
            embedding_service=self.embedding_service,
        )
        if self.event_repo is not None:
            trace_token = bind_trace_context(
                event_repo=self.event_repo,
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                plan_id=plan_id,
                require_planner_first_transfer=is_first_turn,
                allow_memory_usage=not memory_disabled_by_user,
                require_memory_precheck=requires_memory_precheck,
                planner_expected_tools=(
                    list(self._resolved_planner_endpoint.planner_tools)
                    if self._resolved_planner_endpoint is not None
                    else []
                ),
            )
        events = self.runner.run_async(
            user_id=request.user_id,
            session_id=request.session_id,
            new_message=types.Content(role="user", parts=[types.Part(text=request.message)]),
        )

        # Why: capture all model text chunks; final user response is chosen from the last text event.
        texts: list[str] = []
        memory_metadata = _MemoryUsageMetadata()
        try:
            async for event in events:
                text = _extract_event_text(event)
                if text:
                    texts.append(text)
                memory_metadata = _merge_memory_metadata(
                    memory_metadata,
                    _extract_memory_usage_metadata(_extract_event_function_responses(event)),
                )
                await self._mirror_adk_event(request=request, plan_id=plan_id, event=event)

            response = texts[-1] if texts else "adk_scaffold_response: no output"
            response = _sanitize_user_response(response)
            response = _apply_memory_disclosure(
                response=response,
                memory_metadata=memory_metadata,
                memory_disabled_by_user=memory_disabled_by_user,
            )
            await self._index_session_in_memory(request)
            return AgentRunResponse(
                status="complete",
                response=response,
                plan_id=plan_id,
            )
        except Exception as exc:
            logger.exception(
                "adk_runtime_run_failed",
                extra={
                    "tenant_id": request.tenant_id,
                    "user_id": request.user_id,
                    "session_id": request.session_id,
                    "plan_id": plan_id,
                    "planner_endpoint": _endpoint_debug(self._resolved_planner_endpoint),
                    "executor_endpoints": [
                        _endpoint_debug(endpoint) for endpoint in self._resolved_executor_endpoints
                    ],
                    "executor_allowed_skills": self.executor_allowed_skills,
                    "model_name": self.model_name,
                    "mcp_config_path": self.mcp_config_path,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        finally:
            reset_tool_runtime_context(tool_context_token)
            if trace_token is not None:
                reset_trace_context(trace_token)

    async def search_cross_session_memory(self, user_id: str, query: str) -> Any:
        if self.memory_service is None:
            return None
        return await self.memory_service.search_memory(
            app_name=self.app_name,
            user_id=user_id,
            query=query,
        )

    async def _ensure_session(self, request: AgentRunRequest) -> bool:
        return await _ensure_runtime_session(
            session_service=self.session_service,
            app_name=self.app_name,
            request=request,
        )

    async def _index_session_in_memory(self, request: AgentRunRequest) -> None:
        await _index_runtime_session_in_memory(
            session_service=self.session_service,
            memory_service=self.memory_service,
            app_name=self.app_name,
            request=request,
        )

    async def _mirror_adk_event(self, request: AgentRunRequest, plan_id: str, event: Any) -> None:
        author = _to_optional_str(getattr(event, "author", None))
        event_id = _to_optional_str(getattr(event, "id", None))
        invocation_id = _to_optional_str(getattr(event, "invocation_id", None))
        is_final = bool(getattr(event, "is_final_response", False))
        text = _extract_event_text(event)
        function_calls = _extract_event_function_calls(event)
        function_responses = _extract_event_function_responses(event)

        logger.info(
            "adk_event",
            extra={
                "plan_id": plan_id,
                "session_id": request.session_id,
                "author": author,
                "event_id": event_id,
                "invocation_id": invocation_id,
                "is_final_response": is_final,
                "text_preview": (text[:500] if text else ""),
                "function_calls": function_calls,
                "function_responses": function_responses,
            },
        )

        if self.event_repo is None:
            return

        payload = {
            "author": author,
            "event_id": event_id,
            "text_preview": text,
            "is_final_response": is_final,
            "function_calls": function_calls,
            "function_responses": function_responses,
        }
        await self.event_repo.append(
            EventRecord(
                event_type="adk.event",
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                plan_id=plan_id,
                task_id=invocation_id,
                payload=payload,
            )
        )

    def _resolve_planner_endpoint(
        self,
        request_headers: dict[str, str],
    ) -> ResolvedMcpEndpoint | None:
        env_values = _build_runtime_env_overrides(self.skill_service_url, self.skill_service_key)
        endpoint_config = _select_endpoint_config(self.mcp_config_path, env_values)
        if not endpoint_config:
            return None
        return resolve_mcp_endpoint(
            endpoint_config=endpoint_config,
            request_headers=_normalize_headers(request_headers),
            env_values=env_values,
        )

    def _resolve_executor_endpoints(
        self,
        request_headers: dict[str, str],
    ) -> list[ResolvedMcpEndpoint]:
        env_values = _build_runtime_env_overrides(self.skill_service_url, self.skill_service_key)
        config = _load_mcp_config_or_fallback(self.mcp_config_path, env_values)
        if not config:
            planner_endpoint = self._resolve_planner_endpoint(request_headers)
            return [planner_endpoint] if planner_endpoint is not None else []

        return resolve_mcp_endpoints(
            config=config,
            request_headers=_normalize_headers(request_headers),
            env_values=env_values,
        )



