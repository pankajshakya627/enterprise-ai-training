"""Discover and exercise the local policy server from a separate Python process."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import structlog
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from app.models.retrieval import RetrieveRequest, RetrieveResponse


async def inspect_policy(request: RetrieveRequest) -> dict:
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
        if {tool.name for tool in tools} != {"retrieve_policy"}:
            raise ValueError("The server must expose only the approved retrieval tool")
        resources = await client.list_resources()
        prompts = await client.list_prompts()
        contract = await client.read_resource("nexa://retrieval/contract/v1")
        prompt = await client.get_prompt(
            "explain_policy", {"request_json": request.model_dump_json()}
        )
        result = await client.call_tool(
            "retrieve_policy", {"request": request.model_dump(mode="json")}
        )
        response = RetrieveResponse.model_validate(result.structured_content)
        if response.as_of != request.as_of:
            raise ValueError("MCP response date differs from requested scope")
        return {
            "tools": [tool.model_dump(mode="json", by_alias=True) for tool in tools],
            "resources": [str(resource.uri) for resource in resources],
            "prompts": [item.name for item in prompts],
            "contract": json.loads(contract[0].text),
            "explanation_prompt": prompt.model_dump(mode="json", by_alias=True),
            "retrieval": response.model_dump(mode="json"),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--product-id")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = RetrieveRequest(query=args.query, as_of=args.as_of, product_id=args.product_id)
    report = asyncio.run(inspect_policy(request))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    structlog.get_logger().info("mcp_client_completed", output=str(args.output))


if __name__ == "__main__":
    main()
