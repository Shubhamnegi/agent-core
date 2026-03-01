from __future__ import annotations
"""Message-level heuristics and response cleanup.

Why this module exists: keeps prompt/response policy rules independent from runtime orchestration
so wording changes do not increase cognitive load in the main runtime class.
"""

import re


def _message_requests_memory_lookup(message: str) -> bool:
    """Why: explicit user intent should enable memory precheck even on non-first turns."""
    lowered = message.lower()
    memory_markers = (
        "check memory",
        "from memory",
        "search memory",
        "what do you remember",
        "based on my preference",
        "my preference",
        "remembered",
        "recall",
    )
    return any(marker in lowered for marker in memory_markers)


def _message_disables_memory_usage(message: str) -> bool:
    """Why: user opt-out must be detected early to enforce memory usage boundaries."""
    lowered = message.lower()
    disable_markers = (
        "don't use memory",
        "do not use memory",
        "dont use memory",
        "without memory",
        "ignore memory",
        "skip memory",
        "no memory",
    )
    return any(marker in lowered for marker in disable_markers)


def _sanitize_user_response(response: str) -> str:
    """Why: hide internal tool names/constraints from end-user prose."""
    sanitized = response
    sanitized = sanitized.replace(
        "The `get_cost_and_usage_comparisons` tool requires both the baseline and comparison periods to be exactly one month long and to start on the first day of the month.",
        "The requested period-over-period comparison is not available for this exact date range.",
    )
    sanitized = re.sub(
        r"`get_[a-zA-Z0-9_]+`",
        "the requested comparison",
        sanitized,
    )
    return sanitized
