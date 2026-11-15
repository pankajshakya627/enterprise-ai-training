"""Manage scoped durable runs, reviewer approval, leases and banking action recovery."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
import structlog
from ddtrace.llmobs import LLMObs
from langchain_core.exceptions import OutputParserException
from langchain_core.runnables import RunnableConfig
from langchain_openai.chat_models.base import OpenAIRefusalError
from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from openai import OpenAIError
from pymongo.errors import PyMongoError

from app.agents.guardrails import unsafe_input
from app.core.config import AgentSettings, Settings
from app.database.agent_runs import MongoRunStore
from app.models.agent import AgentRequest, AgentSubmissionRequest
from app.models.agent_run import AgentRunResponse, ApprovalDecision
from app.models.validation import (
    SubmittedCase,
    SubmittedServiceRequest,
    ValidationAccess,
    ValidationResult,
)
from app.services.agent import agent_metrics, build_workflow
from app.services.cases import submit_case
from app.services.service_requests import submit_service_request

logger = structlog.get_logger()


class RunError(Exception):
    """Carry a safe HTTP status and detail across the service/router boundary."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code, self.detail = status_code, detail
        super().__init__(detail)


class AgentRunService:
    """Coordinate scoped durable runs and exact reviewer-authorized banking operations.

    Args:
        rag: Bounded client for the RAG service.
        bank: Authenticated banking client, or None when unconfigured.
        store: Scoped run persistence and checkpoint adapter.
        access: Server-issued principal identity, customer entitlements and review permission.
    """

    @classmethod
    async def create(
        cls,
        settings: Settings,
        config: AgentSettings,
        rag: httpx.AsyncClient,
        bank: httpx.AsyncClient | None,
        access: ValidationAccess,
    ) -> AgentRunService:
        """Construct the scoped service with its durable MongoDB run store.

        Args:
            rag: Bounded client for the RAG service.
            bank: Authenticated banking client, or None when unconfigured.
            access: Server-issued principal identity, customer entitlements and review permission.

        Returns:
            Scoped service instance.

        Raises:
            PyMongoError: Run storage or checkpoint initialization fails.
        """
        store = await asyncio.to_thread(MongoRunStore, settings)
        return cls(settings, config, rag, bank, store, access)

    def __init__(
        self,
        settings: Settings,
        config: AgentSettings,
        rag: httpx.AsyncClient,
        bank: httpx.AsyncClient | None,
        store: MongoRunStore,
        access: ValidationAccess,
    ) -> None:
        self.settings, self.config, self.rag, self.bank = settings, config, rag, bank
        self.store, self.access = store, access

    def _config(self, run_id: str) -> RunnableConfig:
        return {
            "configurable": {"thread_id": self.settings.institution + ":" + run_id},
            "recursion_limit": 12,
        }

    def _bank_scope(self) -> str:
        value = (
            "unconfigured"
            if self.bank is None
            else str(self.bank.base_url) + "\n" + self.bank.headers.get("Authorization", "")
        )
        if self.config.banking_transport == "mcp":
            value += "\ntransport=mcp"
            if self.config.banking_mcp_transport == "http":
                value += "\nhttp=" + str(self.config.banking_mcp_url)
                if self.config.banking_mcp_auth_mode == "oauth":
                    value += "\nauth=oauth"
                if self.config.banking_mcp_token is not None:
                    value += "\n" + self.config.banking_mcp_token.get_secret_value()
                if self.config.banking_mcp_signing_key is not None:
                    value += "\n" + self.config.banking_mcp_signing_key.get_secret_value()
        return hashlib.sha256(value.encode()).hexdigest()

    def _owner(self, record: dict[str, Any]) -> ValidationAccess:
        for principal in self.config.agent_principals:
            if principal.principal_id == record["owner_id"] and (
                record["customer_id"] is None or record["customer_id"] in principal.customer_ids
            ):
                return ValidationAccess(
                    customer_ids=principal.customer_ids, principal_id=principal.principal_id
                )
        raise RunError(403, "The run owner's customer access is no longer authorized")

    async def _record(self, run_id: UUID) -> dict[str, Any]:
        record = await self.store.get(str(run_id))
        if (
            record is None
            or (
                record["customer_id"] is not None
                and record["customer_id"] not in self.access.customer_ids
            )
            or (
                record["owner_id"] != self.access.principal_id
                and (not self.access.can_approve or record["customer_id"] is None)
            )
        ):
            raise RunError(404, "Agent run not found")
        return record

    def _graph(self, record: dict[str, Any], lease: str) -> CompiledStateGraph:
        async def execute(
            request: AgentRequest, validation: ValidationResult, key: str
        ) -> SubmittedServiceRequest | SubmittedCase:
            """Recheck banking identity, execution lease and owner access before the external write.

            Args:
                validation: Retained policy and banking validation for the exact proposed action.

            Raises:
                RunError: The run lease was lost; no new submission was started.
            """
            if self.bank is None or self._bank_scope() != record["bank_scope"]:
                raise RunError(
                    409, "The banking identity or destination changed; reconcile this run"
                )
            started = record.get("first_attempt_at")
            if started is not None and datetime.now(UTC) - started.replace(tzinfo=UTC) >= timedelta(
                hours=23
            ):
                raise RunError(409, "The safe retry window expired; reconcile the banking request")
            record["first_attempt_at"] = started or datetime.now(UTC)
            if not await self.store.update(
                record["_id"], lease, first_attempt_at=record["first_attempt_at"]
            ):
                raise RunError(409, "The run lease was lost; no new submission was started")
            # Recheck current entitlements immediately before the external write.
            owner = self._owner(record)
            if request.action == "createCase":
                return await submit_case(request, validation, self.bank, owner, key)
            return await submit_service_request(
                AgentSubmissionRequest.model_validate(request.model_dump()),
                validation,
                self.bank,
                owner,
                key,
            )

        return build_workflow(
            self.settings,
            self.rag,
            banking_client=self.bank,
            agent_settings=self.config,
            access=self._owner(record),
            checkpointer=self.store.checkpointer,
            actioner=execute if self.bank is not None else None,
        )

    async def _view(self, record: dict[str, Any], graph: CompiledStateGraph) -> AgentRunResponse:
        state = await graph.aget_state(self._config(record["_id"]))
        response = state.values.get("response")
        status = record["status"]
        if response is not None and response.status == "submitted":
            status = "submitted"
        elif state.next == ("approval",):
            status = (
                "expired"
                if record["expires_at"].replace(tzinfo=UTC) <= datetime.now(UTC)
                else "pending_approval"
            )
        elif not state.next and response is not None:
            status = response.status
        return AgentRunResponse(
            run_id=record["_id"],
            status=status,
            created_at=record["created_at"],
            expires_at=record["expires_at"],
            proposal_hash=state.values.get("proposal_hash"),
            response=response,
        )

    async def get(self, run_id: UUID) -> AgentRunResponse:
        """Read a visible saved run and derive its current graph/expiry status.

        Args:
            run_id: Identifier of the saved workflow to read or resume.

        Returns:
            Validated saved-run response reflecting the current workflow state.

        Raises:
            RunError: The run is not visible or its owner is no longer authorized.
            PyMongoError: Checkpoint or run storage cannot be read.

        Example:
            run = await service.get(run_id)
        """
        record = await self._record(run_id)
        return await self._view(record, self._graph(record, "read-only"))

    async def start(self, request: AgentRequest) -> AgentRunResponse:
        """Create an owner-bound run and begin classified proposal preparation.

        Returns:
            Validated saved-run response reflecting the current workflow state.

        Raises:
            RunError: Remove sensitive identifiers or instruction overrides before starting a run.

        Example:
            run = await service.start(request)
        """
        if request.customer_id is not None and request.customer_id not in self.access.customer_ids:
            raise RunError(403, "Customer access is not authorized")
        if unsafe_input(request):
            raise RunError(
                422, "Remove sensitive identifiers or instruction overrides before starting a run"
            )
        now = datetime.now(UTC)
        record = {
            "_id": str(uuid4()),
            "owner_id": self.access.principal_id,
            "customer_id": request.customer_id,
            "created_at": now,
            "expires_at": now + timedelta(seconds=self.config.agent_approval_ttl_seconds),
            "status": "running",
            "idempotency_key": uuid4().hex,
            "bank_scope": self._bank_scope(),
            "first_attempt_at": None,
        }
        await self.store.create(record)
        return await self._drive(record, "start", request=request)

    async def approve(self, run_id: UUID, decision: ApprovalDecision) -> AgentRunResponse:
        """Record a different authorized reviewer's exact decision and resume the run.

        Args:
            run_id: Identifier of the saved workflow to read or resume.
            decision: Reviewer decision bound to the exact retained proposal hash.

        Returns:
            Validated saved-run response reflecting the current workflow state.

        Raises:
            RunError: A different authorized reviewer must decide this proposal.

        Example:
            run = await service.approve(run_id, decision)
        """
        if not self.access.can_approve:
            raise RunError(403, "Reviewer authorization is required")
        record = await self._record(run_id)
        if record["owner_id"] == self.access.principal_id:
            raise RunError(403, "A different authorized reviewer must decide this proposal")
        return await self._drive(record, "approve", decision=decision)

    async def retry(self, run_id: UUID) -> AgentRunResponse:
        """Resume only the saved approved banking operation eligible for safe recovery.

        Args:
            run_id: Identifier of the saved workflow to read or resume.

        Returns:
            Validated saved-run response reflecting the current workflow state.

        Raises:
            RunError: The run is not visible or the saved operation is not safely retryable.

        Example:
            run = await service.retry(run_id)
        """
        return await self._drive(await self._record(run_id), "retry")

    async def _drive(
        self,
        record: dict[str, Any],
        operation: Literal["start", "approve", "retry"],
        *,
        request: AgentRequest | None = None,
        decision: ApprovalDecision | None = None,
    ) -> AgentRunResponse:
        lease = uuid4().hex
        claimed = await self.store.claim(
            record["_id"], lease, self.config.agent_run_timeout_seconds + 20
        )
        if claimed is None:
            raise RunError(409, "This agent run is already being processed")
        record, status = claimed, claimed["status"]
        try:
            async with asyncio.timeout(self.config.agent_run_timeout_seconds):
                graph = self._graph(record, lease)
                view = await self._view(record, graph)
                if operation == "approve":
                    assert decision is not None
                    if (
                        view.status != "pending_approval"
                        or view.proposal_hash != decision.proposal_hash
                    ):
                        raise RunError(
                            409, "Approval is expired, stale or does not match the pending proposal"
                        )
                    saved = await self.store.update(
                        record["_id"],
                        lease,
                        approval={
                            **decision.model_dump(),
                            "reviewer_id": self.access.principal_id,
                            "decided_at": datetime.now(UTC),
                        },
                    )
                    if not saved:
                        raise RunError(409, "The run lease was lost; approval was not recorded")
                    value: Any = Command(resume=decision.model_dump())
                elif operation == "retry":
                    if view.status == "submitted":
                        status = view.status
                        return view
                    state = await graph.aget_state(self._config(record["_id"]))
                    if state.next != ("action",) or record["first_attempt_at"] is None:
                        raise RunError(409, "Only an unconfirmed approved action can be retried")
                    value = None
                else:
                    assert request is not None
                    value = {"request": request, "idempotency_key": record["idempotency_key"]}
                if not await self.store.update(record["_id"], lease, status="running"):
                    raise RunError(409, "The run lease was lost; execution was not started")
                status = "failed"
                agent_metrics["requests"] += 1
                with (
                    LLMObs.workflow(name="agent_run", agent_service=self.settings.dd_service)
                    if LLMObs.enabled
                    else nullcontext()
                ):
                    await graph.ainvoke(value, self._config(record["_id"]), durability="sync")
                state = await graph.aget_state(self._config(record["_id"]))
                if state.values.get("request") is not None:
                    record["customer_id"] = state.values["request"].customer_id
                    if not await self.store.update(
                        record["_id"], lease, customer_id=record["customer_id"]
                    ):
                        raise RunError(
                            409, "The run lease was lost; reload the run before continuing"
                        )
                view = await self._view(record, graph)
                status = view.status
                agent_metrics["completed"] += 1
                logger.info("agent_run_completed", run_id=record["_id"], status=status)
                return view
        except RunError:
            raise
        except (
            httpx.HTTPError,
            ValueError,
            TimeoutError,
            PyMongoError,
            OpenAIError,
            OpenAIRefusalError,
            OutputParserException,
            GraphRecursionError,
        ) as exc:
            logger.error("agent_run_failed", run_id=record["_id"], error_type=type(exc).__name__)
            detail = (
                "Submission is unconfirmed; retry this run without changing its request"
                if record.get("first_attempt_at") is not None
                else "Agent run unavailable"
            )
            raise RunError(503, detail) from None
        finally:
            if status == "failed" and record.get("first_attempt_at") is not None:
                status = "unconfirmed"
            await self.store.release(record["_id"], lease, status)
