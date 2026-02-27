from __future__ import annotations

import logging
from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

logger = logging.getLogger(__name__)

_PROMPT_TEXT_LIMIT = 2000
_RESPONSE_TEXT_LIMIT = 2000


def _extract_content_texts(contents: list[Any]) -> list[str]:
    """Pull plain-text parts out of a list of google.genai.types.Content."""
    texts: list[str] = []
    for content in contents:
        parts = getattr(content, "parts", None)
        if not parts:
            continue
        for part in parts:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text:
                texts.append(text)
    return texts


def _extract_function_calls(content: Any) -> list[dict[str, Any]]:
    """Pull function-call summaries from a response Content object."""
    calls: list[dict[str, Any]] = []
    parts = getattr(content, "parts", None) if content else None
    if not parts:
        return calls
    for part in parts:
        fc = getattr(part, "function_call", None)
        if fc is not None:
            calls.append(
                {
                    "name": getattr(fc, "name", None),
                    "args": dict(getattr(fc, "args", {}) or {}),
                }
            )
    return calls


async def before_model_callback(
    callback_context: Any,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    """Log the prompt sent to the LLM (system instruction + conversation)."""
    agent_name = getattr(callback_context, "agent_name", "unknown")
    prompt_texts = _extract_content_texts(llm_request.contents)
    system_instruction: str | None = None
    config = llm_request.config
    if config is not None:
        si = getattr(config, "system_instruction", None)
        if si is not None:
            si_texts = _extract_content_texts([si]) if hasattr(si, "parts") else []
            if si_texts:
                system_instruction = si_texts[0][:_PROMPT_TEXT_LIMIT]
    tool_names = sorted(llm_request.tools_dict.keys()) if llm_request.tools_dict else []
    logger.info(
        "llm_prompt",
        extra={
            "agent": agent_name,
            "model": llm_request.model,
            "system_instruction_preview": (system_instruction or "")[:_PROMPT_TEXT_LIMIT],
            "content_count": len(llm_request.contents),
            "last_content_preview": (prompt_texts[-1][:_PROMPT_TEXT_LIMIT] if prompt_texts else ""),
            "available_tools": tool_names,
        },
    )
    return None


async def after_model_callback(
    callback_context: Any,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """Log the LLM response — both textual parts and function calls."""
    agent_name = getattr(callback_context, "agent_name", "unknown")
    content = llm_response.content
    response_texts = _extract_content_texts([content]) if content else []
    function_calls = _extract_function_calls(content)
    logger.info(
        "llm_response",
        extra={
            "agent": agent_name,
            "model_version": llm_response.model_version,
            "text_preview": (response_texts[0][:_RESPONSE_TEXT_LIMIT] if response_texts else ""),
            "function_calls": function_calls,
            "finish_reason": (
                str(llm_response.finish_reason)
                if llm_response.finish_reason
                else None
            ),
            "error_code": llm_response.error_code,
            "error_message": llm_response.error_message,
        },
    )
    return None


async def before_tool_callback(
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
) -> dict[str, Any] | None:
    logger.info(
        "tool_call_start",
        extra={
            "tool_name": tool.name,
            "tool_args": args,
        },
    )
    if tool.name == "write_memory" and "return_spec" not in args:
        return {"status": "contract_violation", "reason": "missing return_spec"}
    return None


async def after_tool_callback(
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
    result: Any = None,
    tool_response: Any = None,
    **_: Any,
) -> dict[str, Any] | None:
    effective_result = tool_response if tool_response is not None else result
    result_preview = str(effective_result)[:1000] if effective_result else ""
    logger.info(
        "tool_call_end",
        extra={
            "tool_name": tool.name,
            "result_preview": result_preview,
        },
    )
    if isinstance(effective_result, dict):
        enriched = dict(effective_result)
        enriched["tool_name"] = tool.name
        return enriched
    return None


async def on_tool_error_callback(
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
    exc: Exception,
) -> dict[str, Any]:
    logger.error(
        "tool_call_error",
        extra={
            "tool_name": tool.name,
            "error_type": type(exc).__name__,
            "error": str(exc),
        },
    )
    return {
        "status": "failed",
        "tool_name": tool.name,
        "reason": str(exc),
    }
