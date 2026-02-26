from __future__ import annotations

import json

from agent_core.application.services.memory_use_case import MemoryUseCaseService
from agent_core.domain.models import Plan


class ResponseSynthesisService:
    """Builds final response from memory outputs.

    Why this service exists: response synthesis is distinct from execution branching,
    and isolating it keeps the execution loop compact and easier to debug.
    """

    def __init__(self, memory_use_case: MemoryUseCaseService) -> None:
        self.memory_use_case = memory_use_case

    async def synthesize(self, plan: Plan) -> str:
        has_completed_steps = any(step.status.value == "complete" for step in plan.steps)
        outputs = await self.memory_use_case.read_completed_outputs_and_release_locks(plan)

        if not outputs:
            return "Execution complete." if has_completed_steps else "No steps completed."

        final_output = outputs[-1]
        response_text = final_output.get("response_text")
        if isinstance(response_text, str) and response_text.strip():
            return response_text

        return "Execution complete. " + json.dumps(final_output, sort_keys=True)
