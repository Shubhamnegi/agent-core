from google.adk.tools.mcp_tool import (
    SseConnectionParams,
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)

from agent_core.infra.adk.mcp import (
    ResolvedMcpEndpoint,
    build_planner_mcp_toolset,
    resolve_mcp_endpoint,
)


def test_resolve_mcp_endpoint_defaults_to_streamable_http() -> None:
    endpoint = resolve_mcp_endpoint(
        endpoint_config={
            "name": "skill_service",
            "url": "https://example.com/mcp",
            "planner_tool_filter": ["find_relevant_skill", "load_instructions"],
        },
        request_headers={},
        env_values={},
    )

    assert endpoint.transport == "streamable_http"


def test_build_planner_toolset_uses_streamable_http_by_default() -> None:
    endpoint = ResolvedMcpEndpoint(
        name="skill_service",
        transport="streamable_http",
        url="https://example.com/mcp",
        command=None,
        args=[],
        stdio_env={},
        planner_tools=["find_relevant_skill", "load_instructions"],
        headers={"x-api-key": "secret"},
    )

    toolset = build_planner_mcp_toolset(endpoint)

    assert isinstance(toolset._connection_params, StreamableHTTPConnectionParams)


def test_build_planner_toolset_uses_sse_when_configured() -> None:
    endpoint = ResolvedMcpEndpoint(
        name="legacy_endpoint",
        transport="sse",
        url="https://example.com/sse",
        command=None,
        args=[],
        stdio_env={},
        planner_tools=["find_relevant_skill"],
        headers={},
    )

    toolset = build_planner_mcp_toolset(endpoint)

    assert isinstance(toolset._connection_params, SseConnectionParams)


def test_build_planner_toolset_uses_stdio_when_configured() -> None:
    endpoint = ResolvedMcpEndpoint(
        name="aws_cost_explorer",
        transport="stdio",
        url=None,
        command="uvx",
        args=["awslabs.cost-explorer-mcp-server@latest"],
        stdio_env={"FASTMCP_LOG_LEVEL": "ERROR", "AWS_PROFILE": "default"},
        planner_tools=["get_yesterday_cost"],
        headers={},
    )

    toolset = build_planner_mcp_toolset(endpoint)

    assert isinstance(toolset._connection_params, StdioConnectionParams)
