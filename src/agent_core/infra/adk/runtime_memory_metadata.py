from __future__ import annotations
"""Memory usage metadata derivation and disclosure formatting.

Why this module exists: memory evidence extraction is dense and easy to misread;
isolating it keeps runtime flow linear and allows targeted tests for disclosure behavior.
"""

import json
from datetime import datetime
from typing import Any


class _MemoryUsageMetadata:
    """Why: single transport object for aggregating memory usage across streamed events."""

    def __init__(
        self,
        used: bool = False,
        latest_timestamp: str | None = None,
        summary: str | None = None,
    ) -> None:
        self.used = used
        self.latest_timestamp = latest_timestamp
        self.summary = summary


def _merge_memory_metadata(
    left: _MemoryUsageMetadata,
    right: _MemoryUsageMetadata,
) -> _MemoryUsageMetadata:
    """Why: streamed tool responses arrive incrementally and need monotonic aggregation."""
    used = left.used or right.used
    latest = _max_iso_timestamp(left.latest_timestamp, right.latest_timestamp)
    summary = left.summary or right.summary
    return _MemoryUsageMetadata(used=used, latest_timestamp=latest, summary=summary)


def _extract_memory_usage_metadata(
    function_responses: list[dict[str, Any]],
) -> _MemoryUsageMetadata:
    """Why: only memory-search tool responses should influence disclosure state."""
    output = _MemoryUsageMetadata()
    for item in function_responses:
        if item.get("name") != "search_relevant_memory":
            continue
        response_payload = item.get("response")
        if not isinstance(response_payload, dict):
            continue

        count = response_payload.get("count")
        if isinstance(count, int) and count > 0:
            output.used = True

        results = response_payload.get("results")
        if not isinstance(results, list):
            continue

        for result in results:
            if not isinstance(result, dict):
                continue
            created_at = result.get("created_at")
            if isinstance(created_at, str) and created_at:
                output.latest_timestamp = _max_iso_timestamp(output.latest_timestamp, created_at)

            summary = _extract_memory_summary(result)
            if summary and not output.summary:
                output.summary = summary
    return output


def _extract_memory_summary(result: dict[str, Any]) -> str | None:
    value = result.get("value")
    if not isinstance(value, dict):
        return None

    blob = value.get("blob_json")
    if isinstance(blob, str):
        try:
            parsed = json.loads(blob)
            if isinstance(parsed, dict):
                return _summarize_memory_value(parsed)
        except Exception:
            return None

    return _summarize_memory_value(value)


def _summarize_memory_value(value: dict[str, Any]) -> str | None:
    memory_text = value.get("memory_text")
    if isinstance(memory_text, str) and memory_text:
        return memory_text

    summary_fields: list[str] = []
    for field_name in ("domain", "intent"):
        field_value = value.get(field_name)
        if isinstance(field_value, str) and field_value:
            summary_fields.append(f"{field_name}: {field_value}")
    entities = value.get("entities")
    if isinstance(entities, list) and entities:
        summary_fields.append(f"entities: {', '.join(str(item) for item in entities[:5])}")

    if not summary_fields:
        return None
    return "; ".join(summary_fields)


def _max_iso_timestamp(left: str | None, right: str | None) -> str | None:
    if left is None:
        return right
    if right is None:
        return left
    return right if right > left else left


def _apply_memory_disclosure(
    response: str,
    memory_metadata: _MemoryUsageMetadata,
    memory_disabled_by_user: bool,
) -> str:
    """Why: add transparent disclosure so users know when memory shaped the answer."""
    if memory_disabled_by_user:
        prefix = "Note: I did not use memory for this response because you asked to skip memory."
        return f"{prefix}\n\n{response}"

    if not memory_metadata.used:
        return response

    timestamp = memory_metadata.latest_timestamp or "unknown time"
    summary = memory_metadata.summary or "a previously saved preference"
    stale_note = _memory_staleness_note(memory_metadata.latest_timestamp)
    prefix = (
        f"Note: I used saved memory from {timestamp} to tailor this response. "
        f"Applied memory: {summary}."
    )
    if stale_note:
        prefix = f"{prefix} {stale_note}"
    return f"{prefix}\n\n{response}"


def _memory_staleness_note(timestamp: str | None) -> str:
    if not timestamp:
        return ""
    try:
        created_at = datetime.fromisoformat(timestamp)
    except Exception:
        return ""
    age_days = (datetime.now(created_at.tzinfo) - created_at).days
    if age_days >= 30:
        return f"Memory may be stale (saved about {age_days} days ago)."
    return ""
