from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from agent_core.application.ports import (
    EventRepository,
    ExecutorAgent,
    MemoryRepository,
    MessageBusPublisher,
    PlannerAgent,
    PlanRepository,
)
from agent_core.application.services.plan_validator import validate_plan_steps
from agent_core.application.services.step_state_machine import PlanStepStateMachine
from agent_core.domain.exceptions import ReplanLimitReachedError
from agent_core.domain.models import (
    AgentRunRequest,
    AgentRunResponse,
    EventRecord,
    Plan,
    PlanStatus,
    PlanStep,
    ReplanEvent,
    StepExecutionResult,
    StepStatus,
    utc_now,
)


class AgentOrchestrator:
    """Coordinates planning, execution, and bounded replanning for a user request.

    The orchestrator owns flow-control decisions. Step state transitions are delegated
    to `PlanStepStateMachine` so transition rules stay consistent and auditable.
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

        response_text = await self._execute_plan(request, plan)
        return AgentRunResponse(
            status=plan.status.value,
            response=response_text,
            plan_id=plan.plan_id,
        )

    async def _execute_plan(self, request: AgentRunRequest, plan: Plan) -> str:
        """Execute plan steps sequentially and trigger replans on recoverable failures."""
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
                step_index = await self._handle_successful_execution(
                    request=request,
                    plan=plan,
                    step=step,
                    execution=execution,
                    current_step_index=step_index,
                )
                continue

            if execution.status == "insufficient":
                step_index = await self._handle_insufficient_execution(
                    request=request,
                    plan=plan,
                    step=step,
                    execution=execution,
                )
                continue

            step_index = await self._handle_failed_execution(
                request=request,
                plan=plan,
                step=step,
                execution=execution,
            )

        plan.status = PlanStatus.COMPLETE
        plan.completed_at = utc_now()
        await self.plan_repo.save(plan)
        return await self._synthesize_response(plan)

    async def _handle_successful_execution(
        self,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
        execution: StepExecutionResult,
        current_step_index: int,
    ) -> int:
        """Handle success path, including contract violation fallback before persistence."""
        if execution.data is None:
            return current_step_index

        if not self._matches_return_spec(execution.data, step.return_spec.shape):
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
            await self._replan_or_fail(
                request=request,
                plan=plan,
                failed_step=step,
                trigger="contract_violation",
            )
            await self._publish_step_lifecycle(
                event_type="step.failed",
                request=request,
                plan=plan,
                step=step,
            )
            return self.step_state_machine.next_pending_step_index(plan)

        memory_key = await self._write_validated_step_output(
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
        await self._publish_step_lifecycle(
            event_type="step.complete",
            request=request,
            plan=plan,
            step=step,
        )
        await self.plan_repo.save(plan)
        return current_step_index + 1

    async def _handle_insufficient_execution(
        self,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
        execution: StepExecutionResult,
    ) -> int:
        """Handle insufficient path by failing step and invoking bounded replanning."""
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
        await self._publish_step_lifecycle(
            event_type="step.failed",
            request=request,
            plan=plan,
            step=step,
        )
        await self._replan_or_fail(
            request=request,
            plan=plan,
            failed_step=step,
            trigger="insufficient",
        )
        return self.step_state_machine.next_pending_step_index(plan)

    async def _handle_failed_execution(
        self,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
        execution: StepExecutionResult,
    ) -> int:
        """Handle generic executor failure and schedule replanning."""
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
        await self._publish_step_lifecycle(
            event_type="step.failed",
            request=request,
            plan=plan,
            step=step,
        )
        await self._replan_or_fail(
            request=request,
            plan=plan,
            failed_step=step,
            trigger="step_failed",
        )
        return self.step_state_machine.next_pending_step_index(plan)

    async def _replan_or_fail(
        self,
        request: AgentRunRequest,
        plan: Plan,
        failed_step: PlanStep,
        trigger: str,
    ) -> None:
        """Attempt surgical replan; fail fast when the replan budget is exhausted."""
        if plan.replan_count >= self.max_replans:
            completed_steps = [s for s in plan.steps if s.status == StepStatus.COMPLETE]
            plan.status = PlanStatus.FAILED
            await self.plan_repo.save(plan)
            failure_response = {
                "status": "failed",
                "reason": "max replan attempts reached",
                "completed_steps": [
                    {
                        "step_index": step.step_index,
                        "task": step.task,
                        "status": step.status.value,
                        "memory_key": step.memory_key,
                    }
                    for step in completed_steps
                ],
                "last_failure": {
                    "step": failed_step.step_index,
                    "reason": failed_step.failure_reason or "unknown_failure",
                },
            }
            raise ReplanLimitReachedError(
                message="max replan attempts reached",
                failure_response=failure_response,
            )

        plan.replan_count += 1
        plan.status = PlanStatus.REPLANNING
        completed_steps = [s for s in plan.steps if s.status == StepStatus.COMPLETE]
        remaining_steps = [
            step
            for step in plan.steps
            if step.status != StepStatus.COMPLETE and step != failed_step
        ]

        await self.event_repo.append(
            EventRecord(
                event_type="replan.triggered",
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                plan_id=plan.plan_id,
                task_id=failed_step.task_id,
                payload={
                    "attempt": plan.replan_count,
                    "failed_step": failed_step.step_index,
                    "reason": failed_step.failure_reason,
                },
            )
        )

        revised = await self.planner.replan(
            request=request,
            completed_steps=completed_steps,
            failed_step=failed_step,
            reason=failed_step.failure_reason or "step_failed",
            max_steps=self.max_steps,
        )
        validate_plan_steps(revised.steps, max_steps=self.max_steps)

        plan.replan_history.append(
            ReplanEvent(
                attempt=plan.replan_count,
                trigger=trigger,
                failed_step=failed_step.step_index,
                reason=failed_step.failure_reason or "step_failed",
            )
        )
        plan.steps = completed_steps + revised.steps + remaining_steps
        plan.status = PlanStatus.EXECUTING
        await self.plan_repo.save(plan)

    def _matches_return_spec(self, data: dict[str, object], shape: dict[str, object]) -> bool:
        """Use a minimal key-subset check as the current contract gate for scaffold data."""
        return set(shape.keys()).issubset(data.keys())

    async def _write_validated_step_output(
        self,
        request: AgentRunRequest,
        step: PlanStep,
        data: dict[str, object],
    ) -> str:
        """Persist validated step output through memory adapter as the single write path."""
        if step.task_id is None:
            msg = "step task_id must be set before memory write"
            raise ValueError(msg)
        return await self.memory_repo.write(
            tenant_id=request.tenant_id,
            session_id=request.session_id,
            task_id=step.task_id,
            key=f"step_{step.step_index}_output",
            value=data,
            return_spec_shape=step.return_spec.shape,
        )

    async def _publish_step_lifecycle(
        self,
        event_type: str,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
    ) -> None:
        """Emit lightweight lifecycle messages for downstream orchestration/monitoring."""
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

    async def _synthesize_response(self, plan: Plan) -> str:
        """Build final user response from persisted memory outputs, not transient executor data."""
        completed = [step for step in plan.steps if step.status == StepStatus.COMPLETE]
        if not completed:
            return "No steps completed."

        outputs: list[dict[str, Any]] = []
        for step in completed:
            if step.memory_key is None:
                continue
            value = await self.memory_repo.read(step.memory_key)
            if isinstance(value, dict):
                outputs.append(value)

        if not outputs:
            return "Execution complete."

        final_output = outputs[-1]
        response_text = final_output.get("response_text")
        if isinstance(response_text, str) and response_text.strip():
            return response_text

        return "Execution complete. " + json.dumps(final_output, sort_keys=True)


class _NoopMessageBusPublisher:
    """Fallback publisher to keep orchestrator behavior stable without bus wiring."""

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        _ = (topic, payload)
