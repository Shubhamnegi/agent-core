from typing import Any

from fastapi.testclient import TestClient

from agent_core.api.main import app
from agent_core.domain.models import AgentRunResponse
from agent_core.infra.adapters.in_memory import (
    InMemoryEventRepository,
    InMemoryMemoryRepository,
    InMemoryPlanRepository,
    InMemorySoulRepository,
)


def test_agent_run_boundary_is_preserved_and_subagents_endpoint_absent() -> None:
    with TestClient(app) as client:
        container = app.state.container

        async def _fake_run(_: Any) -> AgentRunResponse:
            return AgentRunResponse(
                status="complete",
                response="adk-scaffold-stub",
                plan_id="plan_adk_test",
            )

        container.runtime_engine = "adk_scaffold"
        container.adk_runtime.run = _fake_run  # type: ignore[method-assign]

        response = client.post(
            "/agent/run",
            json={
                "tenant_id": "tenant_1",
                "user_id": "user_1",
                "session_id": "session_1",
                "message": "hello",
            },
        )
        missing = client.post("/agent/subagents", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "complete"
    assert response.json()["plan_id"] == "plan_adk_test"
    assert missing.status_code == 404


def test_storage_adapter_boundary_is_preserved_in_container_wiring() -> None:
    with TestClient(app):
        container = app.state.container

        assert isinstance(container.plan_repo, InMemoryPlanRepository)
        assert isinstance(container.memory_repo, InMemoryMemoryRepository)
        assert isinstance(container.event_repo, InMemoryEventRepository)
        assert isinstance(container.soul_repo, InMemorySoulRepository)

        assert container.adk_runtime.event_repo is container.event_repo
