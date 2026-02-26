from __future__ import annotations

from typing import Any

from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.genai import types


class PlannerAgent(BaseAgent):
    async def _run_async_impl(self, ctx: Any):
        message = _extract_user_text(ctx)
        content = types.Content(
            role="model",
            parts=[types.Part(text=f"planner_scaffold: analyzed request '{message[:80]}'")],
        )
        yield Event(author=self.name, content=content)


class ExecutorAgent(BaseAgent):
    async def _run_async_impl(self, ctx: Any):
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


class CoordinatorAgent(BaseAgent):
    async def _run_async_impl(self, ctx: Any):
        message = _extract_user_text(ctx)
        content = types.Content(
            role="model",
            parts=[
                types.Part(
                    text=(
                        "adk_scaffold_response: coordinator path active; "
                        f"request='{message[:120]}'"
                    )
                )
            ],
        )
        yield Event(author=self.name, content=content)


def _extract_user_text(ctx: Any) -> str:
    user_content = getattr(ctx, "user_content", None)
    if user_content is None:
        return ""
    parts = getattr(user_content, "parts", None)
    if not parts:
        return ""
    return getattr(parts[0], "text", "") or ""
