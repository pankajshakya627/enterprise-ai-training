"""Expose dated policy evidence, its contract and an explanation prompt over stdio."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP
from fastmcp.exceptions import PromptError, ToolError

from app.chains.prompts.mcp import POLICY_EXPLANATION_PROMPT
from app.core.config import get_agent_settings
from app.core.logging import configure_logging
from app.models.retrieval import RetrieveRequest, RetrieveResponse
from app.services.agent_retrieval import retrieve_over_http


def build_mcp_server(client: httpx.AsyncClient) -> FastMCP:
    """Bind the protocol to an owned HTTP client; never access Atlas or banking here."""
    server = FastMCP(
        "NexaAssist policy retrieval",
        version="1.0.0",
        strict_input_validation=True,
        mask_error_details=True,
    )

    @server.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
    async def retrieve_policy(request: RetrieveRequest) -> RetrieveResponse:
        """Retrieve dated policy evidence with citations and complete amendments."""
        try:
            return await retrieve_over_http(request, client)
        except (httpx.HTTPError, ValueError) as exc:
            structlog.get_logger().warning("mcp_retrieval_failed", error_type=type(exc).__name__)
            raise ToolError(
                "Policy retrieval failed; check the RAG service and request scope."
            ) from None

    @server.resource("nexa://retrieval/contract/v1", mime_type="application/json")
    def retrieval_contract() -> dict[str, Any]:
        """Read the current request and response schemas without private configuration."""
        return {
            "request_schema": RetrieveRequest.model_json_schema(),
            "response_schema": RetrieveResponse.model_json_schema(),
        }

    @server.prompt(version="1.0.0")
    def explain_policy(request_json: str) -> str:
        """Prepare a dated, cited explanation request; this does not generate an answer."""
        try:
            request = RetrieveRequest.model_validate_json(request_json)
        except ValueError:
            raise PromptError(
                "Supply a valid retrieval request JSON with an explicit as_of date."
            ) from None
        return POLICY_EXPLANATION_PROMPT.format(request_json=request.model_dump_json())

    return server


async def main() -> None:
    config = get_agent_settings()
    configure_logging("INFO")
    # Stdio reserves stdout for MCP; application logs belong on stderr.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    async with httpx.AsyncClient(
        base_url=str(config.rag_base_url), timeout=config.agent_http_timeout_seconds
    ) as client:
        await build_mcp_server(client).run_async(transport="stdio", show_banner=False)


if __name__ == "__main__":
    asyncio.run(main())
