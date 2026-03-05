from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from opensearchpy import OpenSearch
from redis.asyncio import Redis

from agent_core.domain.models import EventRecord
from agent_core.infra.adapters.opensearch import OpenSearchEventRepository, OpenSearchIndexManager
from agent_core.infra.adapters.redis_events import RedisStreamEventRepository
from agent_core.infra.events.consumer import RedisToOpenSearchEventConsumer


async def main() -> None:
    client = OpenSearch(hosts=["http://localhost:9200"], verify_certs=False, ssl_show_warn=False)
    OpenSearchIndexManager(client=client).ensure_indices_and_policies()

    redis = Redis.from_url("redis://localhost:6379/0")
    sink = OpenSearchEventRepository(client=client)
    producer = RedisStreamEventRepository(
        redis_client=redis,
        stream_name="agent.events",
        read_repo=sink,
        maxlen=10000,
    )

    consumer = RedisToOpenSearchEventConsumer(
        redis_client=redis,
        sink_repo=sink,
        stream_name="agent.events",
        group_name="agent-events-consumers-smoke",
        consumer_name="smoke-worker",
        dlq_stream_name="agent.events.dlq",
        batch_size=10,
        block_ms=200,
        backoff_seconds=0.0,
    )

    await consumer.start()
    try:
        event = EventRecord(
            event_id="evt_smoke_001",
            event_type="smoke.event",
            tenant_id="tenant-smoke",
            session_id="session-smoke",
            plan_id="plan-smoke",
            task_id="task-smoke",
            payload={"source": "smoke-test"},
            ts=datetime.now(UTC),
        )
        await producer.append(event)

        for _ in range(30):
            events = await sink.list_by_plan("plan-smoke")
            if any(item.event_id == "evt_smoke_001" for item in events):
                print(f"SMOKE_OK events_found={len(events)}")
                return
            await asyncio.sleep(0.2)

        raise RuntimeError("smoke_event_not_persisted_in_time")
    finally:
        await consumer.stop()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
