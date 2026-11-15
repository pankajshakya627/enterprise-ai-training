from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, get_args
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from app.agent_main import create_agent_app
from app.agents.orchestrator import build_agent_graph
from app.api import agent as agent_api
from app.api import retrieval as retrieval_api
from app.chains.prompts.orchestrator import ORCHESTRATOR_PROMPT
from app.core.config import AgentSettings, Settings, get_agent_settings, get_settings
from app.main import create_app
from app.models.agent import AgentRequest, IntentDecision
from app.models.answer import AnswerDraft, AnswerResponse
from app.models.retrieval import RetrieveRequest, RetrieveResponse
from app.models.validation import ValidationAccess, ValidationResult
from app.services import agent as agent_service


def decision(**updates: Any) -> IntentDecision:
    return IntentDecision.model_validate(
        {
            "intent": "policy_question",
            "product_id": None,
            "as_of": None,
            "customer_id": None,
            "action": None,
            "product_required": True,
            "missing_fields": [],
            **updates,
        }
    )


@pytest.fixture
def agent_request() -> AgentRequest:
    return AgentRequest(
        query="What is the home loan fee?",
        as_of="2026-10-01",
        product_id="NEXA-HL",
        jurisdiction="IN",
        top_k=7,
    )


@pytest.fixture
def evidence() -> RetrieveResponse:
    """Combine complete amendments and results without duplicating document versions."""
    return RetrieveResponse.model_validate(
        {
            "as_of": "2026-10-01",
            "results": [
                {
                    "rank": 1,
                    "score": 0.9,
                    "text": "Base policy term.",
                    "parent_text": "Base policy term. Full parent section with related clauses.",
                    "citation": {
                        "doc_id": "POL-TEST",
                        "title": "Test policy",
                        "policy_version": "3.0",
                        "clause": "6.2",
                        "effective_date": "2026-01-01",
                    },
                }
            ],
            "amendments": [
                {
                    "rank": 1,
                    "score": 0,
                    "text": "Source-backed test term.",
                    "parent_text": "Source-backed test term. Applies only to new sanctions.",
                    "citation": {
                        "doc_id": "CIR-TEST",
                        "title": "Test circular",
                        "policy_version": "1.0",
                        "clause": None,
                        "effective_date": "2026-07-01",
                    },
                },
                {
                    "rank": 2,
                    "score": 0,
                    "text": "Later amendment term.",
                    "parent_text": "Later amendment term. Existing sanctions retain the base term.",
                    "citation": {
                        "doc_id": "CIR-TEST-2",
                        "title": "Later test circular",
                        "policy_version": "1.1",
                        "clause": "2.1",
                        "effective_date": "2026-09-01",
                    },
                },
            ],
            "timings": {"search_ms": 1, "parent_ms": 2, "total_ms": 3},
        }
    )


def test_policy_graph_runs_in_order_once(
    agent_request: AgentRequest, evidence: RetrieveResponse
) -> None:
    retrieve = AsyncMock(return_value=evidence)
    answer = AsyncMock(
        return_value=AnswerResponse(
            as_of=evidence.as_of,
            status="insufficient_evidence",
            answer="No answer",
            citations=[],
            retrieval_timings=evidence.timings,
            generation_ms=0,
        )
    )
    graph = build_agent_graph(
        RunnableLambda(lambda _: decision()), RunnableLambda(retrieve), answer
    )

    async def run() -> list[str]:
        return [next(iter(update)) async for update in graph.astream({"request": agent_request})]

    assert asyncio.run(run()) == ["orchestrator", "retriever", "grounded_answer"]
    retrieve.assert_awaited_once_with(
        RetrieveRequest(
            query=agent_request.query,
            as_of=agent_request.as_of,
            product_id="NEXA-HL",
            jurisdiction="IN",
            top_k=7,
        )
    )
    answer.assert_awaited_once_with(retrieve.call_args.args[0], evidence)


@pytest.mark.parametrize(
    "choice,changes,authorized,status",
    [
        ({"intent": "forbidden"}, {}, True, "forbidden"),
        ({"intent": "customer_context"}, {}, False, "blocked"),
        ({"intent": "customer_context"}, {"as_of": None}, True, "needs_clarification"),
        ({"intent": "customer_context"}, {"action_details": None}, True, "unavailable"),
        ({"intent": "customer_context"}, {"action": None}, True, "unavailable"),
        (
            {"intent": "customer_context", "customer_id": "NXC-0002"},
            {},
            True,
            "needs_clarification",
        ),
        (
            {"intent": "customer_context"},
            {"action_details": {"request_type": "INTEREST_CERTIFICATE"}},
            True,
            "needs_clarification",
        ),
    ],
)
def test_explicit_service_request_routing_preserves_preflight_guards(
    agent_request: AgentRequest, choice: dict, changes: dict, authorized: bool, status: str
) -> None:
    request = AgentRequest.model_validate(
        {
            **agent_request.model_dump(),
            "query": "Prepare a callback service request.",
            "customer_id": "NXC-0010",
            "action": "createServiceRequest",
            "action_details": {"request_type": "CALLBACK"},
            **changes,
        }
    )
    retrieve, validate, answer = AsyncMock(), AsyncMock(), AsyncMock()
    graph = build_agent_graph(
        RunnableLambda(lambda _: decision(**choice)),
        RunnableLambda(retrieve),
        answer,
        validator=validate,
        access=ValidationAccess(customer_ids={"NXC-0010"} if authorized else set()),
    )
    state = asyncio.run(graph.ainvoke({"request": request}))
    assert state["response"].status == status
    retrieve.assert_not_awaited()
    validate.assert_not_awaited()
    answer.assert_not_awaited()


@pytest.mark.parametrize(
    "choice,fields,status,missing",
    [
        ({"intent": "forbidden"}, {}, "forbidden", []),
        ({}, {"as_of": None}, "needs_clarification", ["as_of"]),
        ({}, {"product_id": None}, "needs_clarification", ["product_id"]),
        ({"intent": "customer_context"}, {}, "needs_clarification", ["customer_id"]),
        ({"intent": "customer_context"}, {"customer_id": "NXC-0002"}, "unavailable", []),
        ({"intent": "supported_action"}, {}, "needs_clarification", ["customer_id", "action"]),
        (
            {"intent": "supported_action", "missing_fields": ["action_details"]},
            {"customer_id": "NXC-0002", "action": "createCase"},
            "needs_clarification",
            ["action_details"],
        ),
        (
            {"intent": "supported_action"},
            {"customer_id": "NXC-0002", "action": "createServiceRequest"},
            "unavailable",
            [],
        ),
        ({"intent": "missing_information"}, {}, "needs_clarification", ["question"]),
        ({"product_id": "NEXA-PL"}, {}, "needs_clarification", ["product_id"]),
        ({"as_of": "2025-01-01"}, {}, "needs_clarification", ["as_of"]),
        (
            {"intent": "forbidden", "missing_fields": ["customer_id"]},
            {"as_of": None},
            "forbidden",
            [],
        ),
    ],
)
def test_terminal_routes_never_retrieve_or_generate(
    agent_request: AgentRequest, choice: dict, fields: dict, status: str, missing: list
) -> None:
    retrieve, answer = AsyncMock(), AsyncMock()
    graph = build_agent_graph(
        RunnableLambda(lambda _: decision(**choice)), RunnableLambda(retrieve), answer
    )
    state = asyncio.run(graph.ainvoke({"request": agent_request.model_copy(update=fields)}))
    assert state["response"].status == status
    assert state["response"].missing_fields == missing
    assert state["response"].answer is None
    retrieve.assert_not_called()
    answer.assert_not_called()


def test_explicit_text_scope_is_extracted(evidence: RetrieveResponse) -> None:
    retrieve = AsyncMock(return_value=evidence)
    answer = AsyncMock(
        return_value=AnswerResponse(
            as_of=evidence.as_of,
            status="insufficient_evidence",
            answer="No answer",
            citations=[],
            retrieval_timings=evidence.timings,
            generation_ms=0,
        )
    )
    graph = build_agent_graph(
        RunnableLambda(lambda _: decision(product_id="NEXA-HL", as_of="2026-10-01")),
        RunnableLambda(retrieve),
        answer,
    )
    asyncio.run(
        graph.ainvoke(
            {
                "request": AgentRequest(
                    query="Home loan fee as of 2026-10-01",
                    jurisdiction="IN",
                    top_k=7,
                )
            }
        )
    )
    assert retrieve.call_args.args[0].model_dump(mode="json") == {
        "query": "Home loan fee as of 2026-10-01",
        "as_of": "2026-10-01",
        "product_id": "NEXA-HL",
        "jurisdiction": "IN",
        "top_k": 7,
    }


@pytest.mark.parametrize(
    "draft_status,invalid_quote",
    [
        ("answered", False),
        ("answered", True),
        ("insufficient_evidence", False),
        ("needs_clarification", False),
    ],
)
def test_agent_reuses_real_phase1_http_route_and_answer_chain(
    settings: Settings,
    agent_request: AgentRequest,
    evidence: RetrieveResponse,
    monkeypatch: pytest.MonkeyPatch,
    draft_status: str,
    invalid_quote: bool,
) -> None:
    retrieve = AsyncMock(return_value=evidence)
    monkeypatch.setattr(retrieval_api, "retrieve_policy", retrieve)
    llm = Mock(spec=BaseChatModel)
    draft = AnswerDraft.model_validate(
        {
            "status": draft_status,
            "answer": "Test terms [1] [2] [3].",
            "evidence": [
                {
                    "evidence_id": 1,
                    "quote": "Invented" if invalid_quote else "Source-backed test term.",
                },
                {"evidence_id": 2, "quote": "Later amendment term."},
                {"evidence_id": 3, "quote": "Base policy term."},
            ],
        }
    )
    prompts = []

    def structured(schema: type, **_: Any) -> RunnableLambda:
        def respond(prompt: Any) -> Any:
            if schema is IntentDecision:
                return decision()
            prompts.append(prompt.to_messages()[-1].content)
            return draft

        return RunnableLambda(respond)

    llm.with_structured_output.side_effect = structured
    monkeypatch.setattr(agent_service, "get_chat_model", Mock(return_value=llm))
    rag = create_app()
    rag.dependency_overrides[get_settings] = lambda: settings

    async def run() -> Any:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=rag), base_url="http://phase1/"
        ) as client:
            direct = await client.post(
                "/retrieve",
                json={
                    **agent_request.model_dump(
                        mode="json", include=set(RetrieveRequest.model_fields)
                    ),
                },
            )
            assert direct.json() == evidence.model_dump(mode="json")
            retrieve.reset_mock()
            response = await agent_service.orchestrate(
                agent_request, settings, AgentSettings(_env_file=None), client
            )
            return response

    result = asyncio.run(run())
    assert result.status == ("insufficient_evidence" if invalid_quote else draft_status)
    assert result.answer is not None
    assert result.answer.status == result.status
    assert result.answer.as_of == agent_request.as_of
    assert result.answer.retrieval_timings == evidence.timings
    retrieve.assert_awaited_once()
    assert retrieve.await_args is not None
    assert retrieve.await_args.args[0].model_dump() == agent_request.model_dump(
        include=set(RetrieveRequest.model_fields)
    )
    assert len(prompts) == (2 if invalid_quote else 1)
    for prompt in prompts:
        context = json.loads(prompt.split("retrieved_context (JSON):\n", 1)[1])
        assert {
            item["evidence_id"]: (item["retrieved_context"], item["matched_child"])
            for item in context
        } == {
            index: (hit.parent_text, hit.citation.model_dump(mode="json"))
            for index, hit in enumerate(evidence.evidence, 1)
        }
    if result.status == "answered":
        assert [citation.matched_child for citation in result.answer.citations] == [
            hit.citation for hit in evidence.evidence
        ]
    else:
        assert result.answer.citations == []
    assert llm.with_structured_output.call_args_list[0].args[0] is IntentDecision
    assert llm.with_structured_output.call_args_list[1].args[0] is AnswerDraft


@pytest.mark.parametrize(
    "failure,field,number",
    [
        (failure, None, None)
        for failure in (
            "timeout",
            "status",
            "redirect",
            "invalid_json",
            "malformed",
            "invalid_amendment",
            "string_rank",
            "string_timing",
            "wrong_date",
        )
    ]
    + [
        ("nonfinite", field, number)
        for field in ("result_score", "amendment_score", "search_ms", "parent_ms", "total_ms")
        for number in (float("nan"), float("inf"), float("-inf"))
    ],
)
def test_http_retrieval_fails_closed(
    agent_app: Any,
    agent_request: AgentRequest,
    evidence: RetrieveResponse,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    field: str | None,
    number: float | None,
) -> None:
    calls = []
    llm = Mock(spec=BaseChatModel)
    llm.with_structured_output.side_effect = lambda schema, **_: RunnableLambda(
        lambda _: (
            decision()
            if schema is IntentDecision
            else AnswerDraft(status="insufficient_evidence", answer="No answer", evidence=[])
        )
    )
    monkeypatch.setattr(agent_service, "get_chat_model", lambda _: llm)

    def handle(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        assert req.method == "POST"
        assert req.url == "http://rag/prefix/retrieve"
        if failure == "timeout":
            raise httpx.ReadTimeout("private upstream detail", request=req)
        if failure == "status":
            return httpx.Response(503, text="private upstream detail")
        if failure == "redirect":
            return httpx.Response(307, headers={"Location": "http://private/secret"})
        if failure == "invalid_json":
            return httpx.Response(200, text="private upstream detail")
        body = evidence.model_dump(mode="json")
        if failure == "wrong_date":
            body["as_of"] = "2025-01-01"
        if failure == "invalid_amendment":
            del body["amendments"][0]["citation"]["doc_id"]
        if failure == "string_rank":
            body["amendments"][0]["rank"] = "1"
        if failure == "string_timing":
            body["timings"]["total_ms"] = "3"
        if failure == "nonfinite":
            assert field is not None and number is not None
            if field == "result_score":
                body["results"][0]["score"] = number
            elif field == "amendment_score":
                body["amendments"][0]["score"] = number
            else:
                body["timings"][field] = number
            return httpx.Response(
                200, content=json.dumps(body), headers={"content-type": "application/json"}
            )
        return httpx.Response(200, json={} if failure == "malformed" else body)

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle),
            base_url="http://rag/prefix/",
            follow_redirects=False,
        ) as client:
            agent_app.dependency_overrides[agent_api.rag_client] = lambda: client
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=agent_app), base_url="http://agent/"
            ) as api:
                response = await api.post("/agent", json=agent_request.model_dump(mode="json"))
            assert response.status_code == 503
            assert response.json() == {"detail": "Agent service unavailable"}

    asyncio.run(run())
    assert len(calls) == 1
    llm.with_structured_output.assert_called_once_with(
        IntentDecision, method="json_schema", strict=True
    )


@pytest.fixture
def agent_app(settings: Settings) -> Any:
    app = create_agent_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_agent_settings] = lambda: AgentSettings(_env_file=None)
    app.dependency_overrides[agent_api.rag_client] = lambda: Mock(spec=httpx.AsyncClient)
    return app


@pytest.mark.parametrize(
    "payload",
    [
        {"query": " "},
        {"query": "x", "unexpected": True},
        {"query": "x", "as_of": "invalid"},
        {"query": "x", "customer_id": "someone"},
        {"query": "x", "action": "approveLoan"},
        {"query": "x", "top_k": 21},
    ],
)
def test_agent_api_validates_boundary(agent_app: Any, payload: dict) -> None:
    assert TestClient(agent_app).post("/agent", json=payload).status_code == 422


@pytest.mark.parametrize(
    "error", [ValueError("secret"), httpx.ReadTimeout("secret"), TimeoutError()]
)
def test_agent_api_sanitizes_upstream_failures(
    agent_app: Any,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    monkeypatch.setattr(agent_api, "orchestrate", AsyncMock(side_effect=error))
    response = TestClient(agent_app).post("/agent", json={"query": "policy"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Agent service unavailable"}


@pytest.mark.parametrize(
    "choice,fields,status",
    [
        ({}, {"as_of": None}, "needs_clarification"),
        ({}, {"product_id": None}, "needs_clarification"),
        ({"as_of": "2025-01-01"}, {}, "needs_clarification"),
        ({"intent": "forbidden"}, {}, "forbidden"),
        ({"intent": "forbidden", "missing_fields": ["as_of"]}, {"as_of": None}, "forbidden"),
    ],
)
def test_agent_api_terminal_routes_do_not_call_rag(
    agent_app: Any,
    agent_request: AgentRequest,
    monkeypatch: pytest.MonkeyPatch,
    choice: dict,
    fields: dict,
    status: str,
) -> None:
    llm = Mock(spec=BaseChatModel)
    llm.with_structured_output.return_value = RunnableLambda(lambda _: decision(**choice))
    monkeypatch.setattr(agent_service, "get_chat_model", lambda _: llm)
    client = Mock(spec=httpx.AsyncClient)
    agent_app.dependency_overrides[agent_api.rag_client] = lambda: client
    response = TestClient(agent_app).post(
        "/agent", json={**agent_request.model_dump(mode="json"), **fields}
    )
    assert response.status_code == 200
    assert response.json()["status"] == status
    assert response.json()["answer"] is None
    client.post.assert_not_called()
    llm.with_structured_output.assert_called_once_with(
        IntentDecision, method="json_schema", strict=True
    )


def test_rag_client_bounds_http_calls_and_closes() -> None:
    config = AgentSettings(
        _env_file=None, rag_base_url="http://rag/prefix", agent_http_timeout_seconds=2
    )

    async def run() -> None:
        async for client in agent_api.rag_client(config):
            assert str(client.base_url) == "http://rag/prefix/"
            assert client.timeout == httpx.Timeout(2)
            assert client.follow_redirects is False
        assert client.is_closed

    asyncio.run(run())


def test_run_deadline_stops_classification(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    async def slow(_: Any) -> IntentDecision:
        await asyncio.sleep(1)
        return decision()

    llm = Mock(spec=BaseChatModel)
    llm.with_structured_output.return_value = RunnableLambda(slow)
    monkeypatch.setattr(agent_service, "get_chat_model", lambda _: llm)
    with pytest.raises(TimeoutError):
        asyncio.run(
            agent_service.orchestrate(
                AgentRequest(query="policy"),
                settings,
                AgentSettings(_env_file=None, agent_run_timeout_seconds=0.01),
                Mock(spec=httpx.AsyncClient),
            )
        )


def test_original_api_contracts_remain_separate() -> None:
    paths = create_app().openapi()["paths"]
    assert {
        "/retrieve",
        "/answer",
        "/answer/evaluated",
        "/feedback",
        "/health",
        "/metrics",
    } <= paths.keys()
    assert "/agent" not in paths
    assert set(create_agent_app().openapi()["paths"]) == {
        "/agent",
        "/health",
        "/metrics",
        "/agent/submit",
        "/agent/runs",
        "/agent/runs/{run_id}",
        "/agent/runs/{run_id}/approval",
        "/agent/runs/{run_id}/retry",
        "/v2/agent",
        "/v2/agent/runs",
        "/v2/agent/runs/{run_id}",
        "/v2/agent/runs/{run_id}/approval",
        "/v2/agent/runs/{run_id}/retry",
    }


def test_agent_config_and_classifier_schema() -> None:
    with pytest.raises(ValidationError):
        AgentSettings(_env_file=None, rag_base_url="file:///etc/passwd")
    with pytest.raises(ValidationError):
        AgentSettings(_env_file=None, agent_http_timeout_seconds=0)
    assert IntentDecision.model_json_schema()["additionalProperties"] is False
    assert ORCHESTRATOR_PROMPT.input_variables == ["request"]


def test_ten_routing_golden_cases_have_valid_contracts() -> None:
    cases = [
        json.loads(line)
        for line in Path("evaluation/datasets/agent.jsonl").read_text().splitlines()
    ]
    assert len(cases) == 10
    for case in cases:
        AgentRequest.model_validate(case["input"])
        assert case["expected_output"]["intent"] in get_args(
            IntentDecision.model_fields["intent"].annotation
        )
        assert case["context"]


@pytest.mark.parametrize(
    "kind,loan,product",
    [
        ("CALLBACK", None, None),
        ("STATEMENT_COPY", None, None),
        ("RE_KYC_REMINDER", None, None),
        ("INTEREST_CERTIFICATE", "NXL-HL-0010", None),
        ("OPT_IN_CIR_2026_07_PART_PREPAY", "NXL-HL-0010", "NEXA-HL"),
    ],
)
@pytest.mark.parametrize("intent", ["supported_action", "customer_context"])
def test_text_service_details_reach_existing_validator(
    evidence: RetrieveResponse, kind: str, loan: str | None, product: str | None, intent: str
) -> None:
    choice = decision(
        intent=intent,
        product_id=product,
        as_of="2026-10-01",
        customer_id="NXC-0010",
        action="createServiceRequest",
        request_type=kind,
        loan_account_id=loan,
    )
    retrieve = AsyncMock(return_value=evidence)
    validate = AsyncMock(return_value=ValidationResult(decision="blocked", reason="Test decision"))
    answer = AsyncMock()
    graph = build_agent_graph(
        RunnableLambda(lambda _: choice),
        RunnableLambda(retrieve),
        answer,
        validator=validate,
        access=ValidationAccess(customer_ids={"NXC-0010"}),
    )
    request = AgentRequest(query=f"Prepare {kind} for NXC-0010 as of 2026-10-01; loan {loan}.")
    state = asyncio.run(graph.ainvoke({"request": request}))
    assert state["response"].status == "blocked"
    resolved = state["request"]
    assert resolved.action_details.request_type == kind
    assert resolved.action_details.loan_account_id == loan
    assert resolved.action_details.notes is None
    assert resolved.customer_id == "NXC-0010"
    validate.assert_awaited_once_with(resolved, evidence)
    retrieve.assert_awaited_once()
    assert "NXC-0010" not in retrieve.call_args.args[0].query
    answer.assert_not_awaited()


@pytest.mark.parametrize(
    "explicit,extracted,expected_loan,missing",
    [
        ({"request_type": "CALLBACK"}, {}, None, []),
        (
            {"request_type": "CALLBACK", "notes": "Call in the afternoon"},
            {"request_type": "CALLBACK", "loan_account_id": "NXL-HL-0010"},
            "NXL-HL-0010",
            [],
        ),
        (
            {"request_type": "CALLBACK"},
            {"request_type": "STATEMENT_COPY"},
            None,
            ["action_details"],
        ),
        (
            {"request_type": "CALLBACK", "loan_account_id": "NXL-HL-0010"},
            {"loan_account_id": "NXL-HL-0023"},
            "NXL-HL-0010",
            ["action_details"],
        ),
    ],
)
def test_service_text_merges_without_overwriting_explicit_details(
    explicit: dict, extracted: dict, expected_loan: str | None, missing: list
) -> None:
    retrieve, answer = AsyncMock(), AsyncMock()
    graph = build_agent_graph(
        RunnableLambda(lambda _: decision(intent="supported_action", **extracted)),
        RunnableLambda(retrieve),
        answer,
    )
    request = AgentRequest(
        query="Prepare the requested service.",
        customer_id="NXC-0010",
        action="createServiceRequest",
        action_details=explicit,
    )
    state = asyncio.run(graph.ainvoke({"request": request}))
    details = state["request"].action_details
    assert details.request_type == explicit["request_type"]
    assert details.loan_account_id == expected_loan
    assert details.notes == explicit.get("notes")
    assert state["response"].missing_fields == missing
    assert state["response"].suggestions if missing else not state["response"].suggestions
    retrieve.assert_not_awaited()
    answer.assert_not_awaited()


@pytest.mark.parametrize(
    "fields,extracted,missing,guide",
    [
        ({}, {}, ["customer_id", "action"], "synthetic customer ID"),
        (
            {"customer_id": "NXC-0010", "action": "createServiceRequest"},
            {},
            ["as_of", "action_details"],
            "statement copy",
        ),
        (
            {"customer_id": "NXC-0010", "action": "createServiceRequest", "as_of": "2026-10-01"},
            {"request_type": "INTEREST_CERTIFICATE"},
            ["action_details"],
            "loan account ID",
        ),
        (
            {"customer_id": "NXC-0010", "action": "createServiceRequest", "as_of": "2026-10-01"},
            {"request_type": "OPT_IN_CIR_2026_07_PART_PREPAY", "loan_account_id": "NXL-HL-0010"},
            ["product_id"],
            "NEXA-HL",
        ),
    ],
)
def test_service_clarification_suggests_options_without_downstream_calls(
    fields: dict, extracted: dict, missing: list, guide: str
) -> None:
    retrieve, answer, validate = AsyncMock(), AsyncMock(), AsyncMock()
    graph = build_agent_graph(
        RunnableLambda(lambda _: decision(intent="supported_action", **extracted)),
        RunnableLambda(retrieve),
        answer,
        validator=validate,
        access=ValidationAccess(customer_ids={"NXC-0010"}),
    )
    state = asyncio.run(
        graph.ainvoke({"request": AgentRequest(query="Prepare a request", **fields)})
    )
    response = state["response"]
    assert response.status == "needs_clarification"
    assert response.missing_fields == missing
    assert guide in " ".join(response.suggestions)
    assert len(response.suggestions) == len(missing)
    retrieve.assert_not_awaited()
    validate.assert_not_awaited()
    answer.assert_not_awaited()


def test_vague_request_suggests_supported_services() -> None:
    graph = build_agent_graph(
        RunnableLambda(lambda _: decision(intent="missing_information")),
        RunnableLambda(AsyncMock()),
        AsyncMock(),
    )
    response = asyncio.run(graph.ainvoke({"request": AgentRequest(query="Help me")}))["response"]
    assert response.missing_fields == ["question"]
    assert "one supported service at a time" in response.suggestions[0]


CHAT_CASES = [
    json.loads(line)
    for line in Path("evaluation/datasets/agent_chat.jsonl").read_text().splitlines()
]


@pytest.mark.parametrize("case", CHAT_CASES, ids=[case["id"] for case in CHAT_CASES])
def test_unified_chat_golden_routing_cases(case: dict, evidence: RetrieveResponse) -> None:
    from app.models.agent import AgentResponse
    from app.models.validation import ServiceRequestProposal, ValidationEvidence

    choice = IntentDecision.model_validate(case["context"]["decision"])
    retrieve = AsyncMock(return_value=evidence)
    answer = AsyncMock(
        return_value=AnswerResponse(
            as_of=evidence.as_of,
            status="answered",
            answer="Fixture policy response",
            citations=[],
            retrieval_timings=evidence.timings,
            generation_ms=0,
        )
    )

    async def validate(request, sources):
        return ValidationResult(
            decision="allowed",
            reason="Fixture validation",
            evidence=[
                ValidationEvidence(
                    citation=evidence.results[0].citation, quote=evidence.results[0].text
                )
            ],
            case_proposal=request.case_details,
            proposal=None
            if request.case_details is not None
            else ServiceRequestProposal(
                customer_id=request.customer_id, **request.action_details.model_dump()
            ),
        )

    customer_read = AsyncMock(
        return_value=AgentResponse(
            intent="customer_context", status="answered", message="Fixture transactions"
        )
    )
    graph = build_agent_graph(
        RunnableLambda(lambda _: choice),
        RunnableLambda(retrieve),
        answer,
        validator=validate,
        customer_reader=customer_read,
        access=ValidationAccess(customer_ids={"NXC-0001"}),
    )
    state = asyncio.run(graph.ainvoke({"request": AgentRequest.model_validate(case["input"])}))
    response = state["response"]
    assert response.status == case["expected_output"]["status"]
    assert response.missing_fields == case["expected_output"]["missing_fields"]
    if response.status in ("needs_clarification", "forbidden"):
        retrieve.assert_not_awaited()
        answer.assert_not_awaited()
        customer_read.assert_not_awaited()
    if response.status == "needs_clarification":
        assert response.suggestions
    if case["id"] == "transactions-read":
        customer_read.assert_awaited_once()
        retrieve.assert_not_awaited()


@pytest.mark.parametrize("mixed", [False, True])
def test_case_date_and_mixed_requests_clarify_before_any_tool(mixed: bool) -> None:
    from app.models.validation import CaseBody

    case = CaseBody(
        customer_id="NXC-0001",
        category="LOAN_ENQUIRY",
        priority="LOW",
        summary="Home-loan documents enquiry",
    )
    choice = decision(
        intent="supported_action", customer_id="NXC-0001", action="createCase", case_details=case
    )
    request = AgentRequest(
        query="Prepare the case",
        as_of="2026-11-15" if mixed else None,
        action_details={"request_type": "CALLBACK"} if mixed else None,
    )
    retrieve, answer, validate = AsyncMock(), AsyncMock(), AsyncMock()
    graph = build_agent_graph(
        RunnableLambda(lambda _: choice),
        RunnableLambda(retrieve),
        answer,
        validator=validate,
        access=ValidationAccess(customer_ids={"NXC-0001"}),
    )
    response = asyncio.run(graph.ainvoke({"request": request}))["response"]
    assert response.status == "needs_clarification"
    assert ("question" if mixed else "as_of") in response.missing_fields
    retrieve.assert_not_awaited()
    answer.assert_not_awaited()
    validate.assert_not_awaited()
