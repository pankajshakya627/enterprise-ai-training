"""Define explicit agent scope, classified intent and terminal response contracts."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.answer import AnswerResponse
from app.models.banking_mcp import TransactionDetails, TransactionResult
from app.models.validation import (
    CaseBody,
    ServiceRequestDetails,
    ServiceRequestType,
    SubmittedCase,
    SubmittedServiceRequest,
    ValidationResult,
)

Intent = Literal[
    "policy_question", "missing_information", "forbidden", "customer_context", "supported_action"
]
Action = Literal["createCase", "addCaseNote", "escalateCase", "createServiceRequest"]
MissingField = Literal[
    "product_id",
    "as_of",
    "customer_id",
    "action",
    "action_details",
    "question",
    "case_details",
    "transactions",
]
type AgentStatus = Literal[
    "answered",
    "insufficient_evidence",
    "needs_clarification",
    "forbidden",
    "unavailable",
    "validated",
    "blocked",
    "rejected",
    "submitted",
]


class AgentRequest(BaseModel):
    """User query with optional policy scope and banking action details.

    Attributes:
        query: User question or scoped retrieval query.
        as_of: Policy lookup date; not the customer loan sanction date.
        product_id: Explicit product scope, when required or supplied.
        customer_id: Synthetic customer identifier within the authorized scope.
        action: Explicit supported banking operation requested by the caller.
        action_details: Typed service-request payload; never an authorization grant.
        jurisdiction: Jurisdiction boundary retained in the policy lookup scope.
        top_k: Maximum number of ranked primary sources requested.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)
    as_of: date | None = None
    product_id: str | None = Field(default=None, min_length=1)
    customer_id: str | None = Field(default=None, pattern=r"^NXC-[0-9]{4}$")
    action: Action | None = None
    action_details: ServiceRequestDetails | None = None
    case_details: CaseBody | None = None
    transactions: TransactionDetails | None = None
    jurisdiction: str | None = Field(default=None, min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class AgentSubmissionRequest(AgentRequest):
    """Payload for the gated legacy service-request submission endpoint.

    Attributes:
        as_of: Policy lookup date; not the customer loan sanction date.
        customer_id: Synthetic customer identifier within the authorized scope.
        action: Explicit supported banking operation requested by the caller.
        action_details: Typed service-request payload; never an authorization grant.
    """

    as_of: date
    customer_id: str = Field(pattern=r"^NXC-[0-9]{4}$")
    action: Literal["createServiceRequest"]
    action_details: ServiceRequestDetails


class IntentDecision(BaseModel):
    """Structured model output; extracted values must be explicit in the request.

    Attributes:
        intent: Structured classification used to choose the graph branch.
        product_id: Explicit product scope, when required or supplied.
        as_of: Policy lookup date; not the customer loan sanction date.
        customer_id: Synthetic customer identifier within the authorized scope.
        action: Explicit supported banking operation requested by the caller.
        request_type: Service-request type explicitly requested in fields or text.
        loan_account_id: Loan identifier explicitly supplied in fields or text.
        product_required: Whether classification requires a product before proceeding.
        missing_fields: Scope fields the caller must provide or reconcile.
    """

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    product_id: str | None
    as_of: date | None
    customer_id: str | None = Field(pattern=r"^NXC-[0-9]{4}$")
    action: Action | None
    request_type: ServiceRequestType | None = None
    loan_account_id: str | None = Field(default=None, min_length=1, max_length=100)
    case_details: CaseBody | None = None
    transactions: TransactionDetails | None = None
    product_required: bool
    missing_fields: list[MissingField]


class AgentResponse(BaseModel):
    """Result of the selected policy, validation or action branch.

    Attributes:
        intent: Structured classification used to choose the graph branch.
        status: HTTP or workflow status used by the scenario.
        message: Safe user-facing explanation of the workflow outcome.
        missing_fields: Scope fields the caller must provide or reconcile.
        suggestions: Supported options and input guidance; never assumed field values.
        answer: Generated answer whose exact text and citations are being evaluated.
        validation: Retained policy and banking validation for the exact proposed action.
        submission: Verified banking receipt retained after an external submission.
    """

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    status: AgentStatus
    message: str
    missing_fields: list[MissingField] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    answer: AnswerResponse | None = None
    validation: ValidationResult | None = None
    submission: SubmittedServiceRequest | None = None
    case_submission: SubmittedCase | None = None
    transactions: TransactionResult | None = None


class LegacyAgentResponse(AgentResponse):
    @model_validator(mode="before")
    @classmethod
    def supported_v1_result(cls, value: object) -> dict:
        response = AgentResponse.model_validate(value)
        if (
            response.transactions is not None
            or response.case_submission is not None
            or (response.validation is not None and response.validation.case_proposal is not None)
            or any(field in {"case_details", "transactions"} for field in response.missing_fields)
        ):
            response = AgentResponse(
                intent="forbidden",
                status="blocked",
                message="This result requires the v2 Agent chat API. Open Agent chat to continue.",
            )
        return response.model_dump()
