from __future__ import annotations

from typing import Any

from agent_core.application.ports import (
    EventRepository,
    ExecutorAgent,
    MemoryRepository,
    MessageBusPublisher,
    PlannerAgent,
    PlanRepository,
)
from agent_core.application.services.execution_flow_service import ExecutionFlowService
from agent_core.application.services.memory_use_case import MemoryUseCaseService
from agent_core.application.services.plan_validator import validate_plan_steps
from agent_core.application.services.replan_manager import ReplanManager
from agent_core.application.services.step_state_machine import PlanStepStateMachine
from agent_core.domain.models import (
    AgentRunRequest,
    AgentRunResponse,
    EventRecord,
    Plan,
    PlanStatus,
)


class AgentOrchestrator:
    """Coordinates planning, execution, and bounded replanning for a user request.

    The orchestrator owns flow-control decisions only. Replanning policy and memory/
    locking behavior are delegated into dedicated use-case services for clearer boundaries.
    """

    def __init__(
        self,
        planner: PlannerAgent,
        executor: ExecutorAgent,
        plan_repo: PlanRepository,
        memory_repo: MemoryRepository,
        event_repo: EventRepository,
        message_bus: MessageBusPublisher | None = None,
        max_steps: int = 10,
        max_replans: int = 3,
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.plan_repo = plan_repo
        self.memory_repo = memory_repo
        self.event_repo = event_repo
        self.message_bus = message_bus or _NoopMessageBusPublisher()
        self.max_steps = max_steps
        self.max_replans = max_replans
        self.step_state_machine = PlanStepStateMachine()
        self.memory_use_case = MemoryUseCaseService(memory_repo=self.memory_repo)
        self.replan_manager = ReplanManager(
            planner=self.planner,
            plan_repo=self.plan_repo,
            event_repo=self.event_repo,
            max_steps=self.max_steps,
            max_replans=self.max_replans,
        )
        self.execution_flow_service = ExecutionFlowService(
            executor=self.executor,
            plan_repo=self.plan_repo,
            event_repo=self.event_repo,
            message_bus=self.message_bus,
            step_state_machine=self.step_state_machine,
            memory_use_case=self.memory_use_case,
            replan_manager=self.replan_manager,
        )

    async def run(self, request: AgentRunRequest) -> AgentRunResponse:
        """Handle one end-to-end run from user message to final response."""
        await self.event_repo.append(
            EventRecord(
                event_type="user_message.received",
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                plan_id=None,
                task_id=None,
                payload={"message_size": len(request.message)},
            )
        )

        planner_output = await self.planner.create_plan(request=request, max_steps=self.max_steps)
        validate_plan_steps(planner_output.steps, max_steps=self.max_steps)

        plan = Plan(
            session_id=request.session_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            steps=planner_output.steps,
            status=PlanStatus.EXECUTING,
        )
        await self.plan_repo.save(plan)

        await self.event_repo.append(
            EventRecord(
                event_type="plan.persisted",
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                plan_id=plan.plan_id,
                task_id=None,
                payload={"steps": len(plan.steps), "status": plan.status.value},
            )
        )

        response_text = await self.execution_flow_service.execute_plan(request, plan)
        return AgentRunResponse(
            status=plan.status.value,
            response=response_text,
            plan_id=plan.plan_id,
        )


class _NoopMessageBusPublisher:
    """Fallback publisher to keep orchestrator behavior stable without bus wiring."""

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        _ = (topic, payload)
