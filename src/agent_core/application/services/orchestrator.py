from __future__ import annotations

from uuid import uuid4

from agent_core.application.ports import (
    EventRepository,
    ExecutorAgent,
    MemoryRepository,
    PlannerAgent,
    PlanRepository,
)
from agent_core.application.services.plan_validator import validate_plan_steps
from agent_core.domain.exceptions import ReplanLimitReachedError
from agent_core.domain.models import (
    AgentRunRequest,
    AgentRunResponse,
    EventRecord,
    Plan,
    PlanStatus,
    PlanStep,
    ReplanEvent,
    StepStatus,
)


class AgentOrchestrator:
    def __init__(
        self,
        planner: PlannerAgent,
        executor: ExecutorAgent,
        plan_repo: PlanRepository,
        memory_repo: MemoryRepository,
        event_repo: EventRepository,
        max_steps: int = 10,
        max_replans: int = 3,
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.plan_repo = plan_repo
        self.memory_repo = memory_repo
        self.event_repo = event_repo
        self.max_steps = max_steps
        self.max_replans = max_replans

    async def run(self, request: AgentRunRequest) -> AgentRunResponse:
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
        step_index = 0
        while step_index < len(plan.steps):
            step = plan.steps[step_index]
            step.status = StepStatus.RUNNING
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

            execution = await self.executor.execute_step(request=request, plan=plan, step=step)
            if execution.status == "ok" and execution.data is not None:
                memory_key = await self.memory_repo.write(
                    tenant_id=request.tenant_id,
                    session_id=request.session_id,
                    task_id=step.task_id,
                    key=f"step_{step.step_index}_output",
                    value=execution.data,
                    return_spec_shape=step.return_spec.shape,
                )
                step.status = StepStatus.COMPLETE
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
                step_index += 1
                await self.plan_repo.save(plan)
                continue

            step.status = StepStatus.FAILED
            step.failure_reason = execution.reason or "unknown_failure"
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
            await self._replan_or_fail(request=request, plan=plan, failed_step=step)
            step_index += 1

        plan.status = PlanStatus.COMPLETE
        await self.plan_repo.save(plan)
        return self._synthesize_response(plan)

    async def _replan_or_fail(
        self,
        request: AgentRunRequest,
        plan: Plan,
        failed_step: PlanStep,
    ) -> None:
        if plan.replan_count >= self.max_replans:
            plan.status = PlanStatus.FAILED
            await self.plan_repo.save(plan)
            msg = "max replan attempts reached"
            raise ReplanLimitReachedError(msg)

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
                trigger="step_failed",
                failed_step=failed_step.step_index,
                reason=failed_step.failure_reason or "step_failed",
            )
        )
        plan.steps = completed_steps + revised.steps + remaining_steps
        plan.status = PlanStatus.EXECUTING
        await self.plan_repo.save(plan)

    def _synthesize_response(self, plan: Plan) -> str:
        completed = [step for step in plan.steps if step.status == StepStatus.COMPLETE]
        if not completed:
            return "No steps completed."
        parts = [f"Step {step.step_index}: {step.task}" for step in completed]
        return "Execution complete. " + " | ".join(parts)
