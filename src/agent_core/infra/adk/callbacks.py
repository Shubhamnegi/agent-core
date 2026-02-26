from __future__ import annotations

from typing import Any


async def before_tool_callback(
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
) -> dict[str, Any] | None:
    if tool.name == "write_memory" and "return_spec" not in args:
        return {"status": "contract_violation", "reason": "missing return_spec"}
    return None


async def after_tool_callback(
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    if isinstance(result, dict):
        enriched = dict(result)
        enriched["tool_name"] = tool.name
        return enriched
    return None


async def on_tool_error_callback(
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "tool_name": tool.name,
        "reason": str(exc),
    }
