"""Scoped banking MCP facade; only this layer connects to the bank REST backend."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from secrets import compare_digest
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.middleware import Middleware

from app.core.config import get_agent_settings
from app.core.logging import configure_logging
from app.mcp.banking_http import BankingHTTPAuth, request_session
from app.models.banking_mcp import (
    CUSTOMER_TOOLS,
    BankingResult,
    CaseRead,
    CaseWrite,
    CustomerRead,
    EscalationWrite,
    NoteWrite,
    ServiceWrite,
    TransactionPage,
    TransactionRead,
    write_grant,
)
from app.models.validation import CustomerAccounts, ServiceRequestReceipt, ValidationAccess


def build_banking_server(
    http: httpx.AsyncClient,
    access: ValidationAccess,
    grant: str | None = None,
    *,
    session_provider: Callable[[], tuple[ValidationAccess, str | None]] | None = None,
) -> FastMCP:
    """Bind a trusted session scope; no tool can supply its own permissions or grant."""
    server = FastMCP("NexaBank scoped tools", strict_input_validation=True, mask_error_details=True)

    def authorize(customer_id: str) -> None:
        current_access = access if session_provider is None else session_provider()[0]
        if customer_id not in current_access.customer_ids:
            raise ToolError(
                "Customer access is not authorized. Inspector reads require this customer in "
                "the server's BANKING_MCP_CUSTOMER_IDS; restart the server after configuring it."
            )

    def approve(tool: str, request: Any) -> None:
        current_grant = grant if session_provider is None else session_provider()[1]
        if current_grant is None or not compare_digest(
            current_grant, write_grant(tool, request.model_dump(mode="json"))
        ):
            raise ToolError(
                "An exact application-issued write grant is required. Token-only Inspector "
                "connections are read-only; submit through the Service requests "
                "owner/reviewer workflow."
            )

    async def exchange(method: str, path: str, **kwargs: Any) -> BankingResult:
        try:
            response = await http.request(method, path, **kwargs)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ToolError("Invalid banking response")
            return BankingResult(
                status_code=response.status_code,
                body=body,
                headers={
                    k: v
                    for k, v in response.headers.items()
                    if k in {"etag", "location", "retry-after", "idempotent-replayed"}
                },
            )
        except httpx.HTTPStatusError as exc:
            return BankingResult(
                status_code=exc.response.status_code,
                body={"detail": "Banking operation unavailable"},
                headers={
                    k: v
                    for k, v in exc.response.headers.items()
                    if k in {"retry-after", "idempotent-replayed"}
                },
            )
        except httpx.HTTPError, ValueError:
            raise ToolError("Banking operation could not be confirmed") from None

    def customer_tool(name: str, suffix: str) -> None:
        async def read(request: CustomerRead) -> BankingResult:
            authorize(request.customer_id)
            result = await exchange("GET", f"v1/customers/{request.customer_id}{suffix}")
            if result.status_code < 300:
                if result.body.get("customer_id") != request.customer_id:
                    raise ToolError("Banking customer identity could not be confirmed")
                if name == "list_accounts":
                    CustomerAccounts.model_validate_json(json.dumps(result.body), strict=True)
            return result

        server.tool(
            name=name,
            description=f"Read authorized customer {name.replace('_', ' ')}.",
            annotations={"readOnlyHint": True},
        )(read)

    for name, suffix in CUSTOMER_TOOLS.items():
        customer_tool(name, suffix)

    @server.tool(annotations={"readOnlyHint": True})
    async def list_transactions(request: TransactionRead) -> BankingResult:
        """Read a dated, bounded page of an authorized customer's own account transactions."""
        authorize(request.customer_id)
        accounts = await exchange("GET", f"v1/customers/{request.customer_id}/accounts")
        if accounts.status_code >= 300:
            return accounts
        owned = CustomerAccounts.model_validate_json(json.dumps(accounts.body), strict=True)
        if owned.customer_id != request.customer_id or not any(
            row.get("account_id") == request.account_id for row in owned.accounts
        ):
            raise ToolError("Account access is not authorized")
        params = {"limit": str(request.limit)}
        for key, value in {
            "from": request.from_date,
            "to": request.to_date,
            "cursor": request.cursor,
        }.items():
            if value is not None:
                params[key] = str(value)
        result = await exchange(
            "GET", f"v1/accounts/{request.account_id}/transactions", params=params
        )
        if result.status_code < 300:
            try:
                page = TransactionPage.model_validate_json(json.dumps(result.body), strict=True)
                page.require_scope(request)
            except ValueError:
                raise ToolError("Transaction response could not be confirmed") from None
        return result

    async def owned_case(request: CaseRead) -> BankingResult:
        authorize(request.customer_id)
        result = await exchange("GET", f"v1/cases/{request.case_id}")
        if result.status_code < 300 and (
            result.body.get("customer_id") != request.customer_id
            or result.body.get("case_id") != request.case_id
        ):
            raise ToolError("Case access is not authorized")
        return result

    server.tool(
        name="get_case",
        description="Read a case belonging to an authorized customer.",
        annotations={"readOnlyHint": True},
    )(owned_case)

    @server.tool(annotations={"readOnlyHint": False, "idempotentHint": True})
    async def create_case(request: CaseWrite) -> BankingResult:
        """Create an exactly granted case with an unchanged operation key."""
        authorize(request.payload.customer_id)
        approve("create_case", request)
        return await exchange(
            "POST",
            "v1/cases",
            json=request.payload.model_dump(mode="json"),
            headers={"Idempotency-Key": request.idempotency_key},
        )

    async def case_write(
        tool: str, request: NoteWrite | EscalationWrite, suffix: str
    ) -> BankingResult:
        authorize(request.customer_id)
        approve(tool, request)
        existing = await owned_case(
            CaseRead(customer_id=request.customer_id, case_id=request.case_id)
        )
        if existing.status_code >= 300:
            return existing
        return await exchange(
            "POST",
            f"v1/cases/{request.case_id}/{suffix}",
            json=request.payload.model_dump(mode="json"),
            headers={"Idempotency-Key": request.idempotency_key},
        )

    @server.tool(annotations={"readOnlyHint": False, "idempotentHint": True})
    async def add_case_note(request: NoteWrite) -> BankingResult:
        """Append an exactly granted note after checking case ownership."""
        return await case_write("add_case_note", request, "notes")

    @server.tool(annotations={"readOnlyHint": False, "idempotentHint": True})
    async def escalate_case(request: EscalationWrite) -> BankingResult:
        """Escalate an owned case using an exact grant and operation key."""
        return await case_write("escalate_case", request, "escalations")

    @server.tool(annotations={"readOnlyHint": False, "idempotentHint": True})
    async def create_service_request(request: ServiceWrite) -> BankingResult:
        """Submit the exact service proposal granted by the trusted approved-action path."""
        authorize(request.payload.customer_id)
        approve("create_service_request", request)
        result = await exchange(
            "POST",
            "v1/service-requests",
            json=request.payload.model_dump(mode="json"),
            headers={"Idempotency-Key": request.idempotency_key},
        )
        if result.status_code < 300:
            receipt = ServiceRequestReceipt.model_validate_json(
                json.dumps(result.body), strict=True
            )
            if (receipt.customer_id, receipt.request_type, receipt.loan_account_id) != (
                request.payload.customer_id,
                request.payload.request_type,
                request.payload.loan_account_id,
            ):
                raise ToolError("Banking receipt identity could not be confirmed")
        return result

    return server


async def main() -> None:
    config = get_agent_settings()
    configure_logging("INFO")
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    if config.banking_api_token is None:
        raise ValueError("Banking backend credentials are required")
    access = ValidationAccess(
        customer_ids=config.banking_mcp_customer_ids, principal_id="mcp-session"
    )
    async with httpx.AsyncClient(
        base_url=str(config.banking_base_url).rstrip("/") + "/",
        headers={"Authorization": "Bearer " + config.banking_api_token.get_secret_value()},
        timeout=config.agent_http_timeout_seconds,
        follow_redirects=False,
    ) as http:
        if config.banking_mcp_transport == "http":
            if config.banking_mcp_token is None or config.banking_mcp_signing_key is None:
                raise ValueError("HTTP MCP credentials are required")
            server = build_banking_server(http, access, session_provider=request_session)
            await server.run_async(
                transport="http",
                host="127.0.0.1",
                port=config.banking_mcp_port,
                path="/mcp",
                json_response=True,
                stateless_http=True,
                show_banner=False,
                middleware=[Middleware(BankingHTTPAuth, settings=config)],
            )
        else:
            await build_banking_server(
                http,
                access,
                config.banking_mcp_write_grant.get_secret_value()
                if config.banking_mcp_write_grant
                else None,
            ).run_async(transport="stdio", show_banner=False)


if __name__ == "__main__":
    asyncio.run(main())
