from typing import Any

import pytest

from agent_core.application.ports import ExecutorAgent
from agent_core.application.services.orchestrator import AgentOrchestrator
from agent_core.domain.exceptions import ReplanLimitReachedError
from agent_core.domain.models import (
    AgentRunRequest,
    Plan,
    PlannerOutput,
    PlanStep,
    ReturnSpec,
    StepExecutionResult,
)
from agent_core.infra.adapters.in_memory import (
    InMemoryEventRepository,
    InMemoryMemoryRepository,
    InMemoryPlanRepository,
)
from agent_core.infra.agents.mock_executor import MockExecutorAgent
from agent_core.infra.agents.mock_planner import MockPlannerAgent


def _orchestrator(max_replans: int = 1) -> AgentOrchestrator:
    return AgentOrchestrator(
        planner=MockPlannerAgent(),
        executor=MockExecutorAgent(),
        plan_repo=InMemoryPlanRepository(),
        memory_repo=InMemoryMemoryRepository(),
        event_repo=InMemoryEventRepository(),
        max_steps=10,
        max_replans=max_replans,
    )


class InsufficientThenSuccessExecutor(ExecutorAgent):
    def __init__(self) -> None:
        self._seen_steps: set[int] = set()

    async def execute_step(
        self,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
    ) -> StepExecutionResult:
        if step.step_index not in self._seen_steps:
            self._seen_steps.add(step.step_index)
            return StepExecutionResult(
                status="insufficient",
                data=None,
                reason="single step cannot complete",
                suggestion="split task",
            )

        payload = {key: f"mock_{step.step_index}" for key in step.return_spec.shape.keys()}
        return StepExecutionResult(status="ok", data=payload)


class InvalidContractExecutor(ExecutorAgent):
    async def execute_step(
        self,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
    ) -> StepExecutionResult:
        return StepExecutionResult(status="ok", data={"unexpected": "value"})


class StepFailedThenSuccessExecutor(ExecutorAgent):
    def __init__(self) -> None:
        self._seen_steps: set[int] = set()

    async def execute_step(
        self,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
    ) -> StepExecutionResult:
        if step.step_index not in self._seen_steps:
            self._seen_steps.add(step.step_index)
            return StepExecutionResult(status="failed", data=None, reason="simulated_failure")

        payload = {key: f"mock_{step.step_index}" for key in step.return_spec.shape.keys()}
        return StepExecutionResult(status="ok", data=payload)


class InvalidContractThenSuccessExecutor(ExecutorAgent):
    def __init__(self) -> None:
        self._seen_steps: set[int] = set()

    async def execute_step(
        self,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
    ) -> StepExecutionResult:
        if step.step_index not in self._seen_steps:
            self._seen_steps.add(step.step_index)
            return StepExecutionResult(status="ok", data={"unexpected": "value"})

        payload = {key: f"mock_{step.step_index}" for key in step.return_spec.shape.keys()}
        return StepExecutionResult(status="ok", data=payload)


class FailSecondStepOnceExecutor(ExecutorAgent):
    def __init__(self) -> None:
        self._failed_once = False

    async def execute_step(
        self,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
    ) -> StepExecutionResult:
        if step.step_index == 2 and not self._failed_once:
            self._failed_once = True
            return StepExecutionResult(status="failed", data=None, reason="step_2_failure")

        payload = {key: f"mock_{step.step_index}" for key in step.return_spec.shape.keys()}
        return StepExecutionResult(status="ok", data=payload)


class RecordingPlanner(MockPlannerAgent):
    def __init__(self) -> None:
        super().__init__()
        self.replan_calls: list[dict[str, Any]] = []

    async def replan(
        self,
        request: AgentRunRequest,
        completed_steps: list[PlanStep],
        failed_step: PlanStep,
        reason: str,
        max_steps: int = 10,
    ) -> PlannerOutput:
        self.replan_calls.append(
            {
                "completed_step_indexes": [step.step_index for step in completed_steps],
                "failed_step_index": failed_step.step_index,
                "failed_step_reason": failed_step.failure_reason,
                "reason": reason,
            }
        )
        return await super().replan(
            request=request,
            completed_steps=completed_steps,
            failed_step=failed_step,
            reason=reason,
            max_steps=max_steps,
        )


class ThreeStepRecordingPlanner(RecordingPlanner):
    async def create_plan(self, request: AgentRunRequest, max_steps: int = 10) -> PlannerOutput:
        steps = [
            PlanStep(
                step_index=1,
                task="step-1",
                skills=["skill_one"],
                return_spec=ReturnSpec(shape={"value_1": "string"}, reason="seed"),
            ),
            PlanStep(
                step_index=2,
                task="step-2",
                skills=["skill_two"],
                return_spec=ReturnSpec(shape={"value_2": "string"}, reason="process"),
            ),
            PlanStep(
                step_index=3,
                task="step-3",
                skills=["skill_three"],
                return_spec=ReturnSpec(shape={"response_text": "string"}, reason="final"),
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
        self.replan_calls.append(
            {
                "completed_step_indexes": [step.step_index for step in completed_steps],
                "failed_step_index": failed_step.step_index,
                "failed_step_reason": failed_step.failure_reason,
                "reason": reason,
            }
        )
        revised = [
            PlanStep(
                step_index=failed_step.step_index,
                task=f"revised-{failed_step.task}",
                skills=failed_step.skills,
                return_spec=failed_step.return_spec,
                input_from_step=failed_step.input_from_step,
            )
        ]
        return PlannerOutput(steps=revised[:max_steps])


class FailStepTwoOnceTrackingExecutor(ExecutorAgent):
    def __init__(self) -> None:
        self.execution_counts: dict[int, int] = {}

    async def execute_step(
        self,
        request: AgentRunRequest,
        plan: Plan,
        step: PlanStep,
    ) -> StepExecutionResult:
        self.execution_counts[step.step_index] = self.execution_counts.get(step.step_index, 0) + 1

        if step.step_index == 2 and self.execution_counts[step.step_index] == 1:
            return StepExecutionResult(status="failed", data=None, reason="step_2_retry_needed")

        payload = {key: f"value_for_{step.step_index}" for key in step.return_spec.shape.keys()}
        if "response_text" in payload:
            payload["response_text"] = "replanned flow complete"
        return StepExecutionResult(status="ok", data=payload)


class CountingMemoryRepository(InMemoryMemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        self.write_count = 0

    async def write(
        self,
        tenant_id: str,
        session_id: str,
        task_id: str,
        key: str,
        value: dict,
        return_spec_shape: dict,
    ) -> str:
        self.write_count += 1
        return await super().write(
            tenant_id=tenant_id,
            session_id=session_id,
            task_id=task_id,
            key=key,
            value=value,
            return_spec_shape=return_spec_shape,
        )


class ReadTrackingMemoryRepository(CountingMemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        self.read_count = 0

    async def read(self, namespaced_key: str) -> dict | None:
        self.read_count += 1
        return await super().read(namespaced_key)


class CollectingMessageBus:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        self.messages.append({"topic": topic, "payload": payload})


@pytest.mark.asyncio
async def test_orchestrator_success_flow() -> None:
    service = _orchestrator()
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="normal request",
    )

    response = await service.run(request)

    assert response.status == "complete"
    assert response.plan_id.startswith("plan_")
    assert response.response == "Mock execution successful"


@pytest.mark.asyncio
async def test_orchestrator_executes_steps_in_sequence_with_transitions() -> None:
    plan_repo = InMemoryPlanRepository()
    event_repo = InMemoryEventRepository()
    service = AgentOrchestrator(
        planner=MockPlannerAgent(),
        executor=MockExecutorAgent(),
        plan_repo=plan_repo,
        memory_repo=InMemoryMemoryRepository(),
        event_repo=event_repo,
        max_steps=10,
        max_replans=1,
    )
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="normal request",
    )

    response = await service.run(request)
    persisted_plan = await plan_repo.get(response.plan_id)
    events = await event_repo.list_by_plan(response.plan_id)

    assert persisted_plan is not None
    assert persisted_plan.completed_at is not None
    assert [step.status.value for step in persisted_plan.steps] == ["complete", "complete"]
    assert [step.step_index for step in persisted_plan.steps] == [1, 2]

    for step in persisted_plan.steps:
        assert step.started_at is not None
        assert step.finished_at is not None
        assert step.started_at <= step.finished_at

    started_events = [
        event
        for event in events
        if event.event_type == "step.started" and "step_index" in event.payload
    ]
    complete_events = [
        event
        for event in events
        if event.event_type == "step.complete" and "step_index" in event.payload
    ]

    assert [event.payload["step_index"] for event in started_events] == [1, 2]
    assert [event.payload["step_index"] for event in complete_events] == [1, 2]

    event_pairs = [(event.event_type, event.payload.get("step_index")) for event in events]
    assert event_pairs.index(("step.started", 1)) < event_pairs.index(("step.complete", 1))
    assert event_pairs.index(("step.complete", 1)) < event_pairs.index(("step.started", 2))
    assert event_pairs.index(("step.started", 2)) < event_pairs.index(("step.complete", 2))


@pytest.mark.asyncio
async def test_orchestrator_persists_valid_outputs_via_write_memory_only() -> None:
    plan_repo = InMemoryPlanRepository()
    memory_repo = CountingMemoryRepository()
    service = AgentOrchestrator(
        planner=MockPlannerAgent(),
        executor=MockExecutorAgent(),
        plan_repo=plan_repo,
        memory_repo=memory_repo,
        event_repo=InMemoryEventRepository(),
        max_steps=10,
        max_replans=1,
    )
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="normal request",
    )

    response = await service.run(request)
    persisted_plan = await plan_repo.get(response.plan_id)

    assert persisted_plan is not None
    completed_steps = [step for step in persisted_plan.steps if step.status.value == "complete"]
    assert len(completed_steps) == 2
    assert memory_repo.write_count == len(completed_steps)
    assert all(step.memory_key is not None for step in completed_steps)


@pytest.mark.asyncio
async def test_orchestrator_publishes_step_complete_to_message_bus() -> None:
    message_bus = CollectingMessageBus()
    service = AgentOrchestrator(
        planner=MockPlannerAgent(),
        executor=MockExecutorAgent(),
        plan_repo=InMemoryPlanRepository(),
        memory_repo=InMemoryMemoryRepository(),
        event_repo=InMemoryEventRepository(),
        message_bus=message_bus,
        max_steps=10,
        max_replans=1,
    )
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="normal request",
    )

    await service.run(request)

    complete_messages = [msg for msg in message_bus.messages if msg["topic"] == "step.complete"]
    assert len(complete_messages) == 2
    assert [msg["payload"]["step_index"] for msg in complete_messages] == [1, 2]


@pytest.mark.asyncio
async def test_orchestrator_publishes_step_failed_to_message_bus() -> None:
    message_bus = CollectingMessageBus()
    service = AgentOrchestrator(
        planner=MockPlannerAgent(),
        executor=MockExecutorAgent(),
        plan_repo=InMemoryPlanRepository(),
        memory_repo=InMemoryMemoryRepository(),
        event_repo=InMemoryEventRepository(),
        message_bus=message_bus,
        max_steps=10,
        max_replans=0,
    )
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="fail this task",
    )

    with pytest.raises(ReplanLimitReachedError):
        await service.run(request)

    failed_messages = [msg for msg in message_bus.messages if msg["topic"] == "step.failed"]
    assert len(failed_messages) >= 1
    assert failed_messages[0]["payload"]["step_index"] == 1


@pytest.mark.asyncio
async def test_orchestrator_stops_on_replan_limit() -> None:
    service = _orchestrator(max_replans=0)
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="fail this task",
    )

    with pytest.raises(ReplanLimitReachedError):
        await service.run(request)


@pytest.mark.asyncio
async def test_orchestrator_handles_insufficient_status_with_replan() -> None:
    plan_repo = InMemoryPlanRepository()
    memory_repo = InMemoryMemoryRepository()
    event_repo = InMemoryEventRepository()
    service = AgentOrchestrator(
        planner=MockPlannerAgent(),
        executor=InsufficientThenSuccessExecutor(),
        plan_repo=plan_repo,
        memory_repo=memory_repo,
        event_repo=event_repo,
        max_steps=10,
        max_replans=2,
    )
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="normal request",
    )

    response = await service.run(request)
    events = await event_repo.list_by_plan(response.plan_id)
    persisted_plan = await plan_repo.get(response.plan_id)

    assert response.status == "complete"
    assert any(event.event_type == "step.insufficient" for event in events)
    assert persisted_plan is not None
    assert persisted_plan.replan_count == 2
    assert all(replan.trigger == "insufficient" for replan in persisted_plan.replan_history)


@pytest.mark.asyncio
async def test_orchestrator_handles_step_failed_trigger_with_replan() -> None:
    plan_repo = InMemoryPlanRepository()
    service = AgentOrchestrator(
        planner=MockPlannerAgent(),
        executor=StepFailedThenSuccessExecutor(),
        plan_repo=plan_repo,
        memory_repo=InMemoryMemoryRepository(),
        event_repo=InMemoryEventRepository(),
        max_steps=10,
        max_replans=2,
    )
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="normal request",
    )

    response = await service.run(request)
    persisted_plan = await plan_repo.get(response.plan_id)

    assert response.status == "complete"
    assert persisted_plan is not None
    assert persisted_plan.replan_count == 2
    assert all(replan.trigger == "step_failed" for replan in persisted_plan.replan_history)


@pytest.mark.asyncio
async def test_orchestrator_validates_step_output_against_return_spec() -> None:
    plan_repo = InMemoryPlanRepository()
    event_repo = InMemoryEventRepository()
    memory_repo = CountingMemoryRepository()
    service = AgentOrchestrator(
        planner=MockPlannerAgent(),
        executor=InvalidContractExecutor(),
        plan_repo=plan_repo,
        memory_repo=memory_repo,
        event_repo=event_repo,
        max_steps=10,
        max_replans=0,
    )
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="normal request",
    )

    with pytest.raises(ReplanLimitReachedError):
        await service.run(request)

    plans = list(plan_repo._plans.values())
    assert len(plans) == 1
    events = await event_repo.list_by_plan(plans[0].plan_id)

    assert memory_repo.write_count == 0
    assert any(event.event_type == "step.contract_violation" for event in events)


@pytest.mark.asyncio
async def test_orchestrator_handles_contract_violation_trigger_with_replan() -> None:
    plan_repo = InMemoryPlanRepository()
    service = AgentOrchestrator(
        planner=MockPlannerAgent(),
        executor=InvalidContractThenSuccessExecutor(),
        plan_repo=plan_repo,
        memory_repo=InMemoryMemoryRepository(),
        event_repo=InMemoryEventRepository(),
        max_steps=10,
        max_replans=2,
    )
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="normal request",
    )

    response = await service.run(request)
    persisted_plan = await plan_repo.get(response.plan_id)

    assert response.status == "complete"
    assert persisted_plan is not None
    assert persisted_plan.replan_count == 2
    assert all(replan.trigger == "contract_violation" for replan in persisted_plan.replan_history)


@pytest.mark.asyncio
async def test_orchestrator_passes_completed_and_failed_context_to_replan() -> None:
    planner = RecordingPlanner()
    service = AgentOrchestrator(
        planner=planner,
        executor=FailSecondStepOnceExecutor(),
        plan_repo=InMemoryPlanRepository(),
        memory_repo=InMemoryMemoryRepository(),
        event_repo=InMemoryEventRepository(),
        max_steps=10,
        max_replans=2,
    )
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="normal request",
    )

    response = await service.run(request)

    assert response.status == "complete"
    assert len(planner.replan_calls) == 1
    replan_call = planner.replan_calls[0]
    assert replan_call["completed_step_indexes"] == [1]
    assert replan_call["failed_step_index"] == 2
    assert replan_call["failed_step_reason"] == "step_2_failure"
    assert replan_call["reason"] == "step_2_failure"


@pytest.mark.asyncio
async def test_orchestrator_synthesizes_response_from_memory_outputs() -> None:
    memory_repo = ReadTrackingMemoryRepository()
    service = AgentOrchestrator(
        planner=MockPlannerAgent(),
        executor=MockExecutorAgent(),
        plan_repo=InMemoryPlanRepository(),
        memory_repo=memory_repo,
        event_repo=InMemoryEventRepository(),
        max_steps=10,
        max_replans=1,
    )
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="normal request",
    )

    response = await service.run(request)

    assert response.response == "Mock execution successful"
    assert memory_repo.write_count == 2
    assert memory_repo.read_count == 2


@pytest.mark.asyncio
async def test_orchestrator_replan_revises_only_remaining_and_preserves_completed() -> None:
    planner = ThreeStepRecordingPlanner()
    executor = FailStepTwoOnceTrackingExecutor()
    memory_repo = CountingMemoryRepository()
    plan_repo = InMemoryPlanRepository()
    service = AgentOrchestrator(
        planner=planner,
        executor=executor,
        plan_repo=plan_repo,
        memory_repo=memory_repo,
        event_repo=InMemoryEventRepository(),
        max_steps=10,
        max_replans=2,
    )
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="normal request",
    )

    response = await service.run(request)
    persisted_plan = await plan_repo.get(response.plan_id)

    assert response.status == "complete"
    assert response.response == "replanned flow complete"
    assert persisted_plan is not None
    assert planner.replan_calls[0]["completed_step_indexes"] == [1]
    assert planner.replan_calls[0]["failed_step_index"] == 2
    assert persisted_plan.steps[0].step_index == 1
    assert persisted_plan.steps[0].task == "step-1"
    assert persisted_plan.steps[1].step_index == 2
    assert persisted_plan.steps[1].task == "revised-step-2"
    assert persisted_plan.steps[2].step_index == 3
    assert persisted_plan.steps[2].task == "step-3"
    assert executor.execution_counts == {1: 1, 2: 2, 3: 1}
    assert memory_repo.write_count == 3


@pytest.mark.asyncio
async def test_orchestrator_caps_replan_attempts_at_three() -> None:
    plan_repo = InMemoryPlanRepository()
    service = AgentOrchestrator(
        planner=MockPlannerAgent(),
        executor=MockExecutorAgent(),
        plan_repo=plan_repo,
        memory_repo=InMemoryMemoryRepository(),
        event_repo=InMemoryEventRepository(),
        max_steps=10,
        max_replans=3,
    )
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="fail this task",
    )

    with pytest.raises(ReplanLimitReachedError):
        await service.run(request)

    plans = list(plan_repo._plans.values())
    assert len(plans) == 1
    assert plans[0].status.value == "failed"
    assert plans[0].replan_count == 3
    assert len(plans[0].replan_history) == 3


@pytest.mark.asyncio
async def test_orchestrator_returns_structured_failure_when_replans_exhausted() -> None:
    service = AgentOrchestrator(
        planner=MockPlannerAgent(),
        executor=MockExecutorAgent(),
        plan_repo=InMemoryPlanRepository(),
        memory_repo=InMemoryMemoryRepository(),
        event_repo=InMemoryEventRepository(),
        max_steps=10,
        max_replans=0,
    )
    request = AgentRunRequest(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="session_1",
        message="fail this task",
    )

    with pytest.raises(ReplanLimitReachedError) as exc_info:
        await service.run(request)

    failure_response = exc_info.value.failure_response
    assert failure_response is not None
    assert failure_response["status"] == "failed"
    assert failure_response["reason"] == "max replan attempts reached"
    assert failure_response["completed_steps"] == []
    assert failure_response["last_failure"] == {"step": 1, "reason": "simulated_failure"}
