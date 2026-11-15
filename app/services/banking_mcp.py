"""HTTPX transport adapter: banking service calls execute as fixed MCP tools, not REST."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import httpx2
from cryptography.fernet import Fernet
from fastmcp import Client
from fastmcp.client.auth import OAuth
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from fastmcp.exceptions import ToolError
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel

from app.core.config import AgentSettings
from app.mcp.banking_http import signed_headers
from app.models.banking_mcp import (
    BANKING_TOOLS,
    CUSTOMER_TOOLS,
    BankingResult,
    CaseRead,
    CaseWrite,
    CustomerRead,
    EscalationWrite,
    NoteWrite,
    ServiceWrite,
    TransactionRead,
    write_grant,
)
from app.models.validation import ValidationAccess


class BankingOAuth(OAuth):
    async def redirect_handler(self, authorization_url: str) -> None:
        raise RuntimeError("Authorize Horizon first: python -m app.mcp.horizon_login")


def banking_oauth(settings: AgentSettings, *, interactive: bool = False) -> OAuth:
    if settings.banking_mcp_oauth_storage_key is None:
        raise ValueError("OAuth token encryption key is required")
    directory = settings.banking_mcp_oauth_directory.expanduser()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    storage = FileTreeStore(
        data_directory=directory,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(directory),
        collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(directory),
    )
    return (OAuth if interactive else BankingOAuth)(
        httpx_client_factory=mcp_http_client,
        token_storage=FernetEncryptionWrapper(
            key_value=storage,
            fernet=Fernet(settings.banking_mcp_oauth_storage_key.get_secret_value()),
        ),
    )


def mcp_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
    **kwargs: Any,
) -> httpx2.AsyncClient:
    """Never redirect signed scope headers to a different endpoint."""
    return httpx2.AsyncClient(
        headers=headers,
        timeout=timeout or 60,
        auth=auth,
        **{**kwargs, "follow_redirects": False},
    )


class BankingMCPTransport(httpx.AsyncBaseTransport):
    """Preserve existing validation/receipt checks while replacing the network boundary.

    Only trusted service code receives this client. Creating a POST grants that
    exact call; the caller must enforce its approval workflow before making it.
    Models and external MCP clients cannot issue grants through tool arguments.
    """

    def __init__(self, settings: AgentSettings, access: ValidationAccess | None) -> None:
        self.settings, self.access = settings, access

    def operation(self, request: httpx.Request) -> tuple[str, dict]:
        prefix = httpx.URL(str(self.settings.banking_base_url)).path.rstrip("/") + "/"
        if not request.url.path.startswith(prefix):
            raise ValueError("Unknown banking path")
        path = request.url.path[len(prefix) :].split("/")
        params = dict(request.url.params)
        arguments: BaseModel
        if request.method == "GET" and path[:2] == ["v1", "customers"] and len(path) in (3, 4):
            suffix = "" if len(path) == 3 else "/" + path[3]
            tool = next(name for name, route in CUSTOMER_TOOLS.items() if route == suffix)
            arguments = CustomerRead(customer_id=path[2])
        elif (
            request.method == "GET"
            and len(path) == 4
            and path[:2] == ["v1", "accounts"]
            and path[3] == "transactions"
        ):
            tool = "list_transactions"
            arguments = TransactionRead.model_validate(
                {
                    "customer_id": params.pop("customer_id", None),
                    "account_id": path[2],
                    "from_date": params.pop("from", None),
                    "to_date": params.pop("to", None),
                    **params,
                }
            )
        elif request.method == "GET" and len(path) == 3 and path[:2] == ["v1", "cases"]:
            tool = "get_case"
            arguments = CaseRead(customer_id=params["customer_id"], case_id=path[2])
        elif request.method == "POST":
            body = json.loads(request.content)
            key = request.headers.get("Idempotency-Key")
            if path == ["v1", "service-requests"]:
                tool, arguments = (
                    "create_service_request",
                    ServiceWrite.model_validate({"payload": body, "idempotency_key": key}),
                )
            elif path == ["v1", "cases"]:
                tool, arguments = (
                    "create_case",
                    CaseWrite.model_validate({"payload": body, "idempotency_key": key}),
                )
            elif (
                len(path) == 4
                and path[:2] == ["v1", "cases"]
                and path[3] in {"notes", "escalations"}
            ):
                data = {
                    "customer_id": params["customer_id"],
                    "case_id": path[2],
                    "payload": body,
                    "idempotency_key": key,
                }
                if path[3] == "notes":
                    tool, arguments = "add_case_note", NoteWrite.model_validate(data)
                else:
                    tool, arguments = "escalate_case", EscalationWrite.model_validate(data)
            else:
                raise ValueError("Unknown banking write")
        else:
            raise ValueError("Unknown banking operation")
        return tool, arguments.model_dump(mode="json")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.access is None:
            raise httpx.ConnectError(
                "Banking MCP requires authenticated customer scope", request=request
            )
        try:
            tool, arguments = self.operation(request)
            transport: StdioTransport | StreamableHttpTransport
            if self.settings.banking_mcp_transport == "http":
                if (
                    self.settings.banking_mcp_auth_mode == "oauth"
                    and self.settings.banking_mcp_url.scheme != "https"
                ):
                    raise ValueError("Hosted OAuth requires HTTPS")
                transport = StreamableHttpTransport(
                    url=str(self.settings.banking_mcp_url),
                    httpx_client_factory=mcp_http_client,
                    headers=signed_headers(
                        self.settings,
                        self.access,
                        write_grant(tool, arguments) if request.method == "POST" else None,
                    ),
                    auth=banking_oauth(self.settings)
                    if self.settings.banking_mcp_auth_mode == "oauth"
                    else None,
                )
            else:
                transport = StdioTransport(
                    command=sys.executable,
                    args=["-m", "app.mcp.banking_server"],
                    cwd=str(Path(__file__).resolve().parents[2]),
                    keep_alive=False,
                    env={
                        "BANKING_MCP_TRANSPORT": "stdio",
                        "BANKING_BASE_URL": str(self.settings.banking_base_url),
                        "BANKING_API_TOKEN": request.headers.get("Authorization", "").removeprefix(
                            "Bearer "
                        ),
                        "AGENT_HTTP_TIMEOUT_SECONDS": str(self.settings.agent_http_timeout_seconds),
                        "BANKING_MCP_CUSTOMER_IDS": json.dumps(sorted(self.access.customer_ids)),
                        "BANKING_MCP_WRITE_GRANT": write_grant(tool, arguments)
                        if request.method == "POST"
                        else "",
                    },
                )
            async with (
                asyncio.timeout(self.settings.agent_http_timeout_seconds),
                Client(
                    transport,
                    timeout=self.settings.agent_http_timeout_seconds,
                    init_timeout=self.settings.agent_http_timeout_seconds,
                ) as client,
            ):
                discovered = await client.list_tools()
                if {item.name for item in discovered} != BANKING_TOOLS:
                    raise ValueError("Unexpected banking tools")
                result = await client.call_tool(tool, {"request": arguments}, raise_on_error=False)
                if result.is_error:
                    raise ValueError("Banking MCP tool failed")
                envelope = BankingResult.model_validate_json(
                    json.dumps(result.structured_content), strict=True
                )
                return httpx.Response(
                    envelope.status_code,
                    json=envelope.body,
                    headers=envelope.headers,
                    request=request,
                )
        except (
            MCPError,
            ToolError,
            OSError,
            RuntimeError,
            ValueError,
            KeyError,
            StopIteration,
            httpx2.HTTPError,
        ):
            raise httpx.ConnectError(
                "Banking MCP operation could not be confirmed", request=request
            ) from None
