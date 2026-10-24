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
                    **agent_request.model_dump(mode="json", exclude={"customer_id", "action"}),
                },
            )
            assert direct.json() == evidence.model_dump(mode="json")
            retrieve.reset_mock()
            response = await agent_service.orchestrate(
                agent_request, settings, AgentSettings(), client
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
        exclude={"customer_id", "action"}
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
    assert set(create_agent_app().openapi()["paths"]) == {"/agent", "/health", "/metrics"}


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
