from agent_core.application.ports import PlannerAgent
from agent_core.domain.models import AgentRunRequest, PlannerOutput, PlanStep, ReturnSpec


class MockPlannerAgent(PlannerAgent):
    async def create_plan(self, request: AgentRunRequest, max_steps: int = 10) -> PlannerOutput:
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
        return PlannerOutput(steps=steps[:max_steps])

    async def replan(
        self,
        request: AgentRunRequest,
        completed_steps: list[PlanStep],
        failed_step: PlanStep,
        reason: str,
        max_steps: int = 10,
    ) -> PlannerOutput:
        revised = [
            PlanStep(
                step_index=failed_step.step_index,
                task=f"Retry: {failed_step.task}",
                skills=failed_step.skills,
                return_spec=failed_step.return_spec,
                input_from_step=failed_step.input_from_step,
            )
        ]
        return PlannerOutput(steps=revised[:max_steps])
