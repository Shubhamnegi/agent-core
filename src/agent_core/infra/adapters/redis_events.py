from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from agent_core.application.ports import EventRepository
from agent_core.domain.models import EventRecord


class RedisStreamEventRepository(EventRepository):
    """Publish events to Redis Streams and delegate reads to an indexed repository."""

    def __init__(
        self,
        redis_client: Any,
        stream_name: str,
        read_repo: EventRepository,
        maxlen: int = 100000,
    ) -> None:
        self.redis_client = redis_client
        self.stream_name = stream_name
        self.read_repo = read_repo
        self.maxlen = maxlen

    async def append(self, event: EventRecord) -> None:
        await self.redis_client.xadd(
            self.stream_name,
            {
                "event_json": serialize_event_record(event),
                "attempt": "0",
            },
            maxlen=self.maxlen,
            approximate=True,
        )

    async def list_by_plan(self, plan_id: str) -> list[EventRecord]:
        return await self.read_repo.list_by_plan(plan_id)


def serialize_event_record(event: EventRecord) -> str:
    document = {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "tenant_id": event.tenant_id,
        "session_id": event.session_id,
        "plan_id": event.plan_id,
        "task_id": event.task_id,
        "payload": event.payload,
        "ts": event.ts.isoformat(),
    }
    return json.dumps(document, separators=(",", ":"), sort_keys=True)


def deserialize_event_record(event_json: str) -> EventRecord:
    data = json.loads(event_json)
    ts_raw = data.get("ts")
    parsed_ts = datetime.fromisoformat(ts_raw) if isinstance(ts_raw, str) else datetime.now()
    return EventRecord(
        event_id=str(data.get("event_id", "")),
        event_type=str(data.get("event_type", "unknown")),
        tenant_id=str(data.get("tenant_id", "")),
        session_id=str(data.get("session_id", "")),
        plan_id=data.get("plan_id"),
        task_id=data.get("task_id"),
        payload=data.get("payload", {}) if isinstance(data.get("payload"), dict) else {},
        ts=parsed_ts,
    )
