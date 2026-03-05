from typing import Any

from fastapi.testclient import TestClient

import agent_core.api.main as api_main
from agent_core.api.main import app
from agent_core.domain.exceptions import PlanValidationError
from agent_core.domain.models import AgentRunResponse
from agent_core.infra.adapters.in_memory import (
    InMemoryEventRepository,
    InMemoryMemoryRepository,
    InMemoryPlanRepository,
    InMemorySoulRepository,
)
from agent_core.infra.adapters.redis_events import RedisStreamEventRepository
from agent_core.infra.events.consumer import RedisToOpenSearchEventConsumer


def test_agent_run_boundary_is_preserved_and_subagents_endpoint_absent(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENT_STORAGE_BACKEND", "in_memory")
    with TestClient(app) as client:
        container = app.state.container

        async def _fake_run(_: Any) -> AgentRunResponse:
            return AgentRunResponse(
                status="complete",
                response="adk-scaffold-stub",
                plan_id="plan_adk_test",
            )

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


def test_storage_adapter_boundary_is_preserved_in_container_wiring(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENT_STORAGE_BACKEND", "in_memory")
    with TestClient(app):
        container = app.state.container

        assert isinstance(container.plan_repo, InMemoryPlanRepository)
        assert isinstance(container.memory_repo, InMemoryMemoryRepository)
        assert isinstance(container.event_repo, InMemoryEventRepository)
        assert isinstance(container.soul_repo, InMemorySoulRepository)

        assert container.adk_runtime.event_repo is container.event_repo


def test_memory_query_uses_embedding_before_knn_search(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENT_STORAGE_BACKEND", "in_memory")
    with TestClient(app) as client:
        container = app.state.container

        class _FakeEmbeddingService:
            def __init__(self) -> None:
                self.calls: list[str] = []

            async def embed_text(self, text: str) -> list[float]:
                self.calls.append(text)
                return [0.01, 0.02, 0.03]

        class _FakeMemoryRepo:
            def __init__(self) -> None:
                self.last_query: dict[str, Any] | None = None

            async def knn_search(
                self,
                tenant_id: str,
                scope: str,
                query_vector: list[float],
                top_k: int,
            ) -> list[dict[str, Any]]:
                self.last_query = {
                    "tenant_id": tenant_id,
                    "scope": scope,
                    "query_vector": query_vector,
                    "top_k": top_k,
                }
                return [{"namespaced_key": "tenant:session:task:summary"}]

        fake_embedding = _FakeEmbeddingService()
        fake_repo = _FakeMemoryRepo()
        container.embedding_service = fake_embedding
        container.memory_repo = fake_repo  # type: ignore[assignment]

        response = client.get(
            "/agent/memory/query",
            params={
                "tenant_id": "tenant_1",
                "user_id": "user_1",
                "query_text": "find latest summary",
                "top_k": 3,
                "scope": "session",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert fake_embedding.calls == ["find latest summary"]
    assert fake_repo.last_query is not None
    assert fake_repo.last_query["query_vector"] == [0.01, 0.02, 0.03]


def test_agent_run_returns_structured_failure_for_infeasible_plan(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENT_STORAGE_BACKEND", "in_memory")
    with TestClient(app) as client:
        container = app.state.container

        async def _raise_infeasible(_: Any) -> AgentRunResponse:
            raise PlanValidationError(
                "plan_infeasible_over_max_steps",
                failure_response={
                    "status": "failed",
                    "reason": "plan_infeasible_over_max_steps",
                    "max_steps": 10,
                    "actual_steps": 11,
                },
            )

        container.adk_runtime.run = _raise_infeasible  # type: ignore[method-assign]

        response = client.post(
            "/agent/run",
            json={
                "tenant_id": "tenant_1",
                "user_id": "user_1",
                "session_id": "session_1",
                "message": "hello",
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "plan_infeasible_over_max_steps"


def test_opensearch_backend_wires_stream_event_write_path(monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENT_STORAGE_BACKEND", "opensearch")

    class _FakeOpenSearch:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs

    class _FakeIndexManager:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs

        def ensure_indices_and_policies(self) -> None:
            return None

    class _FakeRedisClient:
        async def aclose(self) -> None:
            return None

    fake_redis = _FakeRedisClient()
    monkeypatch.setattr(api_main, "OpenSearch", _FakeOpenSearch)
    monkeypatch.setattr(api_main, "OpenSearchIndexManager", _FakeIndexManager)
    monkeypatch.setattr(
        api_main,
        "Redis",
        type("_FakeRedis", (), {"from_url": staticmethod(lambda _url: fake_redis)}),
    )

    settings = api_main.Settings()
    container = api_main.Container(settings)

    assert isinstance(container.event_repo, RedisStreamEventRepository)
    assert isinstance(container.events_consumer, RedisToOpenSearchEventConsumer)
    assert container.adk_runtime.event_repo is container.event_repo
