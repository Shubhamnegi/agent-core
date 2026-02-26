from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from starlette.responses import Response

from agent_core.api.schemas import AgentRunPayload, AgentRunResult, MemoryQueryPayload, SoulPayload
from agent_core.application.services.orchestrator import AgentOrchestrator
from agent_core.domain.exceptions import ReplanLimitReachedError
from agent_core.domain.models import AgentRunRequest
from agent_core.infra.adapters.in_memory import (
    InMemoryEventRepository,
    InMemoryMemoryRepository,
    InMemoryMessageBusPublisher,
    InMemoryPlanRepository,
    InMemorySoulRepository,
    event_to_dict,
)
from agent_core.infra.adk.runtime import AdkRuntimeScaffold
from agent_core.infra.agents.mock_executor import MockExecutorAgent
from agent_core.infra.agents.mock_planner import MockPlannerAgent
from agent_core.infra.config import Settings
from agent_core.infra.logging import configure_logging, request_id_ctx

logger = logging.getLogger(__name__)


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runtime_engine = settings.runtime_engine
        self.plan_repo = InMemoryPlanRepository()
        self.memory_repo = InMemoryMemoryRepository()
        self.event_repo = InMemoryEventRepository()
        self.message_bus = InMemoryMessageBusPublisher()
        self.soul_repo = InMemorySoulRepository()
        self.adk_runtime = AdkRuntimeScaffold(
            app_name=settings.app_name,
            max_replans=settings.max_replans,
            mcp_server_url=settings.mcp_server_url,
            event_repo=self.event_repo,
        )
        self.orchestrator = AgentOrchestrator(
            planner=MockPlannerAgent(),
            executor=MockExecutorAgent(),
            plan_repo=self.plan_repo,
            memory_repo=self.memory_repo,
            event_repo=self.event_repo,
            message_bus=self.message_bus,
            max_steps=settings.max_plan_steps,
            max_replans=settings.max_replans,
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
        if container.runtime_engine == "adk_scaffold":
            result = await container.adk_runtime.run(request_model)
        else:
            result = await container.orchestrator.run(request_model)
    except ReplanLimitReachedError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.failure_response
            or {"status": "failed", "reason": str(exc)},
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
async def query_memory(payload: MemoryQueryPayload) -> dict[str, Any]:
    return {
        "status": "not_implemented",
        "reason": "wire to OpenSearch adapter",
        "query": payload.model_dump(),
    }
