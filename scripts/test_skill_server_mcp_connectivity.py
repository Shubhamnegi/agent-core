from __future__ import annotations

import asyncio
import os
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _masked(value: str | None) -> str:
    if value is None:
        return "MISSING"
    return f"SET(len={len(value)})"


def _build_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    api_key = os.getenv("AGENT_SKILL_SERVICE_KEY")
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _pick_tool_call(tools: list[Any]) -> tuple[str, dict[str, Any]] | None:
    preferred = [
        ("find_relevant_skill", {"query": "aws bill for yesterday"}),
        ("find_relevant_skills", {"query": "aws bill for yesterday"}),
        ("load_instructions", {"skill_name": "aws_cost_explorer"}),
    ]
    names = {getattr(tool, "name", "") for tool in tools}
    for tool_name, args in preferred:
        if tool_name in names:
            return tool_name, args
    if not tools:
        return None
    fallback_name = getattr(tools[0], "name", "")
    if not fallback_name:
        return None
    return fallback_name, {}


async def main() -> int:
    load_dotenv(".env", override=True)

    url = os.getenv("AGENT_SKILL_SERVICE_URL")
    print(f"AGENT_SKILL_SERVICE_URL={url or 'MISSING'}")
    print(f"AGENT_SKILL_SERVICE_KEY={_masked(os.getenv('AGENT_SKILL_SERVICE_KEY'))}")

    if not url:
        print("FAIL: AGENT_SKILL_SERVICE_URL is missing")
        return 2

    headers = _build_headers()

    try:
        async with streamablehttp_client(url=url, headers=headers, timeout=60) as streams:
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                init_result = await session.initialize()
                print(f"initialize: ok (server={init_result.serverInfo.name})")

                listed = await session.list_tools()
                tools = listed.tools if listed and listed.tools else []
                tool_names = [getattr(tool, "name", "") for tool in tools]
                print(f"list_tools: ok (count={len(tool_names)})")
                if tool_names:
                    print("tools:")
                    for name in tool_names:
                        print(f"- {name}")

                if not tools:
                    print("FAIL: list_tools returned no tools")
                    return 3

                chosen = _pick_tool_call(tools)
                if chosen is None:
                    print("FAIL: no callable tool selected")
                    return 4
                tool_name, args = chosen

                result = await session.call_tool(tool_name, args)
                content = getattr(result, "content", None) or []
                print(f"call_tool: ok (tool={tool_name}, content_parts={len(content)})")
                if result.isError:
                    print("FAIL: tool returned isError=true")
                    return 5

                preview = str(content[:1]) if content else "[]"
                print(f"call_tool_preview={preview}")
                print("PASS: MCP connectivity check succeeded")
                return 0
    except Exception as exc:
        print(f"FAIL: MCP connectivity check failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
