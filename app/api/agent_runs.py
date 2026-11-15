"""Expose scoped durable runs, separate reviewer decisions and safe action retries."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import PyMongoError

from app.api.agent import LEGACY_RESPONSE_EXCLUDE
from app.api.auth import get_validation_access
from app.api.dependencies import banking_client, rag_client
from app.core.config import AgentSettings, Settings, get_agent_settings, get_settings
from app.models.agent import AgentRequest
from app.models.agent_run import AgentRunResponse, ApprovalDecision, LegacyAgentRunResponse
from app.models.validation import ValidationAccess
from app.services.agent_runs import AgentRunService, RunError

router = APIRouter(prefix="/agent/runs", tags=["Agent workflows"])
v2_router = APIRouter(prefix="/v2/agent/runs", tags=["Agent workflows v2"])
logger = structlog.get_logger()


def named_access(
    access: Annotated[ValidationAccess | None, Depends(get_validation_access)],
) -> ValidationAccess:
    """Require a named identity for durable runs instead of a legacy training credential.

    Args:
        access: Server-issued principal identity, customer entitlements and review permission.

    Returns:
        Authenticated named access eligible to enter the durable-run service.

    Raises:
        HTTPException: Named agent credentials are required.
    """
    if access is None or access.principal_id == "training":
        raise HTTPException(status_code=403, detail="Named agent credentials are required")
    return access


async def run_service(
    access: Annotated[ValidationAccess, Depends(named_access)],
    settings: Annotated[Settings, Depends(get_settings)],
    config: Annotated[AgentSettings, Depends(get_agent_settings)],
    rag: Annotated[httpx.AsyncClient, Depends(rag_client)],
    bank: Annotated[httpx.AsyncClient | None, Depends(banking_client)],
) -> AsyncIterator[AgentRunService]:
    """Provide the scoped run service and translate safe service/storage errors to HTTP.

    Args:
        access: Server-issued principal identity, customer entitlements and review permission.
        rag: Bounded client for the RAG service.
        bank: Authenticated banking client, or None when unconfigured.

    Yields:
        Scoped durable-run service for the authenticated named principal.

    Raises:
        HTTPException: Agent run storage unavailable.
    """
    try:
        yield await AgentRunService.create(settings, config, rag, bank, access)
    except RunError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    except (PyMongoError, ValueError, TimeoutError) as exc:
        logger.error("agent_run_storage_failed", error_type=type(exc).__name__)
        raise HTTPException(status_code=503, detail="Agent run storage unavailable") from None


RunService = Annotated[AgentRunService, Depends(run_service)]


@router.post(
    "",
    response_model=LegacyAgentRunResponse,
    response_model_exclude={"response": LEGACY_RESPONSE_EXCLUDE},
    status_code=201,
)
@v2_router.post("", response_model=AgentRunResponse, status_code=201)
async def start(request: AgentRequest, service: RunService) -> AgentRunResponse:
    """Start a named-owner durable workflow."""
    return await service.start(request)


@router.get(
    "/{run_id}",
    response_model=LegacyAgentRunResponse,
    response_model_exclude={"response": LEGACY_RESPONSE_EXCLUDE},
)
@v2_router.get("/{run_id}", response_model=AgentRunResponse)
async def read(run_id: UUID, service: RunService) -> AgentRunResponse:
    """Read an owner-visible or authorized reviewer-visible saved run.

    Args:
        run_id: Identifier of the saved workflow to read or resume.
    """
    return await service.get(run_id)


@router.post(
    "/{run_id}/approval",
    response_model=LegacyAgentRunResponse,
    response_model_exclude={"response": LEGACY_RESPONSE_EXCLUDE},
)
@v2_router.post("/{run_id}/approval", response_model=AgentRunResponse)
async def approve(
    run_id: UUID, decision: ApprovalDecision, service: RunService
) -> AgentRunResponse:
    """Apply the reviewer decision through the scoped durable-run service.

    Args:
        run_id: Identifier of the saved workflow to read or resume.
        decision: Reviewer decision bound to the exact retained proposal hash.
    """
    return await service.approve(run_id, decision)


@router.post(
    "/{run_id}/retry",
    response_model=LegacyAgentRunResponse,
    response_model_exclude={"response": LEGACY_RESPONSE_EXCLUDE},
)
@v2_router.post("/{run_id}/retry", response_model=AgentRunResponse)
async def retry(run_id: UUID, service: RunService) -> AgentRunResponse:
    """Recover an eligible unconfirmed action through its unchanged saved run.

    Args:
        run_id: Identifier of the saved workflow to read or resume.
    """
    return await service.retry(run_id)
