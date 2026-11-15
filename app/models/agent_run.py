"""Define durable run views, reviewer decisions and canonical approval fingerprints."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.agent import AgentRequest, AgentResponse, AgentStatus, LegacyAgentResponse
from app.models.validation import ValidationResult


class ApprovalDecision(BaseModel):
    """Reviewer decision tied to the pending proposal hash.

    Attributes:
        decision: Reviewer decision bound to the exact retained proposal hash.
        proposal_hash: Canonical fingerprint binding request, proposal and retained evidence.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    proposal_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class AgentRunResponse(BaseModel):
    """Saved run status, review deadline and latest workflow response.

    Attributes:
        run_id: Identifier of the saved workflow to read or resume.
        status: Current run state or terminal workflow outcome.
        created_at: Timezone-aware timestamp when the run was created.
        expires_at: Timestamp after which a pending proposal can no longer be approved.
        proposal_hash: Canonical fingerprint binding request, proposal and retained evidence.
        response: Validated answer or workflow result retained for rendering.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    status: AgentStatus | Literal["running", "pending_approval", "expired", "unconfirmed", "failed"]
    created_at: datetime
    expires_at: datetime
    proposal_hash: str | None = None
    response: AgentResponse | None = None


class LegacyAgentRunResponse(AgentRunResponse):
    response: LegacyAgentResponse | None = None

    @model_validator(mode="after")
    def hide_unreviewable_proposal(self) -> LegacyAgentRunResponse:
        if self.response is not None and self.response.status == "blocked":
            self.status = "blocked"
            self.proposal_hash = None
        return self


def proposal_digest(request: AgentRequest, validation: ValidationResult) -> str:
    """Bind approval to the exact request, proposal and retained policy evidence.

    Args:
        validation: Retained policy and banking validation for the exact proposed action.

    Returns:
        Canonical SHA-256 fingerprint binding request, proposal and retained evidence.
    """
    body = {
        "request": request.model_dump(
            mode="json",
            exclude={
                field
                for field in ("case_details", "transactions")
                if getattr(request, field) is None
            },
        ),
        "validation": validation.model_dump(
            mode="json", exclude={"case_proposal"} if validation.case_proposal is None else set()
        ),
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
