from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from redis.exceptions import ResponseError

from agent_core.application.ports import EventRepository
from agent_core.infra.adapters.redis_events import deserialize_event_record

logger = logging.getLogger(__name__)


class RedisToOpenSearchEventConsumer:
    """Drain events from Redis Streams and persist into OpenSearch-backed repository."""

    def __init__(
        self,
        redis_client: Any,
        sink_repo: EventRepository,
        stream_name: str,
        group_name: str,
        consumer_name: str,
        dlq_stream_name: str,
        batch_size: int = 50,
        block_ms: int = 1000,
        reclaim_idle_ms: int = 60000,
        reclaim_count: int = 50,
        max_retries: int = 5,
        backoff_seconds: float = 0.2,
    ) -> None:
        self.redis_client = redis_client
        self.sink_repo = sink_repo
        self.stream_name = stream_name
        self.group_name = group_name
        self.consumer_name = consumer_name
        self.dlq_stream_name = dlq_stream_name
        self.batch_size = batch_size
        self.block_ms = block_ms
        self.reclaim_idle_ms = reclaim_idle_ms
        self.reclaim_count = reclaim_count
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        await self._ensure_group()
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="redis-events-consumer")

    async def stop(self) -> None:
        self._running = False
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task

    async def _ensure_group(self) -> None:
        try:
            await self.redis_client.xgroup_create(
                name=self.stream_name,
                groupname=self.group_name,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def _run_loop(self) -> None:
        while self._running:
            await self._drain_reclaimed_pending()
            await self._consume_new_entries()

    async def _drain_reclaimed_pending(self) -> None:
        claimed = await self.redis_client.xautoclaim(
            name=self.stream_name,
            groupname=self.group_name,
            consumername=self.consumer_name,
            min_idle_time=self.reclaim_idle_ms,
            start_id="0-0",
            count=self.reclaim_count,
        )
        entries = _extract_xautoclaim_entries(claimed)
        for message_id, fields in entries:
            await self._process_entry(message_id, fields)

    async def _consume_new_entries(self) -> None:
        payload = await self.redis_client.xreadgroup(
            groupname=self.group_name,
            consumername=self.consumer_name,
            streams={self.stream_name: ">"},
            count=self.batch_size,
            block=self.block_ms,
        )
        entries = _extract_xreadgroup_entries(payload)
        for message_id, fields in entries:
            await self._process_entry(message_id, fields)

    async def _process_entry(self, message_id: str, fields: dict[Any, Any]) -> None:
        event_json = _get_stream_field(fields, "event_json")
        if not event_json:
            await self.redis_client.xack(self.stream_name, self.group_name, message_id)
            return

        attempt_raw = _get_stream_field(fields, "attempt") or "0"
        attempt = _safe_int(attempt_raw)

        try:
            event = deserialize_event_record(event_json)
            await self.sink_repo.append(event)
            await self.redis_client.xack(self.stream_name, self.group_name, message_id)
        except Exception as exc:
            next_attempt = attempt + 1
            if next_attempt > self.max_retries:
                await self.redis_client.xadd(
                    self.dlq_stream_name,
                    {
                        "event_json": event_json,
                        "attempt": str(next_attempt),
                        "error": str(exc),
                    },
                )
                await self.redis_client.xack(self.stream_name, self.group_name, message_id)
                logger.exception(
                    "event_consumer_sent_to_dlq",
                    extra={"message_id": message_id, "attempt": next_attempt},
                )
                return

            await asyncio.sleep(self.backoff_seconds * max(1, next_attempt))
            await self.redis_client.xadd(
                self.stream_name,
                {
                    "event_json": event_json,
                    "attempt": str(next_attempt),
                },
            )
            await self.redis_client.xack(self.stream_name, self.group_name, message_id)
            logger.exception(
                "event_consumer_retry_scheduled",
                extra={"message_id": message_id, "attempt": next_attempt},
            )


def _extract_xautoclaim_entries(payload: Any) -> list[tuple[str, dict[Any, Any]]]:
    if not isinstance(payload, (list, tuple)) or len(payload) < 2:
        return []
    entries = payload[1]
    return _normalize_entries(entries)


def _extract_xreadgroup_entries(payload: Any) -> list[tuple[str, dict[Any, Any]]]:
    if not payload:
        return []
    stream_entries = payload[0] if isinstance(payload, list) else None
    if not isinstance(stream_entries, (list, tuple)) or len(stream_entries) < 2:
        return []
    entries = stream_entries[1]
    return _normalize_entries(entries)


def _normalize_entries(entries: Any) -> list[tuple[str, dict[Any, Any]]]:
    output: list[tuple[str, dict[Any, Any]]] = []
    if not isinstance(entries, list):
        return output
    for item in entries:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        message_id_raw, fields = item
        message_id = _field_to_str(message_id_raw)
        if not message_id or not isinstance(fields, dict):
            continue
        output.append((message_id, fields))
    return output


def _field_to_str(value: Any) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    if value is None:
        return None
    return str(value)


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def _get_stream_field(fields: dict[Any, Any], name: str) -> str | None:
    direct = fields.get(name)
    if direct is not None:
        return _field_to_str(direct)
    encoded = fields.get(name.encode("utf-8"))
    if encoded is not None:
        return _field_to_str(encoded)
    return None
