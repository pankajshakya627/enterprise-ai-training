from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from pydantic import ValidationError

from app.models.agent import AgentRequest
from app.models.banking_mcp import TransactionDetails, TransactionRead
from app.models.validation import ValidationAccess
from app.services.transactions import read_transactions

RECORD = {
    "txn_id": "NXT-1",
    "account_id": "NXA-100001",
    "txn_date": "2026-11-10",
    "amount": 100,
    "direction": "debit",
    "channel": "UPI",
    "narration": "Synthetic purchase",
    "category": "shopping",
    "balance_after": 900,
}


@pytest.mark.parametrize(
    "mode",
    [
        "selected",
        "only_owned",
        "no_dates",
        "cursor",
        "empty",
        "no_access",
        "foreign_customer",
        "foreign_account",
        "several",
        "no_accounts",
        "bad_id",
        "duplicate_accounts",
        "bad_customer",
        "too_many",
        "duplicate_rows",
        "wrong_account",
        "before",
        "after",
        "bad_shape",
        "http",
    ],
)
def test_transactions_enforce_scope_and_return_one_bounded_page(mode: str) -> None:
    calls: list[httpx.Request] = []
    details = {
        "account_id": "NXA-100001",
        "from_date": "2026-11-01",
        "to_date": "2026-11-14",
        "limit": 5,
    }
    if mode in ("only_owned", "several", "no_accounts"):
        details["account_id"] = None
    if mode == "foreign_account":
        details["account_id"] = "NXA-FOREIGN"
    if mode == "no_dates":
        details.pop("from_date")
        details.pop("to_date")
    if mode == "cursor":
        details["cursor"] = "5"
    request = AgentRequest(
        query="Get at most five transactions from an owned account for 1–14 November 2026",
        customer_id="NXC-0001",
        transactions=details,
    )
    access = (
        None
        if mode == "no_access"
        else ValidationAccess(customer_ids=set() if mode == "foreign_customer" else {"NXC-0001"})
    )

    def handle(value: httpx.Request) -> httpx.Response:
        calls.append(value)
        assert value.method == "GET"
        if value.url.path.endswith("/accounts"):
            owned: list[dict[str, object]] = [{"account_id": "NXA-100001"}]
            if mode == "several":
                owned.append({"account_id": "NXA-100002"})
            if mode == "no_accounts":
                owned = []
            if mode == "bad_id":
                owned = [{"account_id": None}]
            if mode == "duplicate_accounts":
                owned *= 2
            return httpx.Response(
                200,
                json={
                    "customer_id": "NXC-0002" if mode == "bad_customer" else "NXC-0001",
                    "accounts": owned,
                    "loans": [],
                },
            )
        assert value.url.path == "/v1/accounts/NXA-100001/transactions"
        assert value.url.params["customer_id"] == "NXC-0001"
        assert value.url.params["limit"] == "5"
        if mode != "no_dates":
            assert value.url.params["from"] == "2026-11-01"
            assert value.url.params["to"] == "2026-11-14"
        else:
            assert "from" not in value.url.params and "to" not in value.url.params
        if mode == "cursor":
            assert value.url.params["cursor"] == "5"
        row = deepcopy(RECORD)
        if mode == "wrong_account":
            row["account_id"] = "NXA-100002"
        if mode in ("before", "after"):
            row["txn_date"] = "2026-10-31" if mode == "before" else "2026-11-15"
        items = [] if mode == "empty" else [row]
        if mode in ("too_many", "duplicate_rows"):
            items *= 6 if mode == "too_many" else 2
        return httpx.Response(
            503 if mode == "http" else 200,
            json={} if mode == "bad_shape" else {"items": items, "next_cursor": "5"},
        )

    async def run():
        async with httpx.AsyncClient(
            base_url="http://bank/", transport=httpx.MockTransport(handle)
        ) as client:
            return await read_transactions(request, client, access)

    if mode in (
        "bad_id",
        "duplicate_accounts",
        "bad_customer",
        "too_many",
        "duplicate_rows",
        "wrong_account",
        "before",
        "after",
        "bad_shape",
        "http",
    ):
        with pytest.raises((ValueError, httpx.HTTPError)):
            asyncio.run(run())
    else:
        result = asyncio.run(run())
        if mode in ("no_access", "foreign_customer", "foreign_account"):
            assert result.status == "blocked"
        elif mode in ("several", "no_accounts"):
            assert result.status == "needs_clarification"
            assert result.missing_fields == ["transactions"] and result.suggestions
        else:
            assert result.status == "answered" and result.transactions is not None
            assert result.transactions.account_id == "NXA-100001"
            assert len(result.transactions.items) == (0 if mode == "empty" else 1)
            assert result.transactions.next_cursor == "5"
            assert json.loads(result.model_dump_json())["transactions"]["items"] == (
                [] if mode == "empty" else [RECORD]
            )
    assert len(calls) == (
        0
        if mode in ("no_access", "foreign_customer")
        else 1
        if mode
        in (
            "foreign_account",
            "several",
            "no_accounts",
            "bad_id",
            "duplicate_accounts",
            "bad_customer",
        )
        else 2
    )


def test_transaction_contract_requires_ordered_dates_and_an_account_for_the_tool() -> None:
    with pytest.raises(ValidationError):
        TransactionDetails(from_date="2026-11-14", to_date="2026-11-01")
    with pytest.raises(ValidationError):
        TransactionRead.model_validate({"customer_id": "NXC-0001"})


def test_transaction_limit_is_enforced_with_unique_rows() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/accounts"):
            return httpx.Response(
                200,
                json={
                    "customer_id": "NXC-0001",
                    "accounts": [{"account_id": "NXA-100001"}],
                    "loans": [],
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [{**RECORD, "txn_id": f"NXT-{index}"} for index in range(6)],
                "next_cursor": None,
            },
        )

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="http://bank/", transport=httpx.MockTransport(handle)
        ) as client:
            await read_transactions(
                AgentRequest(
                    query="Get five transactions",
                    customer_id="NXC-0001",
                    transactions={"account_id": "NXA-100001", "limit": 5},
                ),
                client,
                ValidationAccess(customer_ids={"NXC-0001"}),
            )

    with pytest.raises(ValueError, match="differ from the authorized query"):
        asyncio.run(run())
