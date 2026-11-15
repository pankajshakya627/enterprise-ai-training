"""Wire model classification, HTTP retrieval and deterministic validation into the graph."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import nullcontext

import httpx
import structlog
from ddtrace.llmobs import LLMObs
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.agents.orchestrator import build_agent_graph
from app.chains.answer import build_answer_chain
from app.chains.llm import get_chat_model
from app.chains.prompts.orchestrator import (
    ORCHESTRATOR_PROMPT,
    ORCHESTRATOR_PROMPT_ID,
    ORCHESTRATOR_PROMPT_VERSION,
)
from app.core.agent_budget import AgentModelBudget
from app.core.config import AgentSettings, Settings
from app.core.observability import prompt_annotation_context
from app.models.agent import AgentRequest, AgentResponse, IntentDecision
from app.models.answer import AnswerResponse
from app.models.retrieval import RetrieveRequest, RetrieveResponse
from app.models.validation import (
    SubmittedCase,
    SubmittedServiceRequest,
    ValidationAccess,
    ValidationResult,
)
from app.services.agent_mcp_retrieval import retrieve_over_mcp
from app.services.agent_retrieval import retrieve_over_http
from app.services.transactions import read_transactions
from app.services.validation import validate_service_request

logger = structlog.get_logger()
agent_metrics: Counter[str] = Counter()


def build_workflow(
    settings: Settings,
    client: httpx.AsyncClient,
    *,
    access: ValidationAccess | None = None,
    banking_client: httpx.AsyncClient | None = None,
    agent_settings: AgentSettings | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    actioner: Callable[
        [AgentRequest, ValidationResult, str], Awaitable[SubmittedServiceRequest | SubmittedCase]
    ]
    | None = None,
) -> CompiledStateGraph:
    """Wire the model, retrieval adapter and optional banking capabilities into the graph.

    Args:
        access: Server-issued principal identity, customer entitlements and review permission.
        banking_client: Authenticated banking client, or None when the integration is disabled.
        agent_settings: Validated agent identities, service endpoints and work limits.
        checkpointer: Durable graph checkpoint store required for human-reviewed actions.
        actioner: Optional execution callback for an exactly approved service proposal.

    Returns:
        Compiled classified graph wired to the supplied optional capabilities.

    Example:
        graph = build_workflow(settings, rag_client, agent_settings=config)
    """
    model_settings = (
        settings.model_copy(
            update={"chat_max_output_tokens": agent_settings.agent_model_max_output_tokens}
        )
        if agent_settings is not None
        else settings
    )
    llm = get_chat_model(model_settings)
    budget = (
        AgentModelBudget(agent_settings, settings.token_encoding)
        if agent_settings is not None
        else None
    )
    callbacks: list[BaseCallbackHandler] = [budget] if budget is not None else []
    classification = ORCHESTRATOR_PROMPT | llm.with_structured_output(
        IntentDecision, method="json_schema", strict=True
    )

    async def classify(value: AgentRequest) -> IntentDecision:
        # Notes belong to the fixed bank payload, not to the routing model.
        """Classify explicit request scope using the versioned structured intent prompt."""
        routing_request = value.model_dump(mode="json")
        if routing_request["action_details"] is not None:
            routing_request["action_details"].pop("notes", None)
        variables = {"request": json.dumps(routing_request)}
        with prompt_annotation_context(
            ORCHESTRATOR_PROMPT,
            variables,
            prompt_id=ORCHESTRATOR_PROMPT_ID,
            version=ORCHESTRATOR_PROMPT_VERSION,
        ):
            return IntentDecision.model_validate(
                await classification.ainvoke(variables, config={"callbacks": callbacks})
            )

    async def retrieve(value: RetrieveRequest) -> RetrieveResponse:
        """Retrieve scoped policy evidence through the configured retrieval integration."""
        if agent_settings is not None and agent_settings.agent_retrieval_transport == "mcp":
            return await retrieve_over_mcp(value, agent_settings)
        return await retrieve_over_http(value, client)

    async def answer(value: RetrieveRequest, evidence: RetrieveResponse) -> AnswerResponse:
        """Generate a grounded answer using evidence retained for the request.

        Args:
            evidence: Retained retrieval results and complete applicable amendments.
        """
        chain = build_answer_chain(RunnableLambda(lambda _: evidence), llm, settings)
        return await chain.ainvoke(value, config={"callbacks": callbacks})

    async def validate(value: AgentRequest, evidence: RetrieveResponse) -> ValidationResult:
        """Validate the service proposal using policy evidence and current authorized banking facts.

        Args:
            evidence: Retained retrieval results and complete applicable amendments.
        """
        assert banking_client is not None and access is not None
        with (
            LLMObs.workflow(
                name=(
                    "validator_opt_in"
                    if value.action_details is not None
                    and value.action_details.request_type == "OPT_IN_CIR_2026_07_PART_PREPAY"
                    else "validator_service_request"
                ),
                agent_service=settings.dd_service,
            )
            if LLMObs.enabled
            else nullcontext()
        ):
            result = await validate_service_request(value, evidence, banking_client, access)
        logger.info("agent_validation_completed", decision=result.decision)
        return result

    async def customer_read(value: AgentRequest) -> AgentResponse:
        assert banking_client is not None
        return await read_transactions(value, banking_client, access)

    return build_agent_graph(
        RunnableLambda(classify),
        RunnableLambda(retrieve),
        answer,
        validator=validate if banking_client is not None else None,
        access=access,
        checkpointer=checkpointer,
        actioner=actioner,
        customer_reader=customer_read if banking_client is not None else None,
    )


async def orchestrate(
    request: AgentRequest,
    settings: Settings,
    agent_settings: AgentSettings,
    client: httpx.AsyncClient,
    *,
    access: ValidationAccess | None = None,
    banking_client: httpx.AsyncClient | None = None,
) -> AgentResponse:
    """Execute one independent validation-only or policy-answer graph request.

    Args:
        agent_settings: Validated agent identities, service endpoints and work limits.
        access: Server-issued principal identity, customer entitlements and review permission.
        banking_client: Authenticated banking client, or None when the integration is disabled.

    Returns:
        Validated response from the selected terminal graph branch.

    Example:
        response = await orchestrate(request, settings, config, rag_client)

    Raises:
        TimeoutError: The configured workflow deadline expires.
        ValueError: The structured model output or reserved model work is invalid.
        httpx.HTTPError: An upstream HTTP operation fails.
    """
    agent_metrics["requests"] += 1
    graph = build_workflow(
        settings,
        client,
        access=access,
        banking_client=banking_client,
        agent_settings=agent_settings,
    )
    with (
        LLMObs.workflow(name="agent_orchestration", agent_service=settings.dd_service)
        if LLMObs.enabled
        else nullcontext()
    ):
        async with asyncio.timeout(agent_settings.agent_run_timeout_seconds):
            state = await graph.ainvoke({"request": request}, config={"recursion_limit": 8})
    response = AgentResponse.model_validate(state["response"])
    agent_metrics["completed"] += 1
    logger.info("agent_completed", intent=response.intent, status=response.status)
    return response
