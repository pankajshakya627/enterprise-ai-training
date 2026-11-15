"""Call the approved local MCP tool without changing the graph's evidence contract."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from app.core.config import AgentSettings
from app.models.retrieval import RetrieveRequest, RetrieveResponse


async def retrieve_over_mcp(request: RetrieveRequest, settings: AgentSettings) -> RetrieveResponse:
    """Discover only the approved tool and retain strictly validated, dated evidence.

    The owned stdio subprocess forwards retrieval to the existing HTTP service.
    Failures stop the graph; there is no HTTP fallback or automatic retry.
    """
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        cwd=str(Path(__file__).resolve().parents[2]),
        env={
            "RAG_BASE_URL": str(settings.rag_base_url),
            "AGENT_HTTP_TIMEOUT_SECONDS": str(settings.agent_http_timeout_seconds),
        },
        keep_alive=False,
    )
    try:
        async with (
            asyncio.timeout(settings.agent_http_timeout_seconds),
            Client(
                transport,
                timeout=settings.agent_http_timeout_seconds,
                init_timeout=settings.agent_http_timeout_seconds,
            ) as client,
        ):
            tools = await client.list_tools()
            if {tool.name for tool in tools} != {"retrieve_policy"}:
                raise ValueError("Unexpected MCP tool discovery")
            result = await client.call_tool(
                "retrieve_policy",
                {"request": request.model_dump(mode="json")},
                raise_on_error=False,
            )
            if result.is_error:
                raise ValueError("MCP tool returned an error")
            evidence = RetrieveResponse.model_validate_json(
                json.dumps(result.structured_content), strict=True
            )
            if evidence.as_of != request.as_of:
                raise ValueError("MCP evidence date differs from requested scope")
            return evidence
    except MCPError, ToolError, OSError, RuntimeError, ValueError:
        raise ValueError("MCP policy retrieval unavailable") from None
