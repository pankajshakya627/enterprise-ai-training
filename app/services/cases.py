"""Submit only the exact authorized and reviewed case proposal."""

from __future__ import annotations

import httpx
from ddtrace import tracer

from app.models.agent import AgentRequest
from app.models.validation import CaseReceipt, SubmittedCase, ValidationAccess, ValidationResult


@tracer.wrap(name="case.submit", resource="createCase")
async def submit_case(
    request: AgentRequest,
    validation: ValidationResult,
    client: httpx.AsyncClient,
    access: ValidationAccess,
    idempotency_key: str,
) -> SubmittedCase:
    proposal = request.case_details
    if (
        proposal is None
        or request.action != "createCase"
        or request.customer_id not in access.customer_ids
        or proposal.customer_id != request.customer_id
        or validation.decision != "allowed"
        or validation.case_proposal != proposal
    ):
        raise ValueError("Submission requires an authorized, exact validated case")
    response = await client.post(
        "v1/cases",
        json=proposal.model_dump(mode="json"),
        headers={"Idempotency-Key": idempotency_key},
    )
    response.raise_for_status()
    receipt = CaseReceipt.model_validate_json(response.content, strict=True)
    if any(
        getattr(receipt, field) != getattr(proposal, field)
        for field in ("customer_id", "category", "priority", "summary", "related_application_id")
    ):
        raise ValueError("Banking case receipt differs from the approved proposal")
    return SubmittedCase(case=receipt, evidence=validation.evidence)
