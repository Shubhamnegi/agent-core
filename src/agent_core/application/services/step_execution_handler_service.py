from __future__ import annotations

from agent_core.application.ports import (
    EventRepository,
    MessageBusPublisher,
    PlanRepository,
)
from agent_core.application.services.memory_use_case import MemoryUseCaseService
from agent_core.application.services.replan_manager import ReplanManager
from agent_core.application.services.step_state_machine import PlanStepStateMachine
from agent_core.domain.models import (
    AgentRunRequest,
    EventRecord,
    Plan,
    PlanStep,
    StepExecutionResult,
)


class StepExecutionHandlerService:
    """Handles step outcome branches during execution loop.

    Why this service exists: success/insufficient/failure branches are a separate
    use case from iteration control and become easier to reason about in isolation.
    """

    def __init__(
        self,
        plan_repo: PlanRepository,
        event_repo: EventRepository,
        message_bus: MessageBusPublisher,
        step_state_machine: PlanStepStateMachine,
        memory_use_case: MemoryUseCaseService,
        replan_manager: ReplanManager,
    ) -> None:
        self.plan_repo = plan_repo
        self.event_repo = event_repo
        self.message_bus = message_bus
        self.step_state_machine = step_state_machine
        self.memory_use_case = memory_use_case
        self.replan_manager = replan_manager

    async def handle_successful_execution(
        self,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
        execution: StepExecutionResult,
        current_step_index: int,
    ) -> int:
        if execution.data is None:
            return current_step_index

        if not self.memory_use_case.matches_return_spec(execution.data, step.return_spec.shape):
            self.step_state_machine.mark_failed(step, "contract_violation")
            await self.event_repo.append(
                EventRecord(
                    event_type="step.contract_violation",
                    tenant_id=request.tenant_id,
                    session_id=request.session_id,
                    plan_id=plan.plan_id,
                    task_id=step.task_id,
                    payload={
                        "step_index": step.step_index,
                        "expected_keys": sorted(step.return_spec.shape.keys()),
                        "actual_keys": sorted(execution.data.keys()),
                    },
                )
            )
            await self.replan_manager.replan_or_fail(
                request=request,
                plan=plan,
                failed_step=step,
                trigger="contract_violation",
            )
            await self.publish_step_lifecycle(
                event_type="step.failed",
                request=request,
                plan=plan,
                step=step,
            )
            return self.step_state_machine.next_pending_step_index(plan)

        memory_key = await self.memory_use_case.write_step_output(
            request=request,
            step=step,
            data=execution.data,
        )
        self.step_state_machine.mark_complete(step)
        step.validated = True
        step.memory_key = memory_key
        await self.event_repo.append(
            EventRecord(
                event_type="step.complete",
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                plan_id=plan.plan_id,
                task_id=step.task_id,
                payload={"step_index": step.step_index, "memory_key": memory_key},
            )
        )
        await self.publish_step_lifecycle(
            event_type="step.complete",
            request=request,
            plan=plan,
            step=step,
        )
        await self.plan_repo.save(plan)
        return current_step_index + 1

    async def handle_insufficient_execution(
        self,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
        execution: StepExecutionResult,
    ) -> int:
        self.step_state_machine.mark_failed(step, execution.reason or "insufficient")
        await self.event_repo.append(
            EventRecord(
                event_type="step.insufficient",
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                plan_id=plan.plan_id,
                task_id=step.task_id,
                payload={
                    "step_index": step.step_index,
                    "reason": step.failure_reason,
                    "suggestion": execution.suggestion,
                },
            )
        )
        await self.publish_step_lifecycle(
            event_type="step.failed",
            request=request,
            plan=plan,
            step=step,
        )
        await self.replan_manager.replan_or_fail(
            request=request,
            plan=plan,
            failed_step=step,
            trigger="insufficient",
        )
        return self.step_state_machine.next_pending_step_index(plan)

    async def handle_failed_execution(
        self,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
        execution: StepExecutionResult,
    ) -> int:
        self.step_state_machine.mark_failed(step, execution.reason or "unknown_failure")
        await self.event_repo.append(
            EventRecord(
                event_type="step.failed",
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                plan_id=plan.plan_id,
                task_id=step.task_id,
                payload={
                    "step_index": step.step_index,
                    "reason": step.failure_reason,
                    "suggestion": execution.suggestion,
                },
            )
        )
        await self.publish_step_lifecycle(
            event_type="step.failed",
            request=request,
            plan=plan,
            step=step,
        )
        await self.replan_manager.replan_or_fail(
            request=request,
            plan=plan,
            failed_step=step,
            trigger="step_failed",
        )
        return self.step_state_machine.next_pending_step_index(plan)

    async def publish_step_lifecycle(
        self,
        event_type: str,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
    ) -> None:
        """Emit step lifecycle event for downstream monitoring and control channels."""
        await self.message_bus.publish(
            topic=event_type,
            payload={
                "tenant_id": request.tenant_id,
                "session_id": request.session_id,
                "plan_id": plan.plan_id,
                "task_id": step.task_id,
                "step_index": step.step_index,
                "status": step.status.value,
                "failure_reason": step.failure_reason,
            },
        )
