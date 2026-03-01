from __future__ import annotations
"""ADK event-to-dict extraction helpers.

Why this module exists: ADK event objects are loosely shaped; centralizing extraction
avoids repeated defensive access patterns across runtime and logging code.
"""

from typing import Any


def _extract_event_text(event: Any) -> str:
    """Why: runtime response assembly relies on a stable text extraction contract."""
    content = getattr(event, "content", None)
    if content is None:
        return ""
    parts = getattr(content, "parts", None)
    if not parts:
        return ""
    texts: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            texts.append(text)
    return "\n".join(texts)


def _extract_event_function_calls(event: Any) -> list[dict[str, Any]]:
    """Extract function_call parts from an ADK event."""
    content = getattr(event, "content", None)
    if content is None:
        return []
    parts = getattr(content, "parts", None)
    if not parts:
        return []
    calls: list[dict[str, Any]] = []
    for part in parts:
        fc = getattr(part, "function_call", None)
        if fc is not None:
            calls.append({
                "name": getattr(fc, "name", None),
                "args": dict(getattr(fc, "args", {}) or {}),
            })
    return calls


def _extract_event_function_responses(event: Any) -> list[dict[str, Any]]:
    """Extract function_response parts from an ADK event."""
    content = getattr(event, "content", None)
    if content is None:
        return []
    parts = getattr(content, "parts", None)
    if not parts:
        return []
    responses: list[dict[str, Any]] = []
    for part in parts:
        fr = getattr(part, "function_response", None)
        if fr is not None:
            responses.append({
                "name": getattr(fr, "name", None),
                "response": dict(getattr(fr, "response", {}) or {}),
            })
    return responses


def _to_optional_str(value: Any) -> str | None:
    """Why: normalize unknown ADK values before persistence/logging payloads."""
    return value if isinstance(value, str) else None
