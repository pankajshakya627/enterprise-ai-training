"""Serve validation-only agent calls and the explicitly gated legacy submission path."""

from __future__ import annotations

from typing import Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from langchain_core.exceptions import OutputParserException
from langchain_openai.chat_models.base import OpenAIRefusalError
from openai import OpenAIError

from app.api.auth import get_validation_access
from app.api.dependencies import banking_client, rag_client
from app.api.operations import health
from app.core.config import AgentSettings, Settings, get_agent_settings, get_settings
from app.models.agent import (
    AgentRequest,
    AgentResponse,
    AgentSubmissionRequest,
    LegacyAgentResponse,
)
from app.models.validation import SubmittedServiceRequest, ValidationAccess
from app.services.agent import agent_metrics, orchestrate
from app.services.service_requests import submit_service_request

router = APIRouter()
v2_router = APIRouter(prefix="/v2")
logger = structlog.get_logger()
router.add_api_route("/health", health, methods=["GET"])

# Older independently deployed clients reject extra response fields.
LEGACY_RESPONSE_EXCLUDE: dict[str, Any] = {
    "suggestions": True,
    "transactions": True,
    "case_submission": True,
    "validation": {"case_proposal": True},
}


@router.get("/metrics")
async def metrics() -> dict[str, int]:
    """Return process-local agent workflow counters."""
    return {
        "agent_requests_total": agent_metrics["requests"],
        "agent_completed_total": agent_metrics["completed"],
    }


@router.post(
    "/agent",
    response_model=LegacyAgentResponse,
    response_model_exclude={**LEGACY_RESPONSE_EXCLUDE, "submission": True},
)
@v2_router.post(
    "/agent", response_model=AgentResponse, response_model_exclude={"submission", "case_submission"}
)
async def agent(
    request: AgentRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    agent_settings: Annotated[AgentSettings, Depends(get_agent_settings)],
    client: Annotated[httpx.AsyncClient, Depends(rag_client)],
    access: Annotated[ValidationAccess | None, Depends(get_validation_access)],
    bank: Annotated[httpx.AsyncClient | None, Depends(banking_client)],
) -> AgentResponse:
    """Execute an independent policy-answer or validation-only agent request.

    Args:
        agent_settings: Validated agent identities, service endpoints and work limits.
        access: Server-issued principal identity, customer entitlements and review permission.
        bank: Authenticated banking client, or None when unconfigured.

    Raises:
        HTTPException: Agent service unavailable.
    """
    try:
        return await orchestrate(
            request, settings, agent_settings, client, access=access, banking_client=bank
        )
    except (
        httpx.HTTPError,
        OpenAIError,
        OpenAIRefusalError,
        OutputParserException,
        ValueError,
        TimeoutError,
    ) as exc:
        logger.error("agent_failed", error_type=type(exc).__name__)
        raise HTTPException(status_code=503, detail="Agent service unavailable") from None


@router.post(
    "/agent/submit",
    response_model=SubmittedServiceRequest | LegacyAgentResponse,
    response_model_exclude={**LEGACY_RESPONSE_EXCLUDE, "submission": True},
)
async def submit(
    request: AgentSubmissionRequest,
    idempotency_key: Annotated[
        str, Header(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    ],
    settings: Annotated[Settings, Depends(get_settings)],
    agent_settings: Annotated[AgentSettings, Depends(get_agent_settings)],
    client: Annotated[httpx.AsyncClient, Depends(rag_client)],
    access: Annotated[ValidationAccess | None, Depends(get_validation_access)],
    bank: Annotated[httpx.AsyncClient | None, Depends(banking_client)],
) -> SubmittedServiceRequest | AgentResponse:
    """Validate and submit only through the explicitly enabled legacy write endpoint.

    Args:
        idempotency_key: Persisted operation key reused for the unchanged banking submission.
        agent_settings: Validated agent identities, service endpoints and work limits.
        access: Server-issued principal identity, customer entitlements and review permission.
        bank: Authenticated banking client, or None when unconfigured.

    Raises:
        HTTPException: Submission could not be confirmed. Retry with the same Idempotency-Key and unchanged request body; the bank may have received it.
    """
    if access is None or request.customer_id not in access.customer_ids:
        raise HTTPException(status_code=403, detail="Customer access is not authorized")
    if not agent_settings.agent_allow_direct_submit:
        raise HTTPException(
            status_code=403, detail="Direct submission is disabled; use an approved agent run"
        )
    if bank is None:
        raise HTTPException(status_code=503, detail="Banking integration is unavailable")
    result = await agent(request, settings, agent_settings, client, access, bank)
    if result.status != "validated" or result.validation is None:
        return result
    try:
        return await submit_service_request(
            request, result.validation, bank, access, idempotency_key
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("service_request_submission_unconfirmed", error_type=type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Submission could not be confirmed. Retry with the same Idempotency-Key "
            "and unchanged request body; the bank may have received it.",
        ) from None
