from agent_core.application.ports import ExecutorAgent
from agent_core.domain.models import AgentRunRequest, Plan, PlanStep, StepExecutionResult


class MockExecutorAgent(ExecutorAgent):
    async def execute_step(
        self,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
    ) -> StepExecutionResult:
        message_lower = request.message.lower()
        if "insufficient" in message_lower:
            return StepExecutionResult(
                status="insufficient",
                data=None,
                reason="single step cannot complete",
                suggestion="split task",
            )
        if "fail" in message_lower:
            return StepExecutionResult(status="failed", data=None, reason="simulated_failure")

        payload = {key: f"mock_{step.step_index}" for key in step.return_spec.shape.keys()}
        if "response_text" in payload:
            payload["response_text"] = "Mock execution successful"
        return StepExecutionResult(status="ok", data=payload)
