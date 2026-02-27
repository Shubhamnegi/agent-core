from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from agent_core.application.ports import EventRepository
from agent_core.domain.models import EventRecord

logger = logging.getLogger(__name__)

_PROMPT_TEXT_LIMIT = 2000
_RESPONSE_TEXT_LIMIT = 2000
_TRACE_TEXT_LIMIT = 12000


@dataclass(slots=True)
class _TraceContext:
    event_repo: EventRepository
    tenant_id: str
    session_id: str
    plan_id: str
    require_planner_first_transfer: bool
    planner_transfer_seen: bool = False
    planner_find_skill_called: bool = False
    planner_load_skill_called: bool = False
    planner_no_skill_found: bool = False
    planner_expected_tools: list[str] | None = None
    planner_available_tools: list[str] | None = None


_trace_context: ContextVar[_TraceContext | None] = ContextVar(
    "adk_trace_context",
    default=None,
)


def bind_trace_context(
    event_repo: EventRepository,
    tenant_id: str,
    session_id: str,
    plan_id: str,
    require_planner_first_transfer: bool = False,
    planner_expected_tools: list[str] | None = None,
) -> Token[_TraceContext | None]:
    return _trace_context.set(
        _TraceContext(
            event_repo=event_repo,
            tenant_id=tenant_id,
            session_id=session_id,
            plan_id=plan_id,
            require_planner_first_transfer=require_planner_first_transfer,
            planner_expected_tools=planner_expected_tools,
        )
    )


def reset_trace_context(token: Token[_TraceContext | None]) -> None:
    _trace_context.reset(token)


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


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit]


def _extract_callback_task_id(callback_context: Any) -> str | None:
    for field in ("invocation_id", "task_id", "run_id"):
        value = getattr(callback_context, field, None)
        if isinstance(value, str) and value:
            return value
    return None


async def _append_trace_event(
    event_type: str,
    callback_context: Any,
    payload: dict[str, Any],
) -> None:
    trace_context = _trace_context.get()
    if trace_context is None:
        return
    task_id = _extract_callback_task_id(callback_context)
    await trace_context.event_repo.append(
        EventRecord(
            event_type=event_type,
            tenant_id=trace_context.tenant_id,
            session_id=trace_context.session_id,
            plan_id=trace_context.plan_id,
            task_id=task_id,
            payload=payload,
            ts=datetime.now(UTC),
        )
    )


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

    trace_context = _trace_context.get()
    if agent_name == "planner_subagent_a" and trace_context is not None:
        trace_context.planner_available_tools = tool_names
        has_find = "find_relevant_skill" in tool_names
        has_load = ("load_instruction" in tool_names) or ("load_instructions" in tool_names)
        expected = trace_context.planner_expected_tools or []
        expected_find = "find_relevant_skill" in expected
        expected_load = ("load_instruction" in expected) or ("load_instructions" in expected)

        logger.info(
            "planner_tool_availability",
            extra={
                "agent": agent_name,
                "planner_expected_tools": expected,
                "planner_available_tools": tool_names,
                "has_find_relevant_skill": has_find,
                "has_load_instruction": has_load,
            },
        )

        if expected_load and not has_load:
            logger.warning(
                "planner_load_tool_missing",
                extra={
                    "agent": agent_name,
                    "planner_expected_tools": expected,
                    "planner_available_tools": tool_names,
                    "reason": "planner_tool_filter_mismatch_or_server_tool_absent",
                },
            )

        if expected_find and not has_find:
            logger.warning(
                "planner_find_tool_missing",
                extra={
                    "agent": agent_name,
                    "planner_expected_tools": expected,
                    "planner_available_tools": tool_names,
                },
            )
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

    try:
        await _append_trace_event(
            event_type="adk.prompt",
            callback_context=callback_context,
            payload={
                "agent": agent_name,
                "model": llm_request.model,
                "system_instruction": _truncate(system_instruction or "", _TRACE_TEXT_LIMIT),
                "content_texts": [_truncate(text, _TRACE_TEXT_LIMIT) for text in prompt_texts],
                "content_count": len(llm_request.contents),
                "available_tools": tool_names,
            },
        )
    except Exception:
        logger.exception("prompt_trace_append_failed")
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

    try:
        await _append_trace_event(
            event_type="adk.llm_response",
            callback_context=callback_context,
            payload={
                "agent": agent_name,
                "model_version": llm_response.model_version,
                "text_parts": [_truncate(text, _TRACE_TEXT_LIMIT) for text in response_texts],
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
    except Exception:
        logger.exception("llm_response_trace_append_failed")
    return None


async def before_tool_callback(
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
) -> dict[str, Any] | None:
    agent_name = getattr(tool_context, "agent_name", "unknown")
    logger.info(
        "tool_call_start",
        extra={
            "tool_name": tool.name,
            "agent": agent_name,
            "tool_args": args,
        },
    )
    if tool.name == "write_memory" and "return_spec" not in args:
        return {"status": "contract_violation", "reason": "missing return_spec"}

    if tool.name == "transfer_to_agent":
        destination = args.get("agent_name") if isinstance(args, dict) else None
        trace_context = _trace_context.get()
        if trace_context is not None and isinstance(destination, str):
            if destination == "planner_subagent_a":
                trace_context.planner_transfer_seen = True
                trace_context.planner_find_skill_called = False
                trace_context.planner_load_skill_called = False
                trace_context.planner_no_skill_found = False

            if (
                destination == "executor_subagent_b"
                and trace_context.require_planner_first_transfer
                and not trace_context.planner_transfer_seen
            ):
                logger.warning(
                    "transfer_blocked_planner_required",
                    extra={
                        "tool_name": tool.name,
                        "tool_args": args,
                        "reason": "planner_required_before_executor_first_turn",
                    },
                )
                return {
                    "status": "blocked",
                    "reason": "planner_required_before_executor_first_turn",
                    "required_agent": "planner_subagent_a",
                }

            if destination == "executor_subagent_b" and trace_context.planner_transfer_seen:
                if not trace_context.planner_find_skill_called:
                    logger.warning(
                        "transfer_blocked_planner_find_missing",
                        extra={
                            "planner_expected_tools": trace_context.planner_expected_tools,
                            "planner_available_tools": trace_context.planner_available_tools,
                            "planner_find_skill_called": trace_context.planner_find_skill_called,
                        },
                    )
                    return {
                        "status": "blocked",
                        "reason": "planner_must_discover_skills_before_executor",
                        "required_tool": "find_relevant_skill",
                    }
                if (
                    not trace_context.planner_load_skill_called
                    and not trace_context.planner_no_skill_found
                ):
                    logger.warning(
                        "transfer_blocked_planner_load_missing",
                        extra={
                            "planner_expected_tools": trace_context.planner_expected_tools,
                            "planner_available_tools": trace_context.planner_available_tools,
                            "planner_load_skill_called": trace_context.planner_load_skill_called,
                            "planner_no_skill_found": trace_context.planner_no_skill_found,
                        },
                    )
                    return {
                        "status": "blocked",
                        "reason": "planner_must_load_skills_before_executor",
                        "required_tool": "load_instruction_or_load_instructions",
                    }

    trace_context = _trace_context.get()
    if trace_context is not None and agent_name == "planner_subagent_a":
        if tool.name == "find_relevant_skill":
            trace_context.planner_find_skill_called = True
        if tool.name in {"load_instruction", "load_instructions"}:
            trace_context.planner_load_skill_called = True
    return None


async def after_tool_callback(
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
    result: Any = None,
    tool_response: Any = None,
    **_: Any,
) -> dict[str, Any] | None:
    agent_name = getattr(tool_context, "agent_name", "unknown")
    effective_result = tool_response if tool_response is not None else result
    result_preview = str(effective_result)[:1000] if effective_result else ""
    logger.info(
        "tool_call_end",
        extra={
            "tool_name": tool.name,
            "agent": agent_name,
            "result_preview": result_preview,
        },
    )

    trace_context = _trace_context.get()
    if (
        trace_context is not None
        and agent_name == "planner_subagent_a"
        and tool.name == "find_relevant_skill"
    ):
        trace_context.planner_no_skill_found = _result_indicates_no_skills(effective_result)

    if isinstance(effective_result, dict):
        enriched = dict(effective_result)
        enriched["tool_name"] = tool.name
        return enriched
    return None


def _result_indicates_no_skills(result: Any) -> bool:
    if result is None:
        return False
    try:
        serialized = json.dumps(result, default=str).lower()
    except Exception:
        serialized = str(result).lower()

    empty_markers = (
        '"skills": []',
        '"skill_ids": []',
        '"matched_skills": []',
        '"results": []',
        "no relevant skill",
        "no skills found",
    )
    return any(marker in serialized for marker in empty_markers)


async def on_tool_error_callback(
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
    error: Exception | None = None,
    exc: Exception | None = None,
    **_: Any,
) -> dict[str, Any]:
    effective_error = error or exc or RuntimeError("unknown_tool_error")
    logger.error(
        "tool_call_error",
        extra={
            "tool_name": tool.name,
            "error_type": type(effective_error).__name__,
            "error": str(effective_error),
        },
    )
    return {
        "status": "failed",
        "tool_name": tool.name,
        "reason": str(effective_error),
    }
