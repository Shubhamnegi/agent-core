from __future__ import annotations

from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams

PLANNER_DISCOVERY_TOOLS = ["find_relevant_skills", "load_skill"]


def build_planner_mcp_toolset(mcp_server_url: str) -> McpToolset:
    return McpToolset(
        connection_params=SseConnectionParams(url=mcp_server_url),
        tool_filter=PLANNER_DISCOVERY_TOOLS,
    )


def build_executor_mcp_toolset(mcp_server_url: str, allowed_skills: list[str]) -> McpToolset:
    return McpToolset(
        connection_params=SseConnectionParams(url=mcp_server_url),
        tool_filter=allowed_skills,
    )
