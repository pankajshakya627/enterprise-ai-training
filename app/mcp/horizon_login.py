"""Authorize the local Agent operator with Horizon before serving requests."""

from __future__ import annotations

import asyncio

from fastmcp import Client

from app.core.config import get_agent_settings
from app.models.banking_mcp import BANKING_TOOLS
from app.services.banking_mcp import banking_oauth


async def login() -> None:
    config = get_agent_settings()
    if config.banking_mcp_url.scheme != "https":
        raise ValueError("Horizon login requires an HTTPS MCP URL")
    async with Client(
        str(config.banking_mcp_url), auth=banking_oauth(config, interactive=True)
    ) as client:
        tools = await client.list_tools()
        if {tool.name for tool in tools} != BANKING_TOOLS:
            raise ValueError("Unexpected banking MCP deployment")


if __name__ == "__main__":
    asyncio.run(login())
