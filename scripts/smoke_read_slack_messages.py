from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from agent_core.infra.adk.tool_communication import read_slack_messages


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test for read_slack_messages tool")
    parser.add_argument(
        "--channel",
        default=os.getenv("SLACK_SMOKE_CHANNEL", ""),
        help="Slack channel id (or set SLACK_SMOKE_CHANNEL)",
    )
    parser.add_argument("--limit", type=int, default=10, help="Message limit (1..200)")
    parser.add_argument(
        "--no-files",
        action="store_true",
        help="Skip attached file metadata",
    )
    return parser


async def _run(channel: str, limit: int, include_files: bool) -> int:
    if not channel:
        print("SMOKE_NOT_CONFIGURED missing channel. Use --channel or SLACK_SMOKE_CHANNEL.")
        return 2

    result = await read_slack_messages(
        channel=channel,
        limit=limit,
        include_files=include_files,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))

    status = result.get("status")
    if status == "ok":
        print(f"SMOKE_OK read_slack_messages count={result.get('count', 0)}")
        return 0
    if status == "not_configured":
        print("SMOKE_NOT_CONFIGURED missing slack credentials/config")
        return 2

    print(f"SMOKE_FAILED reason={result.get('reason')}")
    return 1


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    exit_code = asyncio.run(
        _run(
            channel=args.channel,
            limit=args.limit,
            include_files=not args.no_files,
        )
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
