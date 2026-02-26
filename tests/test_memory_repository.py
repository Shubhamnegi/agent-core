from time import monotonic

import pytest

from agent_core.domain.exceptions import ContractViolationError, MemoryLockError
from agent_core.infra.adapters.in_memory import InMemoryMemoryRepository


@pytest.mark.asyncio
async def test_memory_write_auto_namespaces_key() -> None:
    repo = InMemoryMemoryRepository()

    namespaced = await repo.write(
        tenant_id="tenant_1",
        session_id="session_1",
        task_id="task_1",
        key="result",
        value={"intent": "mock"},
        return_spec_shape={"intent": "string"},
    )

    assert namespaced == "tenant_1:session_1:task_1:result"


@pytest.mark.asyncio
async def test_memory_rejects_pre_namespaced_user_key() -> None:
    repo = InMemoryMemoryRepository()

    with pytest.raises(ValueError, match="short key labels"):
        await repo.write(
            tenant_id="tenant_1",
            session_id="session_1",
            task_id="task_1",
            key="tenant_1:session_1:task_1:result",
            value={"intent": "mock"},
            return_spec_shape={"intent": "string"},
        )


@pytest.mark.asyncio
async def test_memory_contract_violation_does_not_write() -> None:
    repo = InMemoryMemoryRepository()

    with pytest.raises(ContractViolationError, match="contract_violation"):
        await repo.write(
            tenant_id="tenant_1",
            session_id="session_1",
            task_id="task_1",
            key="result",
            value={"intent": 123},
            return_spec_shape={"intent": "string"},
        )

    assert repo._data == {}


@pytest.mark.asyncio
async def test_memory_write_lock_times_out_for_conflicting_writer() -> None:
    repo = InMemoryMemoryRepository(lock_wait_timeout_seconds=0.02)

    namespaced = await repo.write(
        tenant_id="tenant_1",
        session_id="session_1",
        task_id="task_1",
        key="result",
        value={"intent": "first"},
        return_spec_shape={"intent": "string"},
    )

    # Why private lock injection: task-scoped namespacing avoids natural key conflicts,
    # so we simulate a stale/foreign lock on the exact key to validate timeout behavior.
    held_lock = repo._locks[namespaced]
    held_lock.owner_task_id = "other_task"
    held_lock.expires_at = monotonic() + 10

    with pytest.raises(MemoryLockError, match="memory_lock_timeout"):
        await repo.write(
            tenant_id="tenant_1",
            session_id="session_1",
            task_id="task_1",
            key="result",
            value={"intent": "second"},
            return_spec_shape={"intent": "string"},
        )


@pytest.mark.asyncio
async def test_memory_lock_releases_after_orchestrator_read_confirmation() -> None:
    repo = InMemoryMemoryRepository(lock_wait_timeout_seconds=0.02)

    namespaced = await repo.write(
        tenant_id="tenant_1",
        session_id="session_1",
        task_id="task_1",
        key="result",
        value={"intent": "first"},
        return_spec_shape={"intent": "string"},
    )
    assert namespaced in repo._locks

    value = await repo.read(namespaced, release_lock=True)
    assert value == {"intent": "first"}
    assert namespaced not in repo._locks

    second_write = await repo.write(
        tenant_id="tenant_1",
        session_id="session_1",
        task_id="task_1",
        key="result",
        value={"intent": "second"},
        return_spec_shape={"intent": "string"},
    )

    assert second_write == "tenant_1:session_1:task_1:result"
