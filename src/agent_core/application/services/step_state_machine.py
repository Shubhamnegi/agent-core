from __future__ import annotations

from agent_core.domain.models import Plan, PlanStep, StepStatus, utc_now


class PlanStepStateMachine:
    """Owns step lifecycle transitions to keep orchestration flow focused on execution.

    Centralizing transitions here makes invalid status moves fail fast with explicit
    errors, which improves debuggability when execution loops become complex.
    """

    def mark_running(self, step: PlanStep) -> None:
        """Move a step from pending to running and stamp start time."""
        if step.status != StepStatus.PENDING:
            msg = f"invalid transition to running from {step.status.value}"
            raise ValueError(msg)
        step.status = StepStatus.RUNNING
        step.started_at = utc_now()
        step.finished_at = None

    def mark_complete(self, step: PlanStep) -> None:
        """Move a running step to complete and stamp finish time."""
        if step.status != StepStatus.RUNNING:
            msg = f"invalid transition to complete from {step.status.value}"
            raise ValueError(msg)
        step.status = StepStatus.COMPLETE
        step.finished_at = utc_now()

    def mark_failed(self, step: PlanStep, reason: str) -> None:
        """Move a running step to failed, preserving failure reason for diagnostics."""
        if step.status != StepStatus.RUNNING:
            msg = f"invalid transition to failed from {step.status.value}"
            raise ValueError(msg)
        step.status = StepStatus.FAILED
        step.failure_reason = reason
        step.finished_at = utc_now()

    def next_pending_step_index(self, plan: Plan) -> int:
        """Return the next non-complete step index used to resume execution after replans."""
        for index, step in enumerate(plan.steps):
            if step.status != StepStatus.COMPLETE:
                return index
        return len(plan.steps)
