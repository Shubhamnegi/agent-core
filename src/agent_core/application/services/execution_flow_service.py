from __future__ import annotations

from uuid import uuid4

from agent_core.application.ports import (
    EventRepository,
    ExecutorAgent,
    MessageBusPublisher,
    PlanRepository,
)
from agent_core.application.services.memory_use_case import MemoryUseCaseService
from agent_core.application.services.replan_manager import ReplanManager
from agent_core.application.services.response_synthesis_service import ResponseSynthesisService
from agent_core.application.services.step_execution_handler_service import (
    StepExecutionHandlerService,
)
from agent_core.application.services.step_state_machine import PlanStepStateMachine
from agent_core.domain.models import (
    AgentRunRequest,
    EventRecord,
    Plan,
    PlanStatus,
    utc_now,
)


class ExecutionFlowService:
    """Runs plan-step execution loop and delegates state/memory/replan decisions.

    Why this service exists: the step loop is a standalone use case with multiple
    branches; extracting it keeps `AgentOrchestrator` focused on session-level flow.
    """

    def __init__(
        self,
        executor: ExecutorAgent,
        plan_repo: PlanRepository,
        event_repo: EventRepository,
        message_bus: MessageBusPublisher,
        step_state_machine: PlanStepStateMachine,
        memory_use_case: MemoryUseCaseService,
        replan_manager: ReplanManager,
    ) -> None:
        self.executor = executor
        self.plan_repo = plan_repo
        self.event_repo = event_repo
        self.message_bus = message_bus
        self.step_state_machine = step_state_machine
        self.step_handler = StepExecutionHandlerService(
            plan_repo=plan_repo,
            event_repo=event_repo,
            message_bus=message_bus,
            step_state_machine=step_state_machine,
            memory_use_case=memory_use_case,
            replan_manager=replan_manager,
        )
        self.response_synthesizer = ResponseSynthesisService(memory_use_case=memory_use_case)

    async def execute_plan(self, request: AgentRunRequest, plan: Plan) -> str:
        """Execute steps in-order and return synthesized response from memory outputs."""
        step_index = 0
        while step_index < len(plan.steps):
            step = plan.steps[step_index]
            self.step_state_machine.mark_running(step)
            step.task_id = f"task_{uuid4().hex[:10]}"

            await self.event_repo.append(
                EventRecord(
                    event_type="step.started",
                    tenant_id=request.tenant_id,
                    session_id=request.session_id,
                    plan_id=plan.plan_id,
                    task_id=step.task_id,
                    payload={"step_index": step.step_index, "skills": step.skills},
                )
            )
            await self.plan_repo.save(plan)

            execution = await self.executor.execute_step(request=request, plan=plan, step=step)
            if execution.status == "ok" and execution.data is not None:
                step_index = await self.step_handler.handle_successful_execution(
                    request=request,
                    plan=plan,
                    step=step,
                    execution=execution,
                    current_step_index=step_index,
                )
                continue

            if execution.status == "insufficient":
                step_index = await self.step_handler.handle_insufficient_execution(
                    request=request,
                    plan=plan,
                    step=step,
                    execution=execution,
                )
                continue

            step_index = await self.step_handler.handle_failed_execution(
                request=request,
                plan=plan,
                step=step,
                execution=execution,
            )

        plan.status = PlanStatus.COMPLETE
        plan.completed_at = utc_now()
        await self.plan_repo.save(plan)
        return await self.response_synthesizer.synthesize(plan)
