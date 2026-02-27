from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, cast
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from opensearchpy import OpenSearch
from starlette.responses import Response

from agent_core.api.schemas import AgentRunPayload, AgentRunResult, MemoryQueryPayload, SoulPayload
from agent_core.application.ports import (
    EventRepository,
    MemoryRepository,
    PlanRepository,
    SoulRepository,
)
from agent_core.domain.exceptions import PlanValidationError, ReplanLimitReachedError
from agent_core.domain.models import AgentRunRequest
from agent_core.infra.adapters.embedding import AdkEmbeddingService, EmbeddingService
from agent_core.infra.adapters.in_memory import (
    InMemoryEventRepository,
    InMemoryMemoryRepository,
    InMemoryPlanRepository,
    InMemorySoulRepository,
    event_to_dict,
)
from agent_core.infra.adapters.opensearch import (
    OpenSearchEventRepository,
    OpenSearchIndexManager,
    OpenSearchMemoryRepository,
    OpenSearchPlanRepository,
    OpenSearchSoulRepository,
)
from agent_core.infra.adk.runtime import AdkRuntimeScaffold
from agent_core.infra.config import Settings
from agent_core.infra.logging import configure_logging, request_id_ctx

logger = logging.getLogger(__name__)


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.plan_repo: PlanRepository
        self.memory_repo: MemoryRepository
        self.event_repo: EventRepository
        self.soul_repo: SoulRepository
        self.embedding_service: EmbeddingService | None = None

        if settings.storage_backend == "opensearch":
            # Why lazy-by-config: keeps local dev/tests stable without requiring OpenSearch uptime.
            client = OpenSearch(
                hosts=[settings.opensearch_url],
                verify_certs=settings.opensearch_verify_certs,
                ssl_show_warn=False,
            )
            OpenSearchIndexManager(
                client=client,
                index_prefix=settings.opensearch_index_prefix,
                embedding_dims=settings.opensearch_embedding_dims,
                events_retention_days=settings.opensearch_events_retention_days,
            ).ensure_indices_and_policies()

            self.embedding_service = AdkEmbeddingService(
                model_name=settings.embedding_model_name,
                output_dimensionality=settings.embedding_output_dimensionality,
            )

            self.plan_repo = OpenSearchPlanRepository(
                client=client,
                index_prefix=settings.opensearch_index_prefix,
            )
            self.memory_repo = OpenSearchMemoryRepository(
                client=client,
                index_prefix=settings.opensearch_index_prefix,
                embedding_service=self.embedding_service,
                expected_embedding_dims=settings.opensearch_embedding_dims,
            )
            self.event_repo = OpenSearchEventRepository(
                client=client,
                index_prefix=settings.opensearch_index_prefix,
            )
            self.soul_repo = OpenSearchSoulRepository(
                client=client,
                index_prefix=settings.opensearch_index_prefix,
            )
        else:
            self.plan_repo = InMemoryPlanRepository()
            self.memory_repo = InMemoryMemoryRepository()
            self.event_repo = InMemoryEventRepository()
            self.soul_repo = InMemorySoulRepository()
        self.adk_runtime = AdkRuntimeScaffold(
            app_name=settings.app_name,
            max_replans=settings.max_replans,
            model_name=settings.model_name,
            mcp_config_path=settings.mcp_config_path,
            skill_service_url=settings.skill_service_url,
            skill_service_key=settings.skill_service_key,
            event_repo=self.event_repo,
            memory_repo=self.memory_repo,
            embedding_service=self.embedding_service,
            mcp_session_timeout=settings.mcp_session_timeout,
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    configure_logging(settings.log_level)
    app.state.container = Container(settings)
    logger.info("application_started")
    yield
    logger.info("application_stopped")


app = FastAPI(title="Agent Core", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def request_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request_id = request.headers.get("X-Request-Id", str(uuid4()))
    token = request_id_ctx.set(request_id)
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response
    finally:
        request_id_ctx.reset(token)


@app.post("/agent/run", response_model=AgentRunResult)
async def run_agent(
    request: Request,
    payload: AgentRunPayload,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> AgentRunResult:
    tenant_id = x_tenant_id or payload.tenant_id
    user_id = x_user_id or payload.user_id
    session_id = x_session_id or payload.session_id

    request_model = AgentRunRequest(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        message=payload.message,
    )

    container = cast(Container, app.state.container)
    try:
        container.adk_runtime.configure_mcp_for_request(dict(request.headers))
        result = await container.adk_runtime.run(request_model)
    except PlanValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.failure_response
            or {"status": "failed", "reason": str(exc)},
        ) from exc
    except ReplanLimitReachedError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.failure_response
            or {"status": "failed", "reason": str(exc)},
        ) from exc
    except Exception as exc:
        logger.exception(
            "agent_run_unhandled_error",
            extra={
                "tenant_id": tenant_id,
                "user_id": user_id,
                "session_id": session_id,
                "error_type": type(exc).__name__,
            },
        )
        raise HTTPException(
            status_code=500,
            detail={"status": "failed", "reason": "internal_error"},
        ) from exc

    return AgentRunResult(status=result.status, response=result.response, plan_id=result.plan_id)


@app.get("/agent/plans/{plan_id}")
async def get_plan(plan_id: str) -> dict[str, Any]:
    container = cast(Container, app.state.container)
    plan = await container.plan_repo.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {
        "plan_id": plan.plan_id,
        "session_id": plan.session_id,
        "tenant_id": plan.tenant_id,
        "user_id": plan.user_id,
        "status": plan.status.value,
        "replan_count": plan.replan_count,
        "steps": [
            {
                "step_index": step.step_index,
                "task": step.task,
                "skills": step.skills,
                "status": step.status.value,
                "task_id": step.task_id,
                "memory_key": step.memory_key,
                "failure_reason": step.failure_reason,
            }
            for step in plan.steps
        ],
    }


@app.get("/agent/plans/{plan_id}/trace")
async def get_trace(plan_id: str) -> dict[str, Any]:
    container = cast(Container, app.state.container)
    events = await container.event_repo.list_by_plan(plan_id)
    return {"plan_id": plan_id, "events": event_to_dict(events)}


@app.put("/agent/souls/{tenant_id}")
async def upsert_soul(tenant_id: str, payload: SoulPayload) -> dict[str, str]:
    container = cast(Container, app.state.container)
    await container.soul_repo.upsert(tenant_id, payload.user_id, payload.model_dump())
    return {"status": "ok"}


@app.get("/agent/memory/query")
async def query_memory(payload: Annotated[MemoryQueryPayload, Depends()]) -> dict[str, Any]:
    container = cast(Container, app.state.container)
    if container.embedding_service is None:
        return {
            "status": "not_implemented",
            "reason": "embedding_service_not_configured",
            "query": payload.model_dump(),
        }

    knn_search = getattr(container.memory_repo, "knn_search", None)
    if not callable(knn_search):
        return {
            "status": "not_implemented",
            "reason": "memory_backend_has_no_knn",
            "query": payload.model_dump(),
        }

    query_vector = await container.embedding_service.embed_text(payload.query_text)
    results = await knn_search(
        tenant_id=payload.tenant_id,
        scope=payload.scope,
        query_vector=query_vector,
        top_k=payload.top_k,
    )
    return {
        "status": "ok",
        "results": results,
        "count": len(results),
    }
