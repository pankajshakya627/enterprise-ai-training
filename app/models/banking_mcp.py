"""Typed banking MCP arguments and a status-preserving result envelope."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.validation import CaseBody, ServiceRequestProposal

type CustomerId = Annotated[str, Field(pattern=r"^NXC-[0-9]{4}$")]
type RecordId = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]
type OperationKey = Annotated[str, Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]

CUSTOMER_TOOLS = {
    "get_customer_profile": "",
    "list_accounts": "/accounts",
    "get_kyc_status": "/kyc-status",
    "get_bureau_summary": "/bureau-summary",
    "get_income": "/income",
    "list_applications": "/applications",
}
BANKING_TOOLS = frozenset(CUSTOMER_TOOLS) | {
    "list_transactions",
    "get_case",
    "create_case",
    "add_case_note",
    "escalate_case",
    "create_service_request",
}


class CustomerRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: CustomerId


class TransactionDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: RecordId | None = None
    from_date: date | None = None
    to_date: date | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, pattern=r"^[0-9]{1,20}$")

    @model_validator(mode="after")
    def ordered_dates(self) -> TransactionDetails:
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("Transaction start date must not follow end date")
        return self


class TransactionRead(CustomerRead, TransactionDetails):
    account_id: RecordId


class TransactionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    txn_id: str = Field(min_length=1)
    account_id: RecordId
    txn_date: date
    amount: int
    direction: Literal["debit", "credit"]
    channel: str | None = None
    narration: str
    category: str | None = None
    balance_after: int


class TransactionPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[TransactionRecord]
    next_cursor: str | None = None

    def require_scope(self, request: TransactionRead) -> None:
        if (
            len(self.items) > request.limit
            or len({row.txn_id for row in self.items}) != len(self.items)
            or any(
                row.account_id != request.account_id
                or (request.from_date is not None and row.txn_date < request.from_date)
                or (request.to_date is not None and row.txn_date > request.to_date)
                for row in self.items
            )
        ):
            raise ValueError("Banking transactions differ from the authorized query")


class TransactionResult(TransactionPage):
    account_id: RecordId


class CaseRead(CustomerRead):
    case_id: Annotated[str, Field(pattern=r"^NXCASE-[0-9]{6}$")]


class NoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)


class EscalationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Literal[
        "COMPLAINT",
        "FEE_WAIVER_OR_SPECIAL_RATE",
        "SUSPECTED_FRAUD",
        "HARDSHIP",
        "KYC_DISCREPANCY",
        "UPLOAD_WITH_EMBEDDED_INSTRUCTIONS",
        "OUT_OF_SCOPE_REQUEST",
    ]
    summary: str = Field(min_length=5, max_length=1000)
    preferred_contact: Literal["CALLBACK", "BRANCH_VISIT", "EMAIL"] = "CALLBACK"


class CaseWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: CaseBody
    idempotency_key: OperationKey


class NoteWrite(CaseRead):
    payload: NoteBody
    idempotency_key: OperationKey


class EscalationWrite(CaseRead):
    payload: EscalationBody
    idempotency_key: OperationKey


class ServiceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: ServiceRequestProposal
    idempotency_key: OperationKey


class BankingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status_code: int = Field(ge=200, le=599)
    body: dict[str, Any]
    headers: dict[str, str] = Field(default_factory=dict)


def write_grant(tool: str, arguments: dict[str, Any]) -> str:
    """Bind an internal one-operation capability to the complete canonical arguments."""
    canonical = json.dumps(
        [tool, arguments], sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
