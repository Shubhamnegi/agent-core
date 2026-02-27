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
    with pytest.raises(PlanValidationError) as exc_info:
        validate_plan_steps(steps, max_steps=10)

    assert exc_info.value.failure_response is not None
    assert exc_info.value.failure_response["reason"] == "plan_infeasible_over_max_steps"


def test_validate_plan_rejects_subagent_spawn_skill() -> None:
    step = PlanStep(
        step_index=1,
        task="attempt subagent",
        skills=["spawn_subagent"],
        return_spec=ReturnSpec(shape={"value": "string"}, reason="test"),
    )

    with pytest.raises(PlanValidationError) as exc_info:
        validate_plan_steps([step], max_steps=10)

    assert exc_info.value.failure_response is not None
    assert exc_info.value.failure_response["reason"] == "subagent_spawning_not_allowed"
