from agent_core.domain.exceptions import PlanValidationError
from agent_core.domain.models import PlanStep

_FORBIDDEN_SKILL_TOKENS = (
    "subagent",
    "spawn_subagent",
    "create_subagent",
    "agent/run",
)


def validate_plan_steps(steps: list[PlanStep], max_steps: int = 10) -> None:
    if not steps:
        msg = "planner_returned_empty_plan"
        raise PlanValidationError(
            msg,
            failure_response={
                "status": "failed",
                "reason": msg,
            },
        )
    if len(steps) > max_steps:
        msg = "plan_infeasible_over_max_steps"
        raise PlanValidationError(
            msg,
            failure_response={
                "status": "failed",
                "reason": msg,
                "max_steps": max_steps,
                "actual_steps": len(steps),
            },
        )

    for step in steps:
        for skill_name in step.skills:
            normalized = skill_name.strip().lower()
            if any(token in normalized for token in _FORBIDDEN_SKILL_TOKENS):
                msg = "subagent_spawning_not_allowed"
                raise PlanValidationError(
                    msg,
                    failure_response={
                        "status": "failed",
                        "reason": msg,
                        "step_index": step.step_index,
                        "skill": skill_name,
                    },
                )
