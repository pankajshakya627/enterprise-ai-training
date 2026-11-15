"""Read a bounded transaction page after checking current account ownership."""

from __future__ import annotations

import httpx
from ddtrace import tracer
from pydantic import TypeAdapter

from app.models.agent import AgentRequest, AgentResponse
from app.models.banking_mcp import RecordId, TransactionPage, TransactionRead, TransactionResult
from app.models.validation import ValidationAccess
from app.services.validation import read_accounts


@tracer.wrap(name="banking.transactions", resource="list_transactions")
async def read_transactions(
    request: AgentRequest, client: httpx.AsyncClient, access: ValidationAccess | None
) -> AgentResponse:
    if access is None or request.customer_id not in access.customer_ids:
        return AgentResponse(
            intent="customer_context",
            status="blocked",
            message="Customer access is not authorized.",
        )
    assert request.customer_id is not None and request.transactions is not None
    details = request.transactions
    accounts = await read_accounts(request.customer_id, client)
    owned = TypeAdapter(list[RecordId]).validate_python(
        [account.get("account_id") for account in accounts.accounts], strict=True
    )
    if len(set(owned)) != len(owned):
        raise ValueError("Banking account identities are invalid or ambiguous")
    account_id = details.account_id
    if account_id is None:
        if len(owned) != 1:
            return AgentResponse(
                intent="missing_information",
                status="needs_clarification",
                message="Please select an owned account for the transaction query.",
                missing_fields=["transactions"],
                suggestions=["Choose an account: " + ", ".join(owned)]
                if owned
                else ["No deposit accounts were returned. Confirm the customer ID."],
            )
        account_id = owned[0]
    if account_id not in owned:
        return AgentResponse(
            intent="customer_context",
            status="blocked",
            message="The selected account does not belong to this customer.",
        )
    selection = TransactionRead(
        customer_id=request.customer_id, **{**details.model_dump(), "account_id": account_id}
    )
    params = {"customer_id": selection.customer_id, "limit": str(selection.limit)}
    for key, value in (("from", selection.from_date), ("to", selection.to_date)):
        if value is not None:
            params[key] = value.isoformat()
    if selection.cursor is not None:
        params["cursor"] = selection.cursor
    response = await client.get(f"v1/accounts/{selection.account_id}/transactions", params=params)
    response.raise_for_status()
    page = TransactionPage.model_validate_json(response.content, strict=True)
    page.require_scope(selection)
    return AgentResponse(
        intent="customer_context",
        status="answered",
        message=f"Found {len(page.items)} transactions for {selection.account_id} within the requested scope.",
        transactions=TransactionResult(
            account_id=selection.account_id, items=page.items, next_cursor=page.next_cursor
        ),
    )
