from agent_core.application.ports import PlannerAgent
from agent_core.domain.exceptions import PlanValidationError
from agent_core.domain.models import AgentRunRequest, PlannerOutput, PlanStep, ReturnSpec


class MockPlannerAgent(PlannerAgent):
    _SKILL_OUTPUT_SCHEMAS: dict[str, set[str]] = {
        "skill_intent_analyzer": {"intent"},
        "skill_response_builder": {"response_text"},
    }

    async def create_plan(self, request: AgentRunRequest, max_steps: int = 10) -> PlannerOutput:
        _ = request
        steps = [
            PlanStep(
                step_index=1,
                task="Analyze request intent",
                skills=["skill_intent_analyzer"],
                return_spec=ReturnSpec(shape={"intent": "string"}, reason="Used in step 2"),
            ),
            PlanStep(
                step_index=2,
                task="Build actionable response",
                skills=["skill_response_builder"],
                input_from_step=1,
                return_spec=ReturnSpec(
                    shape={"response_text": "string"}, reason="Final user output synthesis"
                ),
            ),
        ]
        if len(steps) > max_steps:
            raise PlanValidationError(
                "plan_infeasible_over_max_steps",
                failure_response={
                    "status": "failed",
                    "reason": "plan_infeasible_over_max_steps",
                    "max_steps": max_steps,
                    "actual_steps": len(steps),
                },
            )
        self._validate_return_specs(steps)
        return PlannerOutput(steps=steps)

    async def replan(
        self,
        request: AgentRunRequest,
        completed_steps: list[PlanStep],
        failed_step: PlanStep,
        reason: str,
        max_steps: int = 10,
    ) -> PlannerOutput:
        _ = (request, completed_steps, reason)
        revised = [
            PlanStep(
                step_index=failed_step.step_index,
                task=f"Retry: {failed_step.task}",
                skills=failed_step.skills,
                return_spec=failed_step.return_spec,
                input_from_step=failed_step.input_from_step,
            )
        ]
        if len(revised) > max_steps:
            raise PlanValidationError(
                "plan_infeasible_over_max_steps",
                failure_response={
                    "status": "failed",
                    "reason": "plan_infeasible_over_max_steps",
                    "max_steps": max_steps,
                    "actual_steps": len(revised),
                },
            )
        self._validate_return_specs(revised)
        return PlannerOutput(steps=revised)

    def _validate_return_specs(self, steps: list[PlanStep]) -> None:
        for step in steps:
            expected_keys = set(step.return_spec.shape.keys())
            skill_output_keys = self._union_skill_output_keys(step.skills)
            if not expected_keys.issubset(skill_output_keys):
                raise PlanValidationError(
                    "planner_return_spec_not_satisfiable",
                    failure_response={
                        "status": "failed",
                        "reason": "planner_return_spec_not_satisfiable",
                        "step_index": step.step_index,
                        "missing_keys": sorted(expected_keys - skill_output_keys),
                    },
                )

    def _union_skill_output_keys(self, skills: list[str]) -> set[str]:
        collected: set[str] = set()
        for skill in skills:
            collected |= self._SKILL_OUTPUT_SCHEMAS.get(skill, set())
        return collected
