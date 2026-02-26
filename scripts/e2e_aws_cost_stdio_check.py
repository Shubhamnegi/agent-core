import asyncio

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from google.genai import types
from mcp import StdioServerParameters


async def main() -> None:
    toolset = McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="uvx",
                args=["awslabs.cost-explorer-mcp-server@latest"],
                env={"FASTMCP_LOG_LEVEL": "ERROR", "AWS_PROFILE": "default"},
            ),
            timeout=60,
        )
    )

    agent = LlmAgent(
        name="aws_cost_agent",
        model="gemini-2.5-flash",
        instruction=(
            "Use AWS cost MCP tools to answer cost questions. "
            "Always call a tool for cost requests."
        ),
        tools=[toolset],
    )

    runner = InMemoryRunner(agent=agent, app_name="aws-cost-e2e")
    user_id = "user_test"
    session_id = "session_test"

    await runner.session_service.create_session(
        app_name="aws-cost-e2e",
        user_id=user_id,
        session_id=session_id,
        state={},
    )

    events = runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=types.Content(
            role="user",
            parts=[types.Part(text="what is the aws cost for yesterday")],
        ),
    )

    used_tools: list[str] = []
    final_response = ""

    async for event in events:
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) if content is not None else None
        if not parts:
            continue

        for part in parts:
            function_call = getattr(part, "function_call", None)
            if function_call is not None:
                used_tools.append(getattr(function_call, "name", "unknown"))

            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                final_response = text.strip()

    print("used_tools=", used_tools)
    print("final_response=", final_response)


if __name__ == "__main__":
    asyncio.run(main())
