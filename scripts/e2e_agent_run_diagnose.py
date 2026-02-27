from __future__ import annotations

import asyncio
import os
import traceback
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from agent_core.api.main import app
from agent_core.domain.models import AgentRunRequest
from agent_core.infra.adk.runtime import AdkRuntimeScaffold


@dataclass(slots=True)
class CheckResult:
    ok: bool
    summary: str
    details: dict[str, Any]


def _mask_secret(value: str | None) -> str:
    if value is None:
        return "MISSING"
    return f"SET(len={len(value)})"


def _load_env() -> None:
    load_dotenv(".env", override=True)


def _pick_tool_name(tool_names: list[str]) -> str | None:
    preferred = ["find_relevant_skill", "load_instruction", "list_sub_skills"]
    for name in preferred:
        if name in tool_names:
            return name
    return tool_names[0] if tool_names else None


async def _check_skill_service_mcp() -> CheckResult:
    url = os.getenv("AGENT_SKILL_SERVICE_URL")
    api_key = os.getenv("AGENT_SKILL_SERVICE_KEY")
    if not url:
        return CheckResult(False, "skill_service_url_missing", {})

    headers: dict[str, str] = {}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        async with streamablehttp_client(url=url, headers=headers, timeout=60) as streams:
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                init_result = await session.initialize()
                listed = await session.list_tools()
                tools = listed.tools if listed and listed.tools else []
                tool_names = [
                    getattr(tool, "name", "")
                    for tool in tools
                    if getattr(tool, "name", "")
                ]
                chosen_tool = _pick_tool_name(tool_names)
                if chosen_tool is None:
                    return CheckResult(False, "mcp_list_tools_empty", {"tool_names": tool_names})

                call_result = await session.call_tool(
                    chosen_tool,
                    {"query": "aws bill yesterday"} if chosen_tool == "find_relevant_skill" else {},
                )
                return CheckResult(
                    ok=not bool(getattr(call_result, "isError", False)),
                    summary="mcp_connectivity_ok",
                    details={
                        "server": getattr(
                            getattr(init_result, "serverInfo", None),
                            "name",
                            "unknown",
                        ),
                        "tool_count": len(tool_names),
                        "tool_names": tool_names,
                        "chosen_tool": chosen_tool,
                        "call_is_error": bool(getattr(call_result, "isError", False)),
                    },
                )
    except Exception as exc:
        return CheckResult(
            False,
            "mcp_connectivity_failed",
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )


def _run_api_e2e(message: str) -> CheckResult:
    payload = {
        "tenant_id": "tenant_1",
        "user_id": "user_1",
        "session_id": "sess_e2e_diag_aws",
        "message": message,
        "stream": False,
    }

    try:
        with TestClient(app) as client:
            response = client.post("/agent/run", json=payload)
            detail: Any
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            return CheckResult(
                ok=response.status_code == 200,
                summary="api_e2e_status",
                details={
                    "status_code": response.status_code,
                    "response": detail,
                },
            )
    except Exception as exc:
        return CheckResult(
            False,
            "api_e2e_failed",
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )


async def _run_runtime_direct(message: str) -> CheckResult:
    mcp_timeout = float(os.getenv("AGENT_MCP_SESSION_TIMEOUT", "60.0"))
    runtime = AdkRuntimeScaffold(
        app_name="e2e-diag-runtime",
        model_name=os.getenv("AGENT_MODEL_NAME", "gemini-2.5-flash"),
        mcp_config_path=os.getenv("AGENT_MCP_CONFIG_PATH", "config/mcp_config.json"),
        skill_service_url=os.getenv("AGENT_SKILL_SERVICE_URL"),
        skill_service_key=os.getenv("AGENT_SKILL_SERVICE_KEY"),
        mcp_session_timeout=mcp_timeout,
    )
    runtime.configure_mcp_for_request({})
    runtime.configure_executor_step_tools(["find_relevant_skill"])

    planner = getattr(runtime, "_resolved_planner_endpoint", None)
    endpoints = getattr(runtime, "_resolved_executor_endpoints", [])
    endpoint_names = [getattr(endpoint, "name", "") for endpoint in endpoints]

    try:
        result = await runtime.run(
            AgentRunRequest(
                tenant_id="tenant_1",
                user_id="user_1",
                session_id="sess_e2e_diag_runtime",
                message=message,
            )
        )
        return CheckResult(
            True,
            "runtime_direct_ok",
            {
                "planner_endpoint": getattr(planner, "name", None),
                "executor_endpoints": endpoint_names,
                "status": result.status,
                "response": result.response,
            },
        )
    except Exception as exc:
        return CheckResult(
            False,
            "runtime_direct_failed",
            {
                "planner_endpoint": getattr(planner, "name", None),
                "executor_endpoints": endpoint_names,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=3),
            },
        )


def _print_result(label: str, result: CheckResult) -> None:
    print(f"\n[{label}] {result.summary} ok={result.ok}")
    for key, value in result.details.items():
        print(f"  - {key}: {value}")


def main() -> int:
    _load_env()

    if os.getenv("E2E_FORCE_IN_MEMORY", "1") == "1":
        os.environ["AGENT_STORAGE_BACKEND"] = "in_memory"

    message = os.getenv("E2E_MESSAGE", "What was the aws bill for yesterday?")

    print("E2E diagnostics starting...")
    print(f"AGENT_MODEL_NAME={os.getenv('AGENT_MODEL_NAME')}")
    print(f"AGENT_STORAGE_BACKEND={os.getenv('AGENT_STORAGE_BACKEND')}")
    print(f"AGENT_SKILL_SERVICE_URL={os.getenv('AGENT_SKILL_SERVICE_URL')}")
    print(f"AGENT_SKILL_SERVICE_KEY={_mask_secret(os.getenv('AGENT_SKILL_SERVICE_KEY'))}")
    print(f"GOOGLE_API_KEY={_mask_secret(os.getenv('GOOGLE_API_KEY'))}")

    mcp_result = asyncio.run(_check_skill_service_mcp())
    _print_result("MCP", mcp_result)

    api_result = _run_api_e2e(message)
    _print_result("API", api_result)

    runtime_result = asyncio.run(_run_runtime_direct(message))
    _print_result("RUNTIME", runtime_result)

    if mcp_result.ok and not api_result.ok and not runtime_result.ok:
        error_text = str(runtime_result.details.get("error", ""))
        if "Failed to create MCP session" in error_text:
            print(
                "\nDIAGNOSIS: skill server connectivity is healthy, "
                "but ADK runtime MCP session creation fails in app path."
            )
            print(
                "Likely cause: one of executor MCP endpoints/toolsets "
                "fails to initialize in the combined runtime flow."
            )
            return 2

    if mcp_result.ok and api_result.ok and runtime_result.ok:
        print("\nDIAGNOSIS: end-to-end orchestration path is healthy.")
        return 0

    print("\nDIAGNOSIS: mixed failure state; inspect sections above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
