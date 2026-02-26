from __future__ import annotations

from typing import Any

from agent_core.application.ports import MemoryRepository
from agent_core.domain.models import AgentRunRequest, Plan, PlanStep, StepStatus


class MemoryUseCaseService:
    """Encapsulates memory contract checks and read-confirmed lock release.

    Why this service exists: memory and locking semantics are a distinct use case
    from orchestration control flow and should be testable independently.
    """

    def __init__(self, memory_repo: MemoryRepository) -> None:
        self.memory_repo = memory_repo

    def matches_return_spec(self, data: dict[str, object], shape: dict[str, object]) -> bool:
        """Minimal contract gate: required keys from return spec must exist in output."""
        return set(shape.keys()).issubset(data.keys())

    async def write_step_output(
        self,
        request: AgentRunRequest,
        step: PlanStep,
        data: dict[str, object],
    ) -> str:
        """Persist validated step output through the repository boundary.

        Why this check matters: task_id is the lock owner identity in memory adapters,
        so write must never proceed without it.
        """
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

    async def read_completed_outputs_and_release_locks(self, plan: Plan) -> list[dict[str, Any]]:
        """Read completed outputs and release write locks after read confirmation.

        Why `release_lock=True`: lock lifecycle ends when orchestrator confirms output
        consumption, preventing stale lock contention for future writes.
        """
        outputs: list[dict[str, Any]] = []
        completed_steps = [step for step in plan.steps if step.status == StepStatus.COMPLETE]
        for step in completed_steps:
            if step.memory_key is None:
                continue
            value = await self.memory_repo.read(step.memory_key, release_lock=True)
            if isinstance(value, dict):
                outputs.append(value)
        return outputs
