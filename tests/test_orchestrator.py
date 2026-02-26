import pytest

from agent_core.application.services.orchestrator import AgentOrchestrator
from agent_core.domain.exceptions import ReplanLimitReachedError
from agent_core.domain.models import AgentRunRequest
from agent_core.infra.adapters.in_memory import (
    InMemoryEventRepository,
    InMemoryMemoryRepository,
    InMemoryPlanRepository,
)
from agent_core.infra.agents.mock_executor import MockExecutorAgent
from agent_core.infra.agents.mock_planner import MockPlannerAgent


def _orchestrator(max_replans: int = 1) -> AgentOrchestrator:
    return AgentOrchestrator(
        planner=MockPlannerAgent(),
        executor=MockExecutorAgent(),
        plan_repo=InMemoryPlanRepository(),
        memory_repo=InMemoryMemoryRepository(),
        event_repo=InMemoryEventRepository(),
        max_steps=10,
        max_replans=max_replans,
    )


@pytest.mark.asyncio
async def test_orchestrator_success_flow() -> None:
    service = _orchestrator()
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="normal request",
    )

    response = await service.run(request)

    assert response.status == "complete"
    assert response.plan_id.startswith("plan_")


@pytest.mark.asyncio
async def test_orchestrator_stops_on_replan_limit() -> None:
    service = _orchestrator(max_replans=0)
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="fail this task",
    )

    with pytest.raises(ReplanLimitReachedError):
        await service.run(request)
