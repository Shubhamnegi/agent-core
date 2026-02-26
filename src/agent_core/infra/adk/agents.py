from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.events import Event
from google.genai import types


class PlannerAgent(BaseAgent):
    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Event, None]:
        message = _extract_user_text(ctx)
        content = types.Content(
            role="model",
            parts=[types.Part(text=f"planner_scaffold: analyzed request '{message[:80]}'")],
        )
        yield Event(author=self.name, content=content)


class ExecutorAgent(BaseAgent):
    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Event, None]:
        message = _extract_user_text(ctx)
        content = types.Content(
            role="model",
            parts=[
                types.Part(
                    text=f"executor_scaffold: prepared step output for '{message[:80]}'"
                )
            ],
        )
        yield Event(author=self.name, content=content)


def build_coordinator_agent(
    planner: BaseAgent,
    executor: BaseAgent,
    model_name: str = "models/gemini-flash-lite-latest",
) -> LlmAgent:
    return LlmAgent(
        name="orchestrator_manager",
        description="Manager role scaffold",
        model=model_name,
        instruction=(
            "ADK scaffold coordinator. Delegate planning/execution via sub-agents and "
            "emit concise response summaries."
        ),
        sub_agents=[planner, executor],
    )


def build_planner_agent(
    mcp_toolset: Any | None = None,
    model_name: str = "models/gemini-flash-lite-latest",
) -> LlmAgent:
    tools = [mcp_toolset] if mcp_toolset is not None else []
    return LlmAgent(
        name="planner_subagent_a",
        description="Planner role scaffold",
        model=model_name,
        instruction=(
            "Use MCP discovery tools to identify and load relevant skills, then "
            "produce concise planning guidance."
        ),
        tools=tools,
    )


def build_executor_agent(
    mcp_toolset: Any | None = None,
    model_name: str = "models/gemini-flash-lite-latest",
) -> LlmAgent:
    tools = [mcp_toolset] if mcp_toolset is not None else []
    return LlmAgent(
        name="executor_subagent_b",
        description="Executor role scaffold",
        model=model_name,
        instruction=(
            "Use only allowed MCP skills for this step and return concise execution output."
        ),
        tools=tools,
    )


def _extract_user_text(ctx: Any) -> str:
    user_content = getattr(ctx, "user_content", None)
    if user_content is None:
        return ""
    parts = getattr(user_content, "parts", None)
    if not parts:
        return ""
    return getattr(parts[0], "text", "") or ""
