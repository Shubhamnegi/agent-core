from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.events import Event
from google.genai import types

from agent_core.infra.adk.callbacks import (
    after_model_callback,
    after_tool_callback,
    before_model_callback,
    before_tool_callback,
    on_tool_error_callback,
)
from agent_core.infra.adk.tools import (
    exec_python,
    read_lines,
    read_memory,
    save_action_memory,
    save_user_memory,
    search_relevant_memory,
    write_memory,
    write_temp,
)
from agent_core.prompts import (
    COORDINATOR_INSTRUCTION,
    EXECUTOR_INSTRUCTION,
    EXECUTOR_SCAFFOLD_PREFIX,
    MEMORY_INSTRUCTION,
    PLANNER_INSTRUCTION,
    PLANNER_SCAFFOLD_PREFIX,
)


class PlannerAgent(BaseAgent):
    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Event, None]:
        message = _extract_user_text(ctx)
        content = types.Content(
            role="model",
            parts=[types.Part(text=f"{PLANNER_SCAFFOLD_PREFIX} '{message[:80]}'")],
        )
        yield Event(author=self.name, content=content)


class ExecutorAgent(BaseAgent):
    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Event, None]:
        message = _extract_user_text(ctx)
        content = types.Content(
            role="model",
            parts=[
                types.Part(
                    text=f"{EXECUTOR_SCAFFOLD_PREFIX} '{message[:80]}'"
                )
            ],
        )
        yield Event(author=self.name, content=content)


def build_coordinator_agent(
    memory: BaseAgent,
    planner: BaseAgent,
    executor: BaseAgent,
    model_name: str = "models/gemini-flash-lite-latest",
) -> LlmAgent:
    return LlmAgent(
        name="orchestrator_manager",
        description="Manager role scaffold",
        model=model_name,
        instruction=COORDINATOR_INSTRUCTION,
        sub_agents=[memory, planner, executor],
        before_model_callback=before_model_callback,
        after_model_callback=after_model_callback,
        before_tool_callback=before_tool_callback,
        after_tool_callback=after_tool_callback,
        on_tool_error_callback=on_tool_error_callback,
    )


def build_memory_agent(
    model_name: str = "models/gemini-flash-lite-latest",
) -> LlmAgent:
    return LlmAgent(
        name="memory_subagent_c",
        description="Memory intelligence scaffold",
        model=model_name,
        instruction=MEMORY_INSTRUCTION,
        tools=[search_relevant_memory, save_user_memory, save_action_memory, read_memory],
        before_model_callback=before_model_callback,
        after_model_callback=after_model_callback,
        before_tool_callback=before_tool_callback,
        after_tool_callback=after_tool_callback,
        on_tool_error_callback=on_tool_error_callback,
    )


def build_planner_agent(
    mcp_toolset: Any | None = None,
    model_name: str = "models/gemini-flash-lite-latest",
) -> LlmAgent:
    tools: list[Any] = _infra_tools()
    if mcp_toolset is not None:
        tools.append(mcp_toolset)
    return LlmAgent(
        name="planner_subagent_a",
        description="Planner role scaffold",
        model=model_name,
        instruction=PLANNER_INSTRUCTION,
        tools=tools,
        before_model_callback=before_model_callback,
        after_model_callback=after_model_callback,
        before_tool_callback=before_tool_callback,
        after_tool_callback=after_tool_callback,
        on_tool_error_callback=on_tool_error_callback,
    )


def build_executor_agent(
    mcp_toolsets: list[Any] | None = None,
    model_name: str = "models/gemini-flash-lite-latest",
) -> LlmAgent:
    tools: list[Any] = _infra_tools()
    if mcp_toolsets is not None:
        tools.extend(mcp_toolsets)
    return LlmAgent(
        name="executor_subagent_b",
        description="Executor role scaffold",
        model=model_name,
        instruction=EXECUTOR_INSTRUCTION,
        tools=tools,
        before_model_callback=before_model_callback,
        after_model_callback=after_model_callback,
        before_tool_callback=before_tool_callback,
        after_tool_callback=after_tool_callback,
        on_tool_error_callback=on_tool_error_callback,
    )


def _extract_user_text(ctx: Any) -> str:
    user_content = getattr(ctx, "user_content", None)
    if user_content is None:
        return ""
    parts = getattr(user_content, "parts", None)
    if not parts:
        return ""
    return getattr(parts[0], "text", "") or ""


def _infra_tools() -> list[Any]:
    return [
        write_memory,
        read_memory,
        save_user_memory,
        save_action_memory,
        search_relevant_memory,
        write_temp,
        read_lines,
        exec_python,
    ]
