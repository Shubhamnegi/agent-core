from agent_core.domain.exceptions import PlanValidationError
from agent_core.domain.models import PlanStep


def validate_plan_steps(steps: list[PlanStep], max_steps: int = 10) -> None:
    if not steps:
        msg = "Planner returned empty plan"
        raise PlanValidationError(msg)
    if len(steps) > max_steps:
        msg = f"Plan exceeds max steps ({max_steps})"
        raise PlanValidationError(msg)
