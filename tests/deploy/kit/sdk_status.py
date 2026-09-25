"""The MCP servers a Claude Agent SDK (Python) session connects, as its own
get_mcp_status() reports them, with no model turn and no API key.

    python sdk_status.py --cwd DIR --options options.json

Run with the interpreter that holds claude-agent-sdk. options.json holds the
ClaudeAgentOptions fields under test (`plugins`, `mcp_servers`, ...).
ClaudeSDKClient.connect() with no prompt starts the session and its MCP
servers; get_mcp_status() is asked until no server is `pending`, then one JSON
line is printed: [{name, status, error, tools: [names]}].
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

POLL_SECONDS = 0.1
BOUND_SECONDS = 60


def summary(servers: list[dict]) -> list[dict]:
    return [{"name": server["name"], "status": server["status"], "error": server.get("error"),
             "tools": [tool["name"] for tool in server.get("tools") or []]} for server in servers]


def pending(servers: list[dict]) -> bool:
    return any(server["status"] == "pending" for server in servers)


async def settled(client) -> list[dict]:
    deadline = time.monotonic() + BOUND_SECONDS
    while True:
        servers = (await client.get_mcp_status())["mcpServers"]
        if not pending(servers) or time.monotonic() > deadline:
            return servers
        await asyncio.sleep(POLL_SECONDS)


async def status(cwd: str, options: dict) -> list[dict]:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    client = ClaudeSDKClient(options=ClaudeAgentOptions(cwd=cwd, **options))
    await client.connect()
    try:
        return summary(await settled(client))
    finally:
        await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--options", required=True)
    args = parser.parse_args()
    options = json.loads(Path(args.options).read_text(encoding="utf-8"))
    print(json.dumps(asyncio.run(status(args.cwd, options))))


if __name__ == "__main__":
    main()
