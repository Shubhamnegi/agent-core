from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from agent_core.domain.models import EventRecord
from agent_core.infra.adapters.redis_events import (
    RedisStreamEventRepository,
    deserialize_event_record,
    serialize_event_record,
)
from agent_core.infra.events.consumer import RedisToOpenSearchEventConsumer


class _FakeReadRepo:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_by_plan(self, plan_id: str) -> list[EventRecord]:
        self.calls.append(plan_id)
        return []


class _FakeSinkRepo:
    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.appended: list[EventRecord] = []

    async def append(self, event: EventRecord) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("sink_unavailable")
        self.appended.append(event)


class _FakeRedis:
    def __init__(self) -> None:
        self.xadd_calls: list[dict[str, Any]] = []
        self.xack_calls: list[tuple[str, str, str]] = []
        self.group_create_calls: list[dict[str, Any]] = []

    async def xadd(
        self,
        stream_name: str,
        fields: dict[str, Any],
        maxlen: int | None = None,
        approximate: bool | None = None,
    ) -> str:
        self.xadd_calls.append(
            {
                "stream_name": stream_name,
                "fields": fields,
                "maxlen": maxlen,
                "approximate": approximate,
            }
        )
        return "1-0"

    async def xack(self, stream_name: str, group_name: str, message_id: str) -> int:
        self.xack_calls.append((stream_name, group_name, message_id))
        return 1

    async def xgroup_create(self, **kwargs: Any) -> str:
        self.group_create_calls.append(kwargs)
        return "OK"

    async def xautoclaim(self, **kwargs: Any) -> tuple[str, list[Any], list[Any]]:
        _ = kwargs
        return ("0-0", [], [])

    async def xreadgroup(self, **kwargs: Any) -> list[Any]:
        _ = kwargs
        return []


@pytest.mark.asyncio
async def test_redis_stream_event_repository_publishes_and_delegates_reads() -> None:
    redis = _FakeRedis()
    read_repo = _FakeReadRepo()
    repo = RedisStreamEventRepository(
        redis_client=redis,
        stream_name="agent.events",
        read_repo=read_repo,  # type: ignore[arg-type]
        maxlen=500,
    )
    event = EventRecord(
        event_id="evt_1",
        event_type="adk.event",
        tenant_id="tenant",
        session_id="session",
        plan_id="plan",
        task_id="task",
        payload={"k": "v"},
        ts=datetime.now(UTC),
    )

    await repo.append(event)
    await repo.list_by_plan("plan")

    assert len(redis.xadd_calls) == 1
    assert redis.xadd_calls[0]["stream_name"] == "agent.events"
    assert redis.xadd_calls[0]["maxlen"] == 500
    assert read_repo.calls == ["plan"]


@pytest.mark.asyncio
async def test_consumer_persists_and_acks_on_success() -> None:
    redis = _FakeRedis()
    sink = _FakeSinkRepo()
    consumer = RedisToOpenSearchEventConsumer(
        redis_client=redis,
        sink_repo=sink,  # type: ignore[arg-type]
        stream_name="agent.events",
        group_name="agent-events-consumers",
        consumer_name="worker-1",
        dlq_stream_name="agent.events.dlq",
    )

    event = EventRecord(
        event_id="evt_ok",
        event_type="adk.event",
        tenant_id="tenant",
        session_id="session",
        plan_id="plan",
        task_id="task",
        payload={"k": "v"},
        ts=datetime.now(UTC),
    )

    await consumer._process_entry(
        "1-0",
        {"event_json": serialize_event_record(event), "attempt": "0"},
    )

    assert [item.event_id for item in sink.appended] == ["evt_ok"]
    assert redis.xack_calls == [("agent.events", "agent-events-consumers", "1-0")]


@pytest.mark.asyncio
async def test_consumer_sends_to_dlq_after_retry_limit() -> None:
    redis = _FakeRedis()
    sink = _FakeSinkRepo(fail_times=10)
    consumer = RedisToOpenSearchEventConsumer(
        redis_client=redis,
        sink_repo=sink,  # type: ignore[arg-type]
        stream_name="agent.events",
        group_name="agent-events-consumers",
        consumer_name="worker-1",
        dlq_stream_name="agent.events.dlq",
        max_retries=1,
        backoff_seconds=0.0,
    )

    event = EventRecord(
        event_id="evt_fail",
        event_type="adk.event",
        tenant_id="tenant",
        session_id="session",
        plan_id="plan",
        task_id="task",
        payload={"k": "v"},
        ts=datetime.now(UTC),
    )

    await consumer._process_entry(
        "1-0",
        {"event_json": serialize_event_record(event), "attempt": "1"},
    )

    assert len(redis.xadd_calls) == 1
    assert redis.xadd_calls[0]["stream_name"] == "agent.events.dlq"
    assert redis.xack_calls == [("agent.events", "agent-events-consumers", "1-0")]


def test_event_serialization_roundtrip_preserves_identity() -> None:
    event = EventRecord(
        event_id="evt_roundtrip",
        event_type="adk.event",
        tenant_id="tenant",
        session_id="session",
        plan_id="plan",
        task_id="task",
        payload={"hello": "world"},
        ts=datetime.now(UTC),
    )

    encoded = serialize_event_record(event)
    decoded = deserialize_event_record(encoded)

    assert decoded.event_id == "evt_roundtrip"
    assert decoded.event_type == "adk.event"
    assert decoded.payload == {"hello": "world"}
