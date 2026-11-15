"""Validate service proposals against exact policy evidence and current owned accounts."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx

from app.models.agent import AgentRequest
from app.models.retrieval import RetrieveResponse
from app.models.validation import (
    CustomerAccounts,
    LoanEligibilityFacts,
    LoanReference,
    ServiceRequestProposal,
    ValidationAccess,
    ValidationEvidence,
    ValidationResult,
)

# CIR-2026-07 clause 5.2; the mock API maps POL-HL-V3 to NHL-2025-10.
CIRCULAR_DATE = date(2026, 7, 1)
OPT_IN_RULE = (
    "Borrowers whose NexaHome loans were sanctioned under POL-HL-V3 before 2026-07-01 "
    "may opt in to the revised ₹10,000 part prepayment minimum by written request "
    "to their branch or through a service request."
    " There is no charge for opting in. Until a borrower opts in, the ₹25,000 minimum "
    "continues to apply to that loan. Opting in does not change the processing fee already paid."
)
OPERATIONS_DATE = date(2025, 1, 1)
SERVICE_REQUEST_RULE = (
    "Explain published products, fees, limits and policy by date; compute EMIs and fees from "
    "published terms; give indicative eligibility against published criteria; show the customer's "
    "own data; show application stage; raise service requests and cases."
)


async def read_accounts(customer_id: str, client: httpx.AsyncClient) -> CustomerAccounts:
    response = await client.get(f"v1/customers/{customer_id}/accounts")
    response.raise_for_status()
    accounts = CustomerAccounts.model_validate_json(response.content, strict=True)
    if accounts.customer_id != customer_id:
        raise ValueError("Banking response customer differs from authorized customer")
    return accounts


def _selected_loan(accounts: CustomerAccounts, loan_id: str) -> dict[str, Any] | None:
    selected = [loan for loan in accounts.loans if loan.get("loan_account_id") == loan_id]
    if len(selected) > 1:
        raise ValueError("Banking response contains ambiguous loan records")
    return selected[0] if selected else None


async def validate_service_request(
    request: AgentRequest,
    evidence: RetrieveResponse,
    client: httpx.AsyncClient,
    access: ValidationAccess,
) -> ValidationResult:
    """Check authorized service details against operations policy and owned loan facts.

    Args:
        evidence: Retained retrieval results and complete applicable amendments.
        access: Server-issued principal identity, customer entitlements and review permission.

    Returns:
        Allowed proposal with evidence, or an explicit blocked/insufficient-evidence result.

    Raises:
        ValueError: Validation evidence date differs from requested scope.
    """
    details = request.action_details
    is_case = request.action == "createCase"
    if details is not None and details.request_type == "OPT_IN_CIR_2026_07_PART_PREPAY":
        return await validate_opt_in(request, evidence, client, access)
    if (
        request.customer_id not in access.customer_ids
        or request.action not in ("createServiceRequest", "createCase")
        or request.as_of is None
        or (
            is_case
            and (
                request.case_details is None
                or request.case_details.customer_id != request.customer_id
                or details is not None
            )
        )
        or (not is_case and (details is None or request.case_details is not None))
        or (
            details is not None
            and details.request_type == "INTEREST_CERTIFICATE"
            and details.loan_account_id is None
        )
    ):
        return ValidationResult(
            decision="blocked", reason="An authorized service request is required."
        )
    if evidence.as_of != request.as_of:
        raise ValueError("Validation evidence date differs from requested scope")
    sources = [
        ValidationEvidence(citation=hit.citation, quote=hit.parent_text)
        for hit in evidence.evidence
        if hit.citation.doc_id == "POL-OPS-001"
        and hit.citation.policy_version == "1.0"
        and hit.citation.effective_date == OPERATIONS_DATE
        and SERVICE_REQUEST_RULE in " ".join(hit.parent_text.split())
    ]
    if request.as_of < OPERATIONS_DATE or not sources:
        return ValidationResult(
            decision="insufficient_evidence",
            reason="The complete applicable POL-OPS-001 clause 5.1 is required.",
        )
    assert request.customer_id is not None
    accounts = await read_accounts(request.customer_id, client)
    if details is not None and details.loan_account_id is not None:
        loan = _selected_loan(accounts, details.loan_account_id)
        if loan is None:
            return ValidationResult(
                decision="blocked", reason="The selected loan is not in the customer's accounts."
            )
        facts = LoanReference.model_validate_json(
            json.dumps({key: loan[key] for key in LoanReference.model_fields if key in loan}),
            strict=True,
        )
        if request.product_id is not None and facts.product_id != request.product_id:
            return ValidationResult(
                decision="blocked", reason="The selected loan does not match the requested product."
            )
    return ValidationResult(
        decision="allowed",
        reason=("The case proposal" if is_case else "The service-request proposal")
        + " is permitted by the advisory scope using current "
        "customer records. Human approval and execution are still required; no request was created.",
        evidence=sources,
        proposal=ServiceRequestProposal(customer_id=request.customer_id, **details.model_dump())
        if details is not None
        else None,
        case_proposal=request.case_details if is_case else None,
    )


async def validate_opt_in(
    request: AgentRequest,
    evidence: RetrieveResponse,
    client: httpx.AsyncClient,
    access: ValidationAccess,
) -> ValidationResult:
    """Check the circular opt-in rule against current owned home-loan facts.

    Args:
        evidence: Retained retrieval results and complete applicable amendments.
        access: Server-issued principal identity, customer entitlements and review permission.

    Returns:
        Allowed opt-in proposal, or an explicit failure of scope, evidence or loan eligibility.

    Raises:
        ValueError: Banking response is missing opt-in status.
    """
    if request.customer_id not in access.customer_ids:
        return ValidationResult(decision="blocked", reason="Customer access is not authorized.")
    if (
        request.action != "createServiceRequest"
        or request.action_details is None
        or request.action_details.request_type != "OPT_IN_CIR_2026_07_PART_PREPAY"
        or request.action_details.loan_account_id is None
        or request.product_id != "NEXA-HL"
        or request.as_of is None
    ):
        return ValidationResult(decision="blocked", reason="A scoped opt-in proposal is required.")
    if evidence.as_of != request.as_of:
        raise ValueError("Validation evidence date differs from requested scope")
    sources = [
        ValidationEvidence(citation=hit.citation, quote=hit.parent_text)
        for hit in evidence.amendments
        if hit.citation.doc_id == "CIR-2026-07"
        and hit.citation.policy_version == "1.0"
        and hit.citation.effective_date == CIRCULAR_DATE
        and OPT_IN_RULE in " ".join(hit.parent_text.split())
    ]
    if request.as_of < CIRCULAR_DATE or not sources:
        return ValidationResult(
            decision="insufficient_evidence",
            reason="The complete applicable CIR-2026-07 clause 5.2 is required.",
        )

    assert request.customer_id is not None
    accounts = await read_accounts(request.customer_id, client)
    loan = _selected_loan(accounts, request.action_details.loan_account_id)
    if loan is None:
        return ValidationResult(
            decision="blocked", reason="The selected loan is not in the customer's accounts."
        )
    facts = LoanEligibilityFacts.model_validate_json(
        json.dumps({key: loan[key] for key in LoanEligibilityFacts.model_fields if key in loan}),
        strict=True,
    )
    if (
        facts.product_id != "NEXA-HL"
        or facts.policy_doc != "POL-HL-V3"
        or facts.version_id != "NHL-2025-10"
        or facts.sanction_date >= CIRCULAR_DATE
    ):
        return ValidationResult(
            decision="blocked",
            evidence=sources,
            reason="Opt-in applies only to POL-HL-V3 loans sanctioned before 2026-07-01.",
        )
    if facts.circ_2026_07_part_prepay_opt_in is None:
        raise ValueError("Banking response is missing opt-in status")
    if facts.circ_2026_07_part_prepay_opt_in:
        return ValidationResult(
            decision="blocked", reason="The borrower has already opted in.", evidence=sources
        )
    return ValidationResult(
        decision="allowed",
        reason="The proposal meets circular 5.2 using current banking facts. "
        "Human approval and execution are still required; no service request was created.",
        evidence=sources,
        proposal=ServiceRequestProposal(
            customer_id=request.customer_id, **request.action_details.model_dump()
        ),
    )
