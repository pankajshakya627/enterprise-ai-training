from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pdfplumber
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import RunnableLambda

from app.agent_main import create_agent_app
from app.api import agent as agent_api
from app.api import retrieval as retrieval_api
from app.core.config import AgentSettings, Settings, get_agent_settings, get_settings
from app.database.agent_runs import MongoRunStore
from app.main import create_app
from app.models.agent import IntentDecision
from app.models.answer import AnswerDraft
from app.models.retrieval import RetrieveResponse
from app.services import agent as agent_service

TYPES = [
    "STATEMENT_COPY",
    "CALLBACK",
    "RE_KYC_REMINDER",
    "INTEREST_CERTIFICATE",
    "OPT_IN_CIR_2026_07_PART_PREPAY",
]
OWNER = {"Authorization": "Bearer " + "o" * 32}
REVIEWER = {"Authorization": "Bearer " + "r" * 32}
WORKFLOW_CASES = [
    json.loads(line)
    for line in Path("evaluation/datasets/agent_workflows.jsonl").read_text().splitlines()
]


@pytest.fixture(scope="module")
def published_evidence() -> dict[str, RetrieveResponse]:
    result = {}
    for name, doc, filename, effective_date in (
        (
            "operations",
            "POL-OPS-001",
            "POL-OPS-001_Customer_Due_Diligence_and_Advisor_Scope_Policy_v1.0.pdf",
            "2025-01-01",
        ),
        (
            "circular",
            "CIR-2026-07",
            "CIR-2026-07_Circular_NexaHome_Fee_and_PartPrepayment.pdf",
            "2026-07-01",
        ),
    ):
        with pdfplumber.open(Path("nexa_synthetic_data/policies") / filename) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        hit = {
            "rank": 1,
            "score": 0.9,
            "text": text,
            "parent_text": text,
            "citation": {
                "doc_id": doc,
                "title": name,
                "policy_version": "1.0",
                "clause": None,
                "effective_date": effective_date,
            },
        }
        result[name] = RetrieveResponse.model_validate(
            {
                "as_of": "2026-10-01",
                "results": [hit] if name == "operations" else [],
                "amendments": [hit] if name == "circular" else [],
                "timings": {"search_ms": 1, "parent_ms": 1, "total_ms": 2},
            }
        )
    return result


@pytest.fixture(params=["http", "mcp", "mcp-http"])
def world(
    request: pytest.FixtureRequest,
    run_store: MongoRunStore,
    workflow_settings: Settings,
    published_evidence: dict[str, RetrieveResponse],
    monkeypatch: pytest.MonkeyPatch,
):
    name = "workflow_bank_" + uuid4().hex
    spec = importlib.util.spec_from_file_location(
        name, "nexa_synthetic_data/api/mock_server/server.py"
    )
    assert spec is not None and spec.loader is not None
    bank_module = importlib.util.module_from_spec(spec)
    sys.modules[name] = bank_module
    spec.loader.exec_module(bank_module)
    context = SimpleNamespace(
        store=run_store,
        bank=bank_module,
        intent="supported_action",
        retrievals=[],
        calls=[],
        fault=None,
        evidence={key: value.model_copy(deep=True) for key, value in published_evidence.items()},
        bank_disabled=False,
        post_entered=None,
        post_release=None,
        draft=None,
        classification=None,
    )
    config = AgentSettings(
        _env_file=None,
        banking_api_token="synthetic-bank",
        banking_transport="http" if request.param == "http" else "mcp",
        banking_mcp_transport="http" if request.param == "mcp-http" else "stdio",
        banking_mcp_token="h" * 48,
        banking_mcp_signing_key="s" * 48,
        agent_principals=[
            {"principal_id": "owner", "token": "o" * 32, "customer_ids": ["NXC-0010"]},
            {
                "principal_id": "reviewer",
                "token": "r" * 32,
                "customer_ids": ["NXC-0010"],
                "can_approve": True,
            },
            {"principal_id": "other", "token": "x" * 32, "customer_ids": ["NXC-0010"]},
            {
                "principal_id": "foreign",
                "token": "f" * 32,
                "customer_ids": ["NXC-0023"],
                "can_approve": True,
            },
        ],
    )
    context.config = config
    llm = Mock(spec=BaseChatModel)
    classify = Mock()

    def output(schema, **kwargs):
        def respond(value):
            if schema is IntentDecision:
                classify()
                return context.classification or IntentDecision(
                    intent=context.intent,
                    product_id=None,
                    as_of=None,
                    customer_id=None,
                    action=None,
                    product_required=False,
                    missing_fields=[],
                )
            return context.draft or AnswerDraft(
                status="insufficient_evidence", answer="Not enough evidence", evidence=[]
            )

        return RunnableLambda(respond)

    llm.with_structured_output.side_effect = output
    monkeypatch.setattr(agent_service, "get_chat_model", lambda _: llm)
    context.classify = classify

    async def retrieve(request, settings):
        context.retrievals.append(request)
        item = context.evidence[
            "circular" if request.query.startswith("CIR-2026-07") else "operations"
        ]
        return item.model_copy(deep=True)

    monkeypatch.setattr(retrieval_api, "retrieve_policy", retrieve)

    async def capture(request):
        context.calls.append(request)

    @asynccontextmanager
    async def clients():
        rag_app = create_app()
        rag_app.dependency_overrides[get_settings] = lambda: workflow_settings
        bank_transport = httpx.ASGITransport(app=bank_module.app)

        async def bank_response(request):
            if request.method == "POST" and context.post_entered is not None:
                context.post_entered.set()
                await context.post_release.wait()
            response = await bank_transport.handle_async_request(request)
            await response.aread()
            if request.method == "POST" and context.fault == "receipt":
                return httpx.Response(201, json={"private": "bad receipt"})
            return response

        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=rag_app), base_url="http://rag/"
            ) as rag,
            httpx.AsyncClient(
                transport=httpx.MockTransport(bank_response),
                base_url="http://bank/",
                headers={"Authorization": "Bearer synthetic-bank"},
                event_hooks={"request": [capture]},
            ) as bank,
        ):
            lifecycle_task = None
            lifecycle_stop = asyncio.Event()
            if request.param in {"mcp", "mcp-http"}:
                from app.mcp.banking_server import build_banking_server
                from app.models.validation import ValidationAccess
                from app.services import banking_mcp

                def local_server(**kwargs):
                    env = kwargs["env"]
                    upstream.headers["Authorization"] = "Bearer " + env["BANKING_API_TOKEN"]
                    fault = context.bank_client.headers.get("X-Mock-Fault")
                    if fault is not None:
                        upstream.headers["X-Mock-Fault"] = fault
                    else:
                        upstream.headers.pop("X-Mock-Fault", None)
                    return build_banking_server(
                        upstream,
                        ValidationAccess(customer_ids=json.loads(env["BANKING_MCP_CUSTOMER_IDS"])),
                        env["BANKING_MCP_WRITE_GRANT"] or None,
                    )

                monkeypatch.setattr(banking_mcp, "StdioTransport", local_server)
                # This test scope is the owner/reviewer overlap; API auth is still exercised.
                wrapped = httpx.AsyncClient(
                    transport=banking_mcp.BankingMCPTransport(
                        config,
                        ValidationAccess(customer_ids=config.agent_principals[0].customer_ids),
                    ),
                    base_url=bank.base_url,
                    headers=bank.headers,
                )
                upstream = bank
                bank = wrapped
                if request.param == "mcp-http":
                    import httpx2
                    from starlette.middleware import Middleware

                    from app.mcp.banking_http import BankingHTTPAuth, request_session

                    mcp_server = build_banking_server(
                        upstream,
                        ValidationAccess(customer_ids=frozenset()),
                        session_provider=request_session,
                    )
                    mcp_app = mcp_server.http_app(
                        path="/mcp",
                        stateless_http=True,
                        json_response=True,
                        middleware=[Middleware(BankingHTTPAuth, settings=config)],
                    )
                    lifecycle_ready = asyncio.Event()

                    async def run_mcp_lifespan():
                        async with mcp_app.router.lifespan_context(mcp_app):
                            lifecycle_ready.set()
                            await lifecycle_stop.wait()

                    lifecycle_task = asyncio.create_task(run_mcp_lifespan())
                    await lifecycle_ready.wait()

                    def http_factory(**kwargs):
                        fault = context.bank_client.headers.get("X-Mock-Fault")
                        if fault is not None:
                            upstream.headers["X-Mock-Fault"] = fault
                        else:
                            upstream.headers.pop("X-Mock-Fault", None)
                        return httpx2.AsyncClient(
                            transport=httpx2.ASGITransport(app=mcp_app), **kwargs
                        )

                    monkeypatch.setattr(banking_mcp, "mcp_http_client", http_factory)
            app = create_agent_app()
            app.dependency_overrides[get_settings] = lambda: workflow_settings
            app.dependency_overrides[get_agent_settings] = lambda: config
            app.dependency_overrides[agent_api.rag_client] = lambda: rag
            app.dependency_overrides[agent_api.banking_client] = lambda: (
                None if context.bank_disabled else bank
            )
            context.bank_client, context.app = bank, app
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://agent/"
            ) as caller:
                try:
                    yield caller
                finally:
                    if request.param in {"mcp", "mcp-http"}:
                        await bank.aclose()
                    if lifecycle_task is not None:
                        lifecycle_stop.set()
                        await lifecycle_task

    context.clients = clients
    yield context
    sys.modules.pop(name)


def payload(request_type: str = "CALLBACK") -> dict:
    details = {"request_type": request_type}
    body = {
        "query": "Prepare this service request.",
        "as_of": "2026-10-01",
        "customer_id": "NXC-0010",
        "action": "createServiceRequest",
        "action_details": details,
    }
    if request_type in ("INTEREST_CERTIFICATE", "OPT_IN_CIR_2026_07_PART_PREPAY"):
        details["loan_account_id"] = "NXL-HL-0010"
        body["product_id"] = "NEXA-HL"
    return body


@pytest.mark.parametrize("case", WORKFLOW_CASES, ids=[case["id"] for case in WORKFLOW_CASES])
def test_full_http_graph_approval_and_action(world, case: dict) -> None:
    request_type, decision = (
        case["input"]["action_details"]["request_type"],
        case["context"]["approval"],
    )

    async def run() -> None:
        async with world.clients() as caller:
            start = await caller.post("/agent/runs", headers=OWNER, json=case["input"])
            assert start.status_code == 201, start.text
            pending = start.json()
            assert pending["status"] == "pending_approval"
            assert len(world.retrievals) == 1
            assert world.bank.COUNTERS["sr"] == 0
            assert pending["response"]["validation"]["requires_approval"] is True
            assert pending["response"]["validation"]["evidence"][0]["quote"]
            run_id = pending["run_id"]
            read = await caller.get(f"/agent/runs/{run_id}", headers=OWNER)
            assert read.json() == pending
            result = await caller.post(
                f"/agent/runs/{run_id}/approval",
                headers=REVIEWER,
                json={"decision": decision, "proposal_hash": pending["proposal_hash"]},
            )
            assert result.status_code == 200, result.text
            assert result.json()["status"] == case["expected_output"]["status"]
            assert world.bank.COUNTERS["sr"] == case["expected_output"]["banking_writes"]
            assert len(world.retrievals) == (2 if decision == "approve" else 1)
            world.classify.assert_called_once()
            if decision == "approve":
                receipt = result.json()["response"]["submission"]["service_request"]
                assert receipt["request_type"] == request_type
                assert receipt["status"] == "RECEIVED"
                replay = await caller.post(f"/agent/runs/{run_id}/retry", headers=OWNER)
                assert replay.json() == result.json()
                assert world.bank.COUNTERS["sr"] == 1
            assert world.store.checkpointer.checkpoint_collection.count_documents({}) > 0
            record = await world.store.get(run_id)
            assert record["approval"]["reviewer_id"] == "reviewer"
            assert record["status"] == result.json()["status"]
            repeated = await caller.post(
                f"/agent/runs/{run_id}/approval",
                headers=REVIEWER,
                json={"decision": decision, "proposal_hash": pending["proposal_hash"]},
            )
            assert repeated.status_code == 409

    asyncio.run(run())


@pytest.mark.parametrize("request_type", TYPES)
@pytest.mark.parametrize("fault", ["503-once", "commit-then-timeout", "receipt"])
def test_uncertain_submission_retries_same_persisted_operation(
    world, request_type: str, fault: str
) -> None:

    async def run() -> None:
        async with world.clients() as caller:
            pending = (
                await caller.post("/agent/runs", headers=OWNER, json=payload(request_type))
            ).json()
            if fault == "receipt":
                world.fault = fault
            else:
                world.bank_client.headers["X-Mock-Fault"] = fault
            first = await caller.post(
                f"/agent/runs/{pending['run_id']}/approval",
                headers=REVIEWER,
                json={"decision": "approve", "proposal_hash": pending["proposal_hash"]},
            )
            assert first.status_code == 503
            assert "unconfirmed" in first.text
            assert "private" not in first.text
            assert world.bank.COUNTERS["sr"] == (0 if fault == "503-once" else 1)
            status = await caller.get(f"/agent/runs/{pending['run_id']}", headers=OWNER)
            assert status.json()["status"] == "unconfirmed"
            world.fault = None
            # A new HTTP request reconstructs the service, graph and MongoDB saver.
            retry = await caller.post(f"/agent/runs/{pending['run_id']}/retry", headers=OWNER)
            assert retry.status_code == 200, retry.text
            assert retry.json()["status"] == "submitted"
            assert world.bank.COUNTERS["sr"] == 1
            assert len(world.retrievals) == 2
            posts = [request for request in world.calls if request.method == "POST"]
            assert len(posts) == 2
            assert posts[0].content == posts[1].content
            assert posts[0].headers["Idempotency-Key"] == posts[1].headers["Idempotency-Key"]
            assert posts[0].headers["Authorization"] == posts[1].headers["Authorization"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure",
    [
        "token",
        "foreign_customer",
        "forbidden",
        "missing_date",
        "missing_loan",
        "bad_type",
        "no_bank",
        "policy_date",
        "policy_missing",
    ],
)
def test_early_stops_and_invalid_evidence_never_write(world, failure: str) -> None:

    async def run() -> None:
        body = payload("INTEREST_CERTIFICATE")
        headers = OWNER
        if failure == "token":
            headers = {}
        if failure == "foreign_customer":
            body["customer_id"] = "NXC-0023"
        if failure == "forbidden":
            world.intent = "forbidden"
        if failure == "missing_date":
            body["as_of"] = None
        if failure == "missing_loan":
            body["action_details"].pop("loan_account_id")
        if failure == "bad_type":
            body["action_details"]["request_type"] = "GRANT_FEE_WAIVER"
        if failure == "no_bank":
            world.bank_disabled = True
        if failure == "policy_date":
            world.evidence["operations"].as_of = date(2026, 9, 1)
        if failure == "policy_missing":
            world.evidence["operations"].results = []
        async with world.clients() as caller:
            result = await caller.post("/agent/runs", headers=headers, json=body)
        assert world.bank.COUNTERS["sr"] == 0
        assert not [request for request in world.calls if request.method == "POST"]
        if failure in ("token", "foreign_customer", "bad_type"):
            assert result.status_code == (422 if failure == "bad_type" else 403)
        elif failure == "policy_date":
            assert result.status_code == 503
        else:
            assert result.status_code == 201
            assert (
                result.json()["status"]
                == {
                    "forbidden": "forbidden",
                    "missing_date": "needs_clarification",
                    "missing_loan": "needs_clarification",
                    "no_bank": "unavailable",
                    "policy_missing": "insufficient_evidence",
                }[failure]
            )
        assert len(world.retrievals) == (1 if failure in ("policy_date", "policy_missing") else 0)
        assert len(world.calls) == 0

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure",
    [
        "owner",
        "self_reviewer",
        "other_owner",
        "foreign_reviewer",
        "stale",
        "expired",
        "extra_fields",
        "invalid_hash",
        "revoked_owner",
        "changed_policy",
        "changed_facts",
        "bank_identity",
        "bank_transport",
    ],
)
def test_approval_is_owner_scoped_exact_current_and_non_bypassable(world, failure: str) -> None:

    async def run() -> None:
        kind = "OPT_IN_CIR_2026_07_PART_PREPAY" if failure == "changed_facts" else "CALLBACK"
        async with world.clients() as caller:
            pending = (await caller.post("/agent/runs", headers=OWNER, json=payload(kind))).json()
            headers = REVIEWER
            body = {"decision": "approve", "proposal_hash": pending["proposal_hash"]}
            if failure == "owner":
                headers = OWNER
            if failure == "self_reviewer":
                world.config.agent_principals[0] = world.config.agent_principals[0].model_copy(
                    update={"can_approve": True}
                )
                headers = OWNER
            if failure == "other_owner":
                headers = {"Authorization": "Bearer " + "x" * 32}
            if failure == "foreign_reviewer":
                headers = {"Authorization": "Bearer " + "f" * 32}
            if failure == "stale":
                body["proposal_hash"] = "0" * 64
            if failure == "expired":
                world.store.collection.update_one(
                    {"_id": pending["run_id"]},
                    {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}},
                )
            if failure == "extra_fields":
                body["proposal"] = {"request_type": "OTHER"}
            if failure == "invalid_hash":
                body["proposal_hash"] = "not-a-digest"
            if failure == "revoked_owner":
                world.config.agent_principals[0] = world.config.agent_principals[0].model_copy(
                    update={"customer_ids": frozenset({"NXC-0023"})}
                )
            if failure == "changed_policy":
                world.evidence["operations"].results[
                    0
                ].parent_text += "\nA changed policy annotation."
            if failure == "changed_facts":
                world.bank.OPTED_IN.add("NXC-0010")
            if failure == "bank_transport":
                world.config.banking_transport = (
                    "http" if world.config.banking_transport == "mcp" else "mcp"
                )
            if failure == "bank_identity":
                world.bank_client.headers["Authorization"] = "Bearer changed-bank-caller"
            result = await caller.post(
                f"/agent/runs/{pending['run_id']}/approval", headers=headers, json=body
            )
            expected = (
                200
                if failure in ("changed_policy", "changed_facts")
                else 422
                if failure in ("extra_fields", "invalid_hash")
                else 404
                if failure == "foreign_reviewer"
                else 409
                if failure in ("stale", "expired", "bank_identity", "bank_transport")
                else 403
            )
            assert result.status_code == expected, result.text
            if expected == 200:
                assert result.json()["status"] == "blocked"
            assert world.bank.COUNTERS["sr"] == 0
            assert not [request for request in world.calls if request.method == "POST"]
            if failure == "expired":
                assert (await caller.get(f"/agent/runs/{pending['run_id']}", headers=OWNER)).json()[
                    "status"
                ] == "expired"

    asyncio.run(run())


def test_concurrent_approvals_serialize_the_bank_write(world) -> None:

    async def run() -> None:
        world.post_entered, world.post_release = asyncio.Event(), asyncio.Event()
        async with world.clients() as caller:
            pending = (await caller.post("/agent/runs", headers=OWNER, json=payload())).json()
            path = f"/agent/runs/{pending['run_id']}/approval"
            body = {"decision": "approve", "proposal_hash": pending["proposal_hash"]}
            first = asyncio.create_task(caller.post(path, headers=REVIEWER, json=body))
            await asyncio.wait_for(world.post_entered.wait(), timeout=5)
            second = await caller.post(path, headers=REVIEWER, json=body)
            assert second.status_code == 409
            world.post_release.set()
            assert (await first).json()["status"] == "submitted"
            assert world.bank.COUNTERS["sr"] == 1

    asyncio.run(run())


def test_foreign_reads_missing_runs_and_unapproved_retry_are_rejected(world) -> None:

    async def run() -> None:
        async with world.clients() as caller:
            pending = (await caller.post("/agent/runs", headers=OWNER, json=payload())).json()
            for path, headers in (
                (pending["run_id"], {"Authorization": "Bearer " + "x" * 32}),
                (str(uuid4()), OWNER),
            ):
                assert (await caller.get(f"/agent/runs/{path}", headers=headers)).status_code == 404
            assert (
                await caller.post(f"/agent/runs/{pending['run_id']}/retry", headers=OWNER)
            ).status_code == 409
            assert world.bank.COUNTERS["sr"] == 0

    asyncio.run(run())


def test_uncertain_retry_after_bank_retention_window_requires_reconciliation(world) -> None:

    async def run() -> None:
        async with world.clients() as caller:
            pending = (await caller.post("/agent/runs", headers=OWNER, json=payload())).json()
            world.bank_client.headers["X-Mock-Fault"] = "commit-then-timeout"
            response = await caller.post(
                f"/agent/runs/{pending['run_id']}/approval",
                headers=REVIEWER,
                json={"decision": "approve", "proposal_hash": pending["proposal_hash"]},
            )
            assert response.status_code == 503
            world.store.collection.update_one(
                {"_id": pending["run_id"]},
                {"$set": {"first_attempt_at": datetime.now(UTC) - timedelta(hours=23)}},
            )
            retry = await caller.post(f"/agent/runs/{pending['run_id']}/retry", headers=OWNER)
            assert retry.status_code == 409
            assert "reconcile" in retry.text
            assert world.bank.COUNTERS["sr"] == 1

    asyncio.run(run())


@pytest.mark.parametrize("request_type", TYPES)
def test_direct_submission_is_disabled_by_default(world, request_type: str) -> None:

    async def run() -> None:
        async with world.clients() as caller:
            response = await caller.post(
                "/agent/submit",
                headers={**OWNER, "Idempotency-Key": "compatibility-test-key"},
                json=payload(request_type),
            )
            assert response.status_code == 403
            assert not world.retrievals and not world.calls
            assert world.bank.COUNTERS["sr"] == 0

    asyncio.run(run())


def test_policy_only_run_keeps_one_retrieval_and_never_enters_action(world) -> None:

    async def run() -> None:
        world.intent = "policy_question"
        async with world.clients() as caller:
            response = await caller.post(
                "/agent/runs",
                headers=OWNER,
                json={"query": "Explain the permitted advisory scope", "as_of": "2026-10-01"},
            )
            assert response.status_code == 201
            assert response.json()["status"] == "insufficient_evidence"
            assert response.json()["proposal_hash"] is None
            assert len(world.retrievals) == 1
            assert not world.calls
            foreign = await caller.get(f"/agent/runs/{response.json()['run_id']}", headers=REVIEWER)
            assert foreign.status_code == 404

    asyncio.run(run())


def test_checkpoint_preserves_complete_amendments_and_nested_citations(world) -> None:

    async def run() -> None:
        amendment = world.evidence["circular"].amendments[0].model_copy(deep=True)
        world.evidence["operations"].amendments.append(amendment)
        async with world.clients() as caller:
            response = await caller.post("/agent/runs", headers=OWNER, json=payload())
            assert response.status_code == 201
            record = await world.store.checkpointer.aget_tuple(
                {"configurable": {"thread_id": "NexaBank:" + response.json()["run_id"]}}
            )
            assert record is not None
            retained = record.checkpoint["channel_values"]["retrieved"]
            assert retained == world.evidence["operations"]
            assert retained.amendments[0].citation == amendment.citation
            assert retained.amendments[0].parent_text == amendment.parent_text

    asyncio.run(run())


def test_storage_errors_return_safe_api_messages(world, monkeypatch: pytest.MonkeyPatch) -> None:
    from pymongo.errors import ConnectionFailure

    from app.services.agent_runs import AgentRunService

    async def unavailable(*args, **kwargs):
        raise ConnectionFailure("private mongodb credentials")

    monkeypatch.setattr(AgentRunService, "create", unavailable)

    async def run() -> None:
        async with world.clients() as caller:
            response = await caller.post("/agent/runs", headers=OWNER, json=payload())
            assert response.status_code == 503
            assert "private" not in response.text
            assert not world.calls and not world.retrievals

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["approval", "start", "attempt", "publish"])
def test_lost_run_lease_never_starts_an_unowned_write(
    world, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    original = MongoRunStore.update

    async def lose_lease(self, run_id, lease, **changes):
        failed = (
            (stage == "approval" and "approval" in changes)
            or (stage == "start" and changes.get("status") == "running")
            or (stage == "attempt" and "first_attempt_at" in changes)
            or (stage == "publish" and "customer_id" in changes)
        )
        return False if failed else await original(self, run_id, lease, **changes)

    async def run() -> None:
        async with world.clients() as caller:
            pending = (await caller.post("/agent/runs", headers=OWNER, json=payload())).json()
            monkeypatch.setattr(MongoRunStore, "update", lose_lease)
            result = await caller.post(
                f"/agent/runs/{pending['run_id']}/approval",
                headers=REVIEWER,
                json={"decision": "approve", "proposal_hash": pending["proposal_hash"]},
            )
            assert result.status_code == 409
            assert "lease" in result.text
            assert world.bank.COUNTERS["sr"] == (1 if stage == "publish" else 0)
            if stage == "publish":
                # A committed graph checkpoint still publishes the confirmed receipt after a ledger failure.
                assert (await caller.get(f"/agent/runs/{pending['run_id']}", headers=OWNER)).json()[
                    "status"
                ] == "submitted"

    asyncio.run(run())


def test_pending_run_survives_application_and_real_process_restart(
    world, workflow_settings: Settings
) -> None:
    import os
    import subprocess

    async def run() -> None:
        async with world.clients() as caller:
            first = await caller.post("/agent/runs", headers=OWNER, json=payload())
            assert first.status_code == 201
            pending = first.json()
        uri = os.environ.get("NEXA_TEST_MONGODB_URI")
        if uri is not None:
            script = """
import asyncio, json, sys
from app.core.config import Settings
from app.database.agent_runs import MongoRunStore
settings = Settings(_env_file=None, mongodb_uri=sys.argv[1], mongodb_db=sys.argv[2],
                    mongodb_collection='unused', openai_api_key='unused', institution='NexaBank',
                    jurisdiction='IN', confidentiality_level='internal')
store = MongoRunStore(settings)
async def load():
    item = await store.checkpointer.aget_tuple({'configurable': {'thread_id': 'NexaBank:' + sys.argv[3]}})
    assert item is not None
    return item.checkpoint['channel_values']['proposal_hash']
print(json.dumps({'proposal_hash': asyncio.run(load())}))
"""
            worker = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-c",
                    script,
                    uri,
                    workflow_settings.mongodb_db,
                    pending["run_id"],
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert json.loads(worker.stdout)["proposal_hash"] == pending["proposal_hash"]
        async with world.clients() as restarted:
            status = await restarted.get(f"/agent/runs/{pending['run_id']}", headers=OWNER)
            assert status.json() == pending
            result = await restarted.post(
                f"/agent/runs/{pending['run_id']}/approval",
                headers=REVIEWER,
                json={"decision": "approve", "proposal_hash": pending["proposal_hash"]},
            )
            assert result.json()["status"] == "submitted"
            assert world.bank.COUNTERS["sr"] == 1

    asyncio.run(run())


def test_deadline_includes_checkpoint_preparation_before_graph_resume(
    world, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = MongoRunStore.update

    async def slow_approval(self, run_id, lease, **changes):
        if "approval" in changes:
            await asyncio.sleep(0.5)
        return await original(self, run_id, lease, **changes)

    async def run() -> None:
        async with world.clients() as caller:
            pending = (await caller.post("/agent/runs", headers=OWNER, json=payload())).json()
            world.config.agent_run_timeout_seconds = 0.05
            monkeypatch.setattr(MongoRunStore, "update", slow_approval)
            response = await caller.post(
                f"/agent/runs/{pending['run_id']}/approval",
                headers=REVIEWER,
                json={"decision": "approve", "proposal_hash": pending["proposal_hash"]},
            )
            assert response.status_code == 503
            assert world.bank.COUNTERS["sr"] == 0
            assert len(world.retrievals) == 1
            record = await world.store.get(pending["run_id"])
            assert record["status"] == "pending_approval"
            assert "lease_id" not in record

    asyncio.run(run())


def test_policy_answer_and_exact_citation_survive_the_persistent_run(world) -> None:

    async def run() -> None:
        world.intent = "policy_question"
        quote = world.evidence["operations"].results[0].parent_text.splitlines()[0]
        world.draft = AnswerDraft(
            status="answered", answer=quote + " [1]", evidence=[{"evidence_id": 1, "quote": quote}]
        )
        async with world.clients() as caller:
            response = await caller.post(
                "/agent/runs",
                headers=OWNER,
                json={"query": "Name the source document", "as_of": "2026-10-01"},
            )
            assert response.status_code == 201
            assert response.json()["status"] == "answered"
            assert response.json()["response"]["answer"]["citations"][0]["quote"] == quote
            restored = await caller.get(f"/agent/runs/{response.json()['run_id']}", headers=OWNER)
            assert restored.json() == response.json()
            assert len(world.retrievals) == 1
            assert not world.calls

    asyncio.run(run())


def test_validation_only_api_preserves_its_existing_response_fields(world) -> None:

    async def run() -> None:
        async with world.clients() as caller:
            response = await caller.post("/agent", headers=OWNER, json=payload())
            assert response.status_code == 200
            assert response.json()["status"] == "validated"
            assert set(response.json()) == {
                "intent",
                "status",
                "message",
                "missing_fields",
                "answer",
                "validation",
            }
            assert world.bank.COUNTERS["sr"] == 0

    asyncio.run(run())


@pytest.mark.parametrize("field", ["query", "notes"])
def test_sensitive_input_is_rejected_before_durable_request_storage(world, field: str) -> None:
    secret = "sk-synthetic-credential-that-must-not-be-checkpointed"

    async def run() -> None:
        async with world.clients() as caller:
            body = payload()
            if field == "query":
                body["query"] = secret
            else:
                body["action_details"]["notes"] = secret
            response = await caller.post("/agent/runs", headers=OWNER, json=body)
            assert response.status_code == 422
            assert secret not in response.text
            assert world.store.collection.count_documents({}) == 0
            world.classify.assert_not_called()
            assert not world.retrievals and not world.calls

    asyncio.run(run())


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_streamlit_owner_reviewer_flow_through_actual_graph_and_bank(
    world, monkeypatch, decision: str
) -> None:
    from fastapi.testclient import TestClient
    from streamlit.testing.v1 import AppTest

    from app.core import lifespan
    from app.ui import app as ui

    with asyncio.Runner() as runner:
        context = world.clients()
        runner.run(context.__aenter__())
        monkeypatch.setattr(lifespan, "get_settings", world.app.dependency_overrides[get_settings])
        try:
            with TestClient(world.app) as agent_api_client:

                def dispatch(request: httpx.Request) -> httpx.Response:
                    response = agent_api_client.request(
                        request.method,
                        str(request.url),
                        content=request.content,
                        headers=request.headers,
                    )
                    return httpx.Response(
                        response.status_code,
                        content=response.content,
                        headers=response.headers,
                        request=request,
                    )

                with httpx.Client(
                    base_url="http://agent", transport=httpx.MockTransport(dispatch)
                ) as client:
                    monkeypatch.setattr(ui, "api_client", lambda _: client)
                    screen = AppTest.from_file(
                        Path("app/streamlit_app.py").resolve(), default_timeout=15
                    )
                    screen.session_state["workspace"] = "Service requests"
                    screen.session_state["policy_date"] = date(2026, 10, 1)
                    screen.run()
                    screen.text_input(key="agent_token").set_value("o" * 32).run()
                    screen.text_input(key="agent_customer").set_value("NXC-0010")
                    screen.selectbox(key="agent_kind").select("CALLBACK")
                    next(
                        b for b in screen.button if b.label == "Prepare for approval"
                    ).click().run()
                    assert not screen.exception and not screen.error
                    assert "pending_approval" in str([m.value for m in screen.markdown])
                    assert world.bank.COUNTERS["sr"] == 0 and len(world.retrievals) == 1
                    saved_run = screen.text_input(key="agent_run_id").value
                    screen.text_input(key="agent_token").set_value("r" * 32).run()
                    next(b for b in screen.button if b.label == "Load saved run").click().run()
                    next(r for r in screen.radio if r.label == "Reviewer decision").set_value(
                        decision
                    )
                    screen.checkbox[0].check()
                    next(
                        b for b in screen.button if b.label == "Submit reviewer decision"
                    ).click().run()
                    assert not screen.exception and not screen.error
                    assert screen.text_input(key="agent_run_id").value == saved_run
                    assert world.bank.COUNTERS["sr"] == (1 if decision == "approve" else 0)
                    assert len(world.retrievals) == (2 if decision == "approve" else 1)
        finally:
            runner.run(context.__aexit__(None, None, None))


@pytest.mark.parametrize("kind", TYPES)
def test_text_request_clarification_uses_both_mcp_paths_before_approved_action(
    world, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from app.mcp.server import build_mcp_server
    from app.services import agent_mcp_retrieval

    world.config.agent_retrieval_transport = "mcp"
    needs_loan = kind in ("INTEREST_CERTIFICATE", "OPT_IN_CIR_2026_07_PART_PREPAY")
    choice = IntentDecision(
        intent="supported_action",
        product_id="NEXA-HL" if kind == "OPT_IN_CIR_2026_07_PART_PREPAY" else None,
        as_of=None,
        customer_id=None,
        action="createServiceRequest",
        request_type=kind,
        loan_account_id=None,
        product_required=False,
        missing_fields=[],
    )
    world.classification = choice

    async def run() -> None:
        async with world.clients() as caller:
            rag = world.app.dependency_overrides[agent_api.rag_client]()
            policy_server = build_mcp_server(rag)
            monkeypatch.setattr(agent_mcp_retrieval, "StdioTransport", lambda **_: policy_server)
            query = f"Please prepare a {kind} service request."
            initial = await caller.post(
                "/v2/agent/runs", headers=OWNER, json={"query": query, "as_of": "2026-10-01"}
            )
            assert initial.status_code == 201, initial.text
            clarification = initial.json()["response"]
            assert clarification["status"] == "needs_clarification"
            assert "customer_id" in clarification["missing_fields"]
            assert clarification["suggestions"]
            assert not world.retrievals and not world.calls
            assert world.bank.COUNTERS["sr"] == 0
            loan = "NXL-HL-0010" if needs_loan else None
            world.classification = choice.model_copy(
                update={"customer_id": "NXC-0010", "loan_account_id": loan}
            )
            prepared = await caller.post(
                "/v2/agent/runs",
                headers=OWNER,
                json={
                    "query": query + f"\nClarification: Customer NXC-0010, loan {loan}.",
                    "as_of": "2026-10-01",
                },
            )
            assert prepared.status_code == 201, prepared.text
            pending = prepared.json()
            assert pending["status"] == "pending_approval"
            proposal = pending["response"]["validation"]["proposal"]
            assert proposal["request_type"] == kind
            assert proposal["customer_id"] == "NXC-0010"
            assert proposal["loan_account_id"] == loan
            assert len(world.retrievals) == 1
            assert world.bank.COUNTERS["sr"] == 0
            submitted = await caller.post(
                f"/v2/agent/runs/{pending['run_id']}/approval",
                headers=REVIEWER,
                json={"decision": "approve", "proposal_hash": pending["proposal_hash"]},
            )
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()["status"] == "submitted"
            assert world.bank.COUNTERS["sr"] == 1
            assert len(world.retrievals) == 2
            assert world.classify.call_count == 2
            assert [request.method for request in world.calls] == ["GET", "GET", "POST"]

    asyncio.run(run())


@pytest.mark.parametrize("decision", ["approve", "reject", "self", "stale", "timeout"])
def test_unified_chat_case_uses_exact_approval_and_idempotent_recovery(
    world, decision: str
) -> None:
    from app.models.validation import CaseBody

    for index, principal in enumerate(world.config.agent_principals[:2]):
        world.config.agent_principals[index] = principal.model_copy(
            update={"customer_ids": principal.customer_ids | {"NXC-0001"}}
        )
    details = CaseBody(
        customer_id="NXC-0001",
        category="LOAN_ENQUIRY",
        priority="LOW",
        summary="Home-loan documents enquiry",
    )
    world.classification = IntentDecision(
        intent="supported_action",
        product_id=None,
        as_of=None,
        customer_id="NXC-0001",
        action="createCase",
        case_details=details,
        product_required=False,
        missing_fields=[],
    )

    async def run() -> None:
        async with world.clients() as caller:
            first = await caller.post(
                "/v2/agent/runs",
                headers=OWNER,
                json={
                    "query": "Prepare a LOW-priority LOAN_ENQUIRY case for NXC-0001 about home-loan documents. Obtain approval before submitting.",
                    "as_of": "2026-10-01",
                },
            )
            assert first.status_code == 201, first.text
            pending = first.json()
            assert pending["status"] == "pending_approval"
            assert pending["response"]["validation"]["case_proposal"] == details.model_dump(
                mode="json"
            )
            assert pending["response"]["validation"]["proposal"] is None
            legacy = await caller.get(f"/agent/runs/{pending['run_id']}", headers=OWNER)
            assert legacy.status_code == 200
            assert legacy.json()["status"] == "blocked"
            assert legacy.json()["proposal_hash"] is None
            assert legacy.json()["response"]["validation"] is None
            assert "v2" in legacy.json()["response"]["message"]
            assert world.bank.COUNTERS["case"] == 0
            assert len(world.retrievals) == 1
            headers = OWNER if decision == "self" else REVIEWER
            fingerprint = "0" * 64 if decision == "stale" else pending["proposal_hash"]
            if decision == "timeout":
                world.bank_client.headers["X-Mock-Fault"] = "commit-then-timeout"
            result = await caller.post(
                f"/v2/agent/runs/{pending['run_id']}/approval",
                headers=headers,
                json={
                    "decision": "reject" if decision == "reject" else "approve",
                    "proposal_hash": fingerprint,
                },
            )
            if decision in ("self", "stale"):
                assert result.status_code == (403 if decision == "self" else 409)
                assert world.bank.COUNTERS["case"] == 0
            elif decision == "reject":
                assert result.json()["status"] == "rejected"
                assert world.bank.COUNTERS["case"] == 0
            else:
                if decision == "timeout":
                    assert result.status_code == 503
                    assert world.bank.COUNTERS["case"] == 1
                    result = await caller.post(
                        f"/v2/agent/runs/{pending['run_id']}/retry", headers=OWNER
                    )
                assert result.status_code == 200, result.text
                assert result.json()["status"] == "submitted"
                receipt = result.json()["response"]["case_submission"]["case"]
                assert receipt["customer_id"] == "NXC-0001"
                assert receipt["category"] == "LOAN_ENQUIRY" and receipt["priority"] == "LOW"
                assert world.bank.COUNTERS["case"] == 1
                repeated = await caller.post(
                    f"/v2/agent/runs/{pending['run_id']}/retry", headers=OWNER
                )
                assert repeated.json() == result.json()
                assert world.bank.COUNTERS["case"] == 1
                assert len(world.retrievals) == 2
            world.classify.assert_called_once()

    asyncio.run(run())


def test_transaction_query_routes_to_banking_without_policy_retrieval_or_writes(world) -> None:
    from app.models.banking_mcp import TransactionDetails

    world.classification = IntentDecision(
        intent="customer_context",
        product_id=None,
        as_of=None,
        customer_id="NXC-0010",
        action=None,
        product_required=False,
        missing_fields=[],
        transactions=TransactionDetails(
            account_id=next(
                a["account_id"]
                for a in world.bank.CUSTOMERS["NXC-0010"]["accounts"]
                if a["type"] != "loan"
            ),
            from_date="2026-11-01",
            to_date="2026-11-14",
            limit=5,
        ),
    )

    async def run() -> None:
        async with world.clients() as caller:
            result = await caller.post(
                "/v2/agent",
                headers=OWNER,
                json={
                    "query": "Get at most five transactions from an owned account for 1–14 November 2026 for NXC-0010"
                },
            )
            assert result.status_code == 200, result.text
            body = result.json()
            assert body["status"] == "answered"
            assert len(body["transactions"]["items"]) <= 5
            assert not world.retrievals
            assert all(request.method == "GET" for request in world.calls)
            assert any(
                request.url.params.get("limit") == "5"
                and request.url.params.get("from") == "2026-11-01"
                and request.url.params.get("to") == "2026-11-14"
                for request in world.calls
            )
            assert world.bank.COUNTERS["case"] == world.bank.COUNTERS["sr"] == 0

    asyncio.run(run())


def test_legacy_run_responses_keep_the_strict_v1_field_contract(world) -> None:
    from pydantic import ConfigDict, create_model, model_validator

    from app.models.agent import AgentResponse
    from app.models.validation import ValidationResult

    def allowed_requires_proposal(value: Any) -> Any:
        if (value.decision == "allowed") != (value.proposal is not None):
            raise ValueError("Only an allowed decision can carry a proposal")
        return value

    # Freeze v1 field names while sharing unchanged scalar types with the application.
    validation_fields: dict[str, Any] = {
        name: (field.annotation, field)
        for name, field in ValidationResult.model_fields.items()
        if name in {"decision", "reason", "evidence", "proposal", "requires_approval"}
    }
    validators: dict[str, Any] = {
        "allowed": model_validator(mode="after")(allowed_requires_proposal)
    }
    legacy_validation = create_model(
        "V1Validation",
        __config__=ConfigDict(extra="forbid"),
        __validators__=validators,
        **validation_fields,
    )
    fields: dict[str, Any] = {
        name: (field.annotation, field)
        for name, field in AgentResponse.model_fields.items()
        if name in {"intent", "status", "message", "missing_fields", "answer", "submission"}
    }
    fields["validation"] = (legacy_validation | None, None)
    legacy_response = create_model("V1Response", __config__=ConfigDict(extra="forbid"), **fields)

    async def run() -> None:
        async with world.clients() as caller:
            started = await caller.post("/agent/runs", headers=OWNER, json=payload())
            body = started.json()
            identifier = body["run_id"]
            loaded = await caller.get(f"/agent/runs/{identifier}", headers=OWNER)
            approved = await caller.post(
                f"/agent/runs/{identifier}/approval",
                headers=REVIEWER,
                json={"decision": "approve", "proposal_hash": body["proposal_hash"]},
            )
            retried = await caller.post(f"/agent/runs/{identifier}/retry", headers=OWNER)
            for response in (started, loaded, approved, retried):
                assert response.status_code < 300, response.text
                legacy_response.model_validate(response.json()["response"])
            assert world.bank.COUNTERS["sr"] == 1

    asyncio.run(run())


@pytest.mark.parametrize("missing", ["case_details", "transactions"])
def test_legacy_client_is_directed_to_v2_for_new_clarification_fields(missing: str) -> None:
    from app.models.agent import LegacyAgentResponse

    response = LegacyAgentResponse.model_validate(
        {
            "intent": "missing_information",
            "status": "needs_clarification",
            "message": "Details?",
            "missing_fields": [missing],
        }
    )
    assert response.status == "blocked" and not response.missing_fields
    assert "v2" in response.message
