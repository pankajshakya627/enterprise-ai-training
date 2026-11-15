from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from app.models.agent import AgentRequest
from app.models.agent_run import proposal_digest
from app.models.validation import (
    CaseBody,
    ServiceRequestProposal,
    ValidationAccess,
    ValidationEvidence,
    ValidationResult,
)
from app.services.cases import submit_case

EVIDENCE = ValidationEvidence.model_validate(
    {
        "quote": "Raise cases.",
        "citation": {
            "doc_id": "POL-OPS-001",
            "title": "Scope",
            "policy_version": "1.0",
            "clause": "5.1",
            "effective_date": "2025-01-01",
        },
    }
)


@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "no_details",
        "wrong_action",
        "no_access",
        "wrong_customer",
        "not_allowed",
        "changed_proposal",
        "customer_id",
        "category",
        "priority",
        "summary",
        "related_application_id",
        "naive_time",
        "http",
    ],
)
def test_case_submission_rejects_unapproved_or_mismatched_proposals_and_receipts(mode: str) -> None:
    proposal = CaseBody(
        customer_id="NXC-0001",
        category="LOAN_ENQUIRY",
        priority="LOW",
        summary="Home-loan documents enquiry",
    )
    request = AgentRequest(
        query="Prepare a case",
        as_of="2026-11-15",
        customer_id="NXC-0001",
        action="createCase",
        case_details=proposal,
    )
    validation = ValidationResult(
        decision="allowed", reason="Permitted", evidence=[EVIDENCE], case_proposal=proposal
    )
    access = ValidationAccess(customer_ids=set() if mode == "no_access" else {"NXC-0001"})
    if mode == "no_details":
        request.case_details = None
    if mode == "wrong_action":
        request.action = "createServiceRequest"
    if mode == "wrong_customer":
        request.customer_id = "NXC-0010"
        access = ValidationAccess(customer_ids={"NXC-0010"})
    if mode == "not_allowed":
        validation = ValidationResult(decision="blocked", reason="Denied")
    if mode == "changed_proposal":
        validation = validation.model_copy(
            update={"case_proposal": proposal.model_copy(update={"priority": "HIGH"})}
        )
    calls = []

    def handle(value: httpx.Request) -> httpx.Response:
        calls.append(value)
        assert value.url.path == "/v1/cases" and value.method == "POST"
        assert value.headers["Idempotency-Key"] == "case-test-operation-key"
        body = {
            **proposal.model_dump(mode="json", exclude={"channel"}),
            "case_id": "NXCASE-000001",
            "status": "OPEN",
            "created_at": datetime.now(UTC).isoformat(),
            "notes": [],
            "escalation": None,
        }
        if mode in ("customer_id", "category", "priority", "summary", "related_application_id"):
            body[mode] = "NXC-0010" if mode == "customer_id" else "Changed"
        if mode == "naive_time":
            body["created_at"] = "2026-11-15T12:00:00"
        return httpx.Response(503 if mode == "http" else 201, json=body)

    async def run():
        async with httpx.AsyncClient(
            base_url="http://bank/", transport=httpx.MockTransport(handle)
        ) as client:
            return await submit_case(request, validation, client, access, "case-test-operation-key")

    if mode == "success":
        result = asyncio.run(run())
        assert result.case.case_id == "NXCASE-000001" and result.evidence == [EVIDENCE]
    else:
        with pytest.raises((ValueError, httpx.HTTPError)):
            asyncio.run(run())
    assert len(calls) == (
        0
        if mode
        in (
            "no_details",
            "wrong_action",
            "no_access",
            "wrong_customer",
            "not_allowed",
            "changed_proposal",
        )
        else 1
    )


def test_case_and_service_proposals_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError):
        ValidationResult(
            decision="allowed",
            reason="Bad mixed proposal",
            evidence=[EVIDENCE],
            proposal=ServiceRequestProposal(customer_id="NXC-0001", request_type="CALLBACK"),
            case_proposal=CaseBody(
                customer_id="NXC-0001",
                category="LOAN_ENQUIRY",
                priority="LOW",
                summary="Home-loan documents enquiry",
            ),
        )


def test_new_optional_fields_do_not_change_legacy_service_approval_digest() -> None:
    import hashlib
    import json

    request = AgentRequest(
        query="Callback",
        customer_id="NXC-0001",
        action="createServiceRequest",
        action_details={"request_type": "CALLBACK"},
    )
    validation = ValidationResult(
        decision="allowed",
        reason="Allowed",
        evidence=[EVIDENCE],
        proposal=ServiceRequestProposal(customer_id="NXC-0001", request_type="CALLBACK"),
    )
    legacy = {
        "request": request.model_dump(mode="json", exclude={"case_details", "transactions"}),
        "validation": validation.model_dump(mode="json", exclude={"case_proposal"}),
    }
    expected = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert proposal_digest(request, validation) == expected
