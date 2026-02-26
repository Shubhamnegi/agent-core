from __future__ import annotations

from uuid import uuid4

from google.adk.agents import SequentialAgent
from google.adk.runners import InMemoryRunner
from google.genai import types

from agent_core.domain.models import AgentRunRequest, AgentRunResponse
from agent_core.infra.adk.agents import CoordinatorAgent, ExecutorAgent, PlannerAgent


class AdkRuntimeScaffold:
    def __init__(self, app_name: str = "agent-core") -> None:
        planner = PlannerAgent(name="planner_subagent_a", description="Planner role scaffold")
        executor = ExecutorAgent(name="executor_subagent_b", description="Executor role scaffold")
        coordinator = CoordinatorAgent(
            name="orchestrator_manager",
            description="Manager role scaffold",
        )
        self.root_agent = SequentialAgent(
            name="agent_core_sequential_shell",
            description="Deterministic scaffold shell",
            sub_agents=[coordinator, planner, executor],
        )
        self.runner = InMemoryRunner(agent=self.root_agent, app_name=app_name)

    async def run(self, request: AgentRunRequest) -> AgentRunResponse:
        events = self.runner.run_async(
            user_id=request.user_id,
            session_id=request.session_id,
            new_message=types.Content(role="user", parts=[types.Part(text=request.message)]),
        )

        texts: list[str] = []
        async for event in events:
            if event.content and event.content.parts and event.content.parts[0].text:
                texts.append(event.content.parts[0].text)

        response = texts[-1] if texts else "adk_scaffold_response: no output"
        return AgentRunResponse(
            status="complete",
            response=response,
            plan_id=f"plan_adk_{uuid4().hex[:12]}",
        )
