from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from google.adk.tools.mcp_tool import (
    McpToolset,
    SseConnectionParams,
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)
from mcp import StdioServerParameters

PLANNER_DISCOVERY_TOOLS = ["find_relevant_skill", "load_instructions"]


@dataclass(slots=True)
class ResolvedMcpEndpoint:
    name: str
    transport: str
    url: str | None
    command: str | None
    args: list[str]
    stdio_env: dict[str, str]
    planner_tools: list[str]
    headers: dict[str, Any]


def load_mcp_config(config_path: str) -> dict[str, Any]:
    content = Path(config_path).read_text(encoding="utf-8")
    parsed = json.loads(content)
    return cast(dict[str, Any], parsed)


def resolve_mcp_endpoint(
    endpoint_config: dict[str, Any],
    request_headers: dict[str, str],
    env_values: dict[str, str],
) -> ResolvedMcpEndpoint:
    normalized_headers = {key.lower(): value for key, value in request_headers.items()}

    transport = endpoint_config.get("transport", "streamable_http")
    if not isinstance(transport, str):
        transport = "streamable_http"
    normalized_transport = transport.strip().lower()
    if normalized_transport not in {"streamable_http", "sse", "stdio"}:
        msg = "mcp_transport_not_supported"
        raise ValueError(msg)

    raw_url: str | None = None
    command: str | None = None
    args: list[str] = []
    stdio_env: dict[str, str] = {}

    if normalized_transport == "stdio":
        command_value = endpoint_config.get("command")
        if isinstance(command_value, str) and command_value:
            command = command_value
        if command is None:
            msg = "mcp_stdio_command_missing"
            raise ValueError(msg)

        raw_args = endpoint_config.get("args", [])
        if isinstance(raw_args, list):
            args = [value for value in raw_args if isinstance(value, str)]

        raw_stdio_env = endpoint_config.get("stdio_env", {})
        if isinstance(raw_stdio_env, dict):
            stdio_env = {
                key: value
                for key, value in raw_stdio_env.items()
                if isinstance(key, str) and isinstance(value, str)
            }
    else:
        url_value = endpoint_config.get("url")
        if isinstance(url_value, str) and url_value:
            raw_url = url_value
        if raw_url is None:
            url_env = endpoint_config.get("url_env")
            if isinstance(url_env, str):
                raw_url = env_values.get(url_env)
        if not raw_url:
            msg = "mcp_endpoint_url_missing"
            raise ValueError(msg)

    resolved_headers: dict[str, Any] = {}
    auth_headers = endpoint_config.get("auth_headers", [])
    if isinstance(auth_headers, list):
        for header_rule in auth_headers:
            if not isinstance(header_rule, dict):
                continue
            header_name = header_rule.get("name")
            if not isinstance(header_name, str) or not header_name:
                continue

            value: str | None = None
            request_header_name = header_rule.get("request_header")
            if isinstance(request_header_name, str):
                value = normalized_headers.get(request_header_name.lower())

            if value is None:
                env_key = header_rule.get("env")
                if isinstance(env_key, str):
                    value = env_values.get(env_key)

            if value is not None:
                resolved_headers[header_name] = value

    planner_tools = endpoint_config.get("planner_tool_filter")
    if not isinstance(planner_tools, list) or not planner_tools:
        planner_tools = PLANNER_DISCOVERY_TOOLS
    planner_tools = [tool for tool in planner_tools if isinstance(tool, str)]

    endpoint_name = endpoint_config.get("name")
    if not isinstance(endpoint_name, str) or not endpoint_name:
        endpoint_name = "unnamed"

    return ResolvedMcpEndpoint(
        name=endpoint_name,
        transport=normalized_transport,
        url=raw_url,
        command=command,
        args=args,
        stdio_env=stdio_env,
        planner_tools=planner_tools,
        headers=resolved_headers,
    )


def build_planner_mcp_toolset(endpoint: ResolvedMcpEndpoint) -> McpToolset:
    connection_params = _build_connection_params(endpoint)
    return McpToolset(
        connection_params=connection_params,
        tool_filter=endpoint.planner_tools,
    )


def build_executor_mcp_toolset(
    endpoint: ResolvedMcpEndpoint,
    allowed_skills: list[str],
) -> McpToolset:
    connection_params = _build_connection_params(endpoint)
    return McpToolset(
        connection_params=connection_params,
        tool_filter=allowed_skills,
    )


def _build_connection_params(
    endpoint: ResolvedMcpEndpoint,
) -> StreamableHTTPConnectionParams | SseConnectionParams | StdioConnectionParams:
    if endpoint.transport == "stdio":
        if endpoint.command is None:
            msg = "mcp_stdio_command_missing"
            raise ValueError(msg)
        return StdioConnectionParams(
            server_params=StdioServerParameters(
                command=endpoint.command,
                args=endpoint.args,
                env=endpoint.stdio_env,
            )
        )

    if endpoint.url is None:
        msg = "mcp_endpoint_url_missing"
        raise ValueError(msg)

    if endpoint.transport == "sse":
        return SseConnectionParams(url=endpoint.url, headers=endpoint.headers)
    return StreamableHTTPConnectionParams(url=endpoint.url, headers=endpoint.headers)
