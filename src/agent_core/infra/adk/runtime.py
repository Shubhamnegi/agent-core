from __future__ import annotations

import logging
import os
import re
from datetime import datetime
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
    load_mcp_config,
    resolve_mcp_endpoint,
    resolve_mcp_endpoints,
)

logger = logging.getLogger(__name__)


class AdkRuntimeScaffold:
    def __init__(
        self,
        app_name: str = "agent-core",
        max_replans: int = 3,
        model_name: str = "models/gemini-flash-lite-latest",
        mcp_config_path: str | None = None,
        skill_service_url: str | None = None,
        skill_service_key: str | None = None,
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
        self.event_repo = event_repo
        self.memory_repo = memory_repo
        self.embedding_service = embedding_service
        self.mcp_session_timeout = mcp_session_timeout
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
            },
        )

        self.memory_agent = build_memory_agent(model_name=self.model_name)
        self.planner_agent = build_planner_agent(
            mcp_toolset=self.planner_mcp_toolset,
            model_name=self.model_name,
        )
        self.executor_agent = build_executor_agent(
            mcp_toolsets=self.executor_mcp_toolsets,
            model_name=self.model_name,
        )
        self.coordinator_agent = build_coordinator_agent(
            memory=self.memory_agent,
            planner=self.planner_agent,
            executor=self.executor_agent,
            model_name=self.model_name,
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
        session = await self.session_service.get_session(
            app_name=self.app_name,
            user_id=request.user_id,
            session_id=request.session_id,
        )
        if session is not None:
            return False
        await self.session_service.create_session(
            app_name=self.app_name,
            user_id=request.user_id,
            session_id=request.session_id,
            state=_build_initial_session_state(request),
        )
        return True

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


def _build_initial_session_state(request: AgentRunRequest) -> dict[str, Any]:
    return {
        "tenant_id": request.tenant_id,
        "user_id": request.user_id,
        "session_id": request.session_id,
    }


def _message_requests_memory_lookup(message: str) -> bool:
    lowered = message.lower()
    memory_markers = (
        "check memory",
        "from memory",
        "search memory",
        "what do you remember",
        "based on my preference",
        "my preference",
        "remembered",
        "recall",
    )
    return any(marker in lowered for marker in memory_markers)


def _message_disables_memory_usage(message: str) -> bool:
    lowered = message.lower()
    disable_markers = (
        "don't use memory",
        "do not use memory",
        "dont use memory",
        "without memory",
        "ignore memory",
        "skip memory",
        "no memory",
    )
    return any(marker in lowered for marker in disable_markers)


class _MemoryUsageMetadata:
    def __init__(
        self,
        used: bool = False,
        latest_timestamp: str | None = None,
        summary: str | None = None,
    ) -> None:
        self.used = used
        self.latest_timestamp = latest_timestamp
        self.summary = summary


def _merge_memory_metadata(
    left: _MemoryUsageMetadata,
    right: _MemoryUsageMetadata,
) -> _MemoryUsageMetadata:
    used = left.used or right.used
    latest = _max_iso_timestamp(left.latest_timestamp, right.latest_timestamp)
    summary = left.summary or right.summary
    return _MemoryUsageMetadata(used=used, latest_timestamp=latest, summary=summary)


def _extract_memory_usage_metadata(
    function_responses: list[dict[str, Any]],
) -> _MemoryUsageMetadata:
    output = _MemoryUsageMetadata()
    for item in function_responses:
        if item.get("name") != "search_relevant_memory":
            continue
        response_payload = item.get("response")
        if not isinstance(response_payload, dict):
            continue

        count = response_payload.get("count")
        if isinstance(count, int) and count > 0:
            output.used = True

        results = response_payload.get("results")
        if not isinstance(results, list):
            continue

        for result in results:
            if not isinstance(result, dict):
                continue
            created_at = result.get("created_at")
            if isinstance(created_at, str) and created_at:
                output.latest_timestamp = _max_iso_timestamp(output.latest_timestamp, created_at)

            summary = _extract_memory_summary(result)
            if summary and not output.summary:
                output.summary = summary
    return output


def _extract_memory_summary(result: dict[str, Any]) -> str | None:
    value = result.get("value")
    if not isinstance(value, dict):
        return None

    blob = value.get("blob_json")
    if isinstance(blob, str):
        try:
            parsed = json.loads(blob)
            if isinstance(parsed, dict):
                return _summarize_memory_value(parsed)
        except Exception:
            return None

    return _summarize_memory_value(value)


def _summarize_memory_value(value: dict[str, Any]) -> str | None:
    memory_text = value.get("memory_text")
    if isinstance(memory_text, str) and memory_text:
        return memory_text

    summary_fields: list[str] = []
    for field_name in ("domain", "intent"):
        field_value = value.get(field_name)
        if isinstance(field_value, str) and field_value:
            summary_fields.append(f"{field_name}: {field_value}")
    entities = value.get("entities")
    if isinstance(entities, list) and entities:
        summary_fields.append(f"entities: {', '.join(str(item) for item in entities[:5])}")

    if not summary_fields:
        return None
    return "; ".join(summary_fields)


def _max_iso_timestamp(left: str | None, right: str | None) -> str | None:
    if left is None:
        return right
    if right is None:
        return left
    return right if right > left else left


def _sanitize_user_response(response: str) -> str:
    sanitized = response
    sanitized = sanitized.replace(
        "The `get_cost_and_usage_comparisons` tool requires both the baseline and comparison periods to be exactly one month long and to start on the first day of the month.",
        "The requested period-over-period comparison is not available for this exact date range.",
    )
    sanitized = re.sub(
        r"`get_[a-zA-Z0-9_]+`",
        "the requested comparison",
        sanitized,
    )
    return sanitized


def _apply_memory_disclosure(
    response: str,
    memory_metadata: _MemoryUsageMetadata,
    memory_disabled_by_user: bool,
) -> str:
    if memory_disabled_by_user:
        prefix = "Note: I did not use memory for this response because you asked to skip memory."
        return f"{prefix}\n\n{response}"

    if not memory_metadata.used:
        return response

    timestamp = memory_metadata.latest_timestamp or "unknown time"
    summary = memory_metadata.summary or "a previously saved preference"
    stale_note = _memory_staleness_note(memory_metadata.latest_timestamp)
    prefix = (
        f"Note: I used saved memory from {timestamp} to tailor this response. "
        f"Applied memory: {summary}."
    )
    if stale_note:
        prefix = f"{prefix} {stale_note}"
    return f"{prefix}\n\n{response}"


def _memory_staleness_note(timestamp: str | None) -> str:
    if not timestamp:
        return ""
    try:
        created_at = datetime.fromisoformat(timestamp)
    except Exception:
        return ""
    age_days = (datetime.now(created_at.tzinfo) - created_at).days
    if age_days >= 30:
        return f"Memory may be stale (saved about {age_days} days ago)."
    return ""


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


def _extract_event_function_calls(event: Any) -> list[dict[str, Any]]:
    """Extract function_call parts from an ADK event."""
    content = getattr(event, "content", None)
    if content is None:
        return []
    parts = getattr(content, "parts", None)
    if not parts:
        return []
    calls: list[dict[str, Any]] = []
    for part in parts:
        fc = getattr(part, "function_call", None)
        if fc is not None:
            calls.append({
                "name": getattr(fc, "name", None),
                "args": dict(getattr(fc, "args", {}) or {}),
            })
    return calls


def _extract_event_function_responses(event: Any) -> list[dict[str, Any]]:
    """Extract function_response parts from an ADK event."""
    content = getattr(event, "content", None)
    if content is None:
        return []
    parts = getattr(content, "parts", None)
    if not parts:
        return []
    responses: list[dict[str, Any]] = []
    for part in parts:
        fr = getattr(part, "function_response", None)
        if fr is not None:
            responses.append({
                "name": getattr(fr, "name", None),
                "response": dict(getattr(fr, "response", {}) or {}),
            })
    return responses


def _to_optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _default_skill_service_endpoint() -> dict[str, Any]:
    return {
        "name": "skill_service",
        "url_env": "AGENT_SKILL_SERVICE_URL",
        "planner_tool_filter": ["find_relevant_skill", "load_instructions"],
        "auth_headers": [
            {
                "name": "x-api-key",
                "request_header": "x-skill-service-key",
                "env": "AGENT_SKILL_SERVICE_KEY",
            }
        ],
    }


def _find_endpoint_by_name(config: dict[str, Any], endpoint_name: str) -> dict[str, Any] | None:
    endpoints = config.get("endpoints", [])
    if not isinstance(endpoints, list):
        return None
    for endpoint in endpoints:
        if isinstance(endpoint, dict) and endpoint.get("name") == endpoint_name:
            return endpoint
    return None


def _build_runtime_env_overrides(
    skill_service_url: str | None,
    skill_service_key: str | None,
) -> dict[str, str]:
    values = dict(os.environ)
    if skill_service_url:
        values["AGENT_SKILL_SERVICE_URL"] = skill_service_url
    if skill_service_key:
        values["AGENT_SKILL_SERVICE_KEY"] = skill_service_key
    return values


def _get_endpoint_name(config: dict[str, Any]) -> str:
    endpoint_name = config.get("planner_endpoint")
    if isinstance(endpoint_name, str) and endpoint_name:
        return endpoint_name
    return "skill_service"


def _select_endpoint_config(
    mcp_config_path: str | None,
    env_values: dict[str, str],
) -> dict[str, Any]:
    if mcp_config_path:
        config = load_mcp_config(mcp_config_path)
        endpoint_name = _get_endpoint_name(config)
        endpoint = _find_endpoint_by_name(config, endpoint_name)
        if endpoint is None:
            msg = "mcp_endpoint_not_found"
            raise ValueError(msg)
        return endpoint

    fallback = _default_skill_service_endpoint()
    if not env_values.get("AGENT_SKILL_SERVICE_URL"):
        return {}
    return fallback


def _load_mcp_config_or_fallback(
    mcp_config_path: str | None,
    env_values: dict[str, str],
) -> dict[str, Any]:
    if mcp_config_path:
        return load_mcp_config(mcp_config_path)

    fallback_endpoint = _default_skill_service_endpoint()
    if not env_values.get("AGENT_SKILL_SERVICE_URL"):
        return {}
    return {
        "planner_endpoint": fallback_endpoint["name"],
        "endpoints": [fallback_endpoint],
    }


def _normalize_headers(request_headers: dict[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in request_headers.items()}


def _endpoint_debug(endpoint: ResolvedMcpEndpoint | None) -> dict[str, Any] | None:
    if endpoint is None:
        return None
    return {
        "name": endpoint.name,
        "transport": endpoint.transport,
        "url": endpoint.url,
        "command": endpoint.command,
        "args": endpoint.args,
        "planner_tools": endpoint.planner_tools,
        "header_names": sorted(endpoint.headers.keys()),
    }


