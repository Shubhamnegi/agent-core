import pytest

from agent_core.application.services.plan_validator import validate_plan_steps
from agent_core.domain.exceptions import PlanValidationError
from agent_core.domain.models import PlanStep, ReturnSpec


def _step(index: int) -> PlanStep:
    return PlanStep(
        step_index=index,
        task=f"step-{index}",
        skills=["skill_x"],
        return_spec=ReturnSpec(shape={"value": "string"}, reason="test"),
    )


def test_validate_plan_rejects_empty() -> None:
    with pytest.raises(PlanValidationError):
        validate_plan_steps([], max_steps=10)


def test_validate_plan_rejects_more_than_max() -> None:
    steps = [_step(i) for i in range(1, 12)]
    with pytest.raises(PlanValidationError):
        validate_plan_steps(steps, max_steps=10)
