from __future__ import annotations

from agent_core.application.ports import EventRepository, PlannerAgent, PlanRepository
from agent_core.application.services.plan_validator import validate_plan_steps
from agent_core.domain.exceptions import ReplanLimitReachedError
from agent_core.domain.models import (
    AgentRunRequest,
    EventRecord,
    Plan,
    PlanStatus,
    PlanStep,
    ReplanEvent,
    StepStatus,
)


class ReplanManager:
    """Owns bounded replanning policy and plan merge behavior.

    Why this service exists: replanning mixes policy decisions (max attempts,
    failure shape) with plan-state mutation, which is a separate use case from
    step execution.
    """

    def __init__(
        self,
        planner: PlannerAgent,
        plan_repo: PlanRepository,
        event_repo: EventRepository,
        max_steps: int,
        max_replans: int,
    ) -> None:
        self.planner = planner
        self.plan_repo = plan_repo
        self.event_repo = event_repo
        self.max_steps = max_steps
        self.max_replans = max_replans

    async def replan_or_fail(
        self,
        request: AgentRunRequest,
        plan: Plan,
        failed_step: PlanStep,
        trigger: str,
    ) -> None:
        """Attempt a surgical replan; raise structured failure when budget is exhausted."""
        if plan.replan_count >= self.max_replans:
            completed_steps = [step for step in plan.steps if step.status == StepStatus.COMPLETE]
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
        completed_steps = [step for step in plan.steps if step.status == StepStatus.COMPLETE]
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
