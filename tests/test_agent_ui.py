from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args
from uuid import uuid4

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from app.models.agent_run import AgentRunResponse
from app.models.validation import ServiceRequestType
from app.ui import app as ui


@pytest.fixture
def run_view() -> dict:
    now = datetime.now(UTC)
    return {
        "run_id": str(uuid4()),
        "status": "pending_approval",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=15)).isoformat(),
        "proposal_hash": "a" * 64,
        "response": {
            "intent": "supported_action",
            "status": "validated",
            "message": "Review required",
            "validation": {
                "decision": "allowed",
                "reason": "Permitted by policy",
                "proposal": {"customer_id": "NXC-0010", "request_type": "CALLBACK"},
                "evidence": [
                    {
                        "quote": "Callback requests are permitted.",
                        "citation": {
                            "doc_id": "POL-OPS-001",
                            "title": "Scope",
                            "policy_version": "1.0",
                            "clause": "5.1",
                            "effective_date": "2025-01-01",
                        },
                    }
                ],
            },
        },
    }


@pytest.fixture
def screen(monkeypatch: pytest.MonkeyPatch, run_view: dict):
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=run_view)

    with httpx.Client(
        base_url="http://agent.test", transport=httpx.MockTransport(respond)
    ) as client:
        monkeypatch.setattr(ui, "api_client", lambda _: client)
        app = AppTest.from_file(Path("app/streamlit_app.py").resolve(), default_timeout=10)
        app.session_state["workspace"] = "Service requests"
        app.run()
        app.text_input(key="agent_token").set_value("owner-test-credential").run()
        assert not app.exception
        yield app, calls


@pytest.mark.parametrize("kind", get_args(ServiceRequestType.__value__))
def test_start_uses_shared_schema_and_preserves_saved_run(screen, kind: str) -> None:
    app, calls = screen
    app.text_input(key="agent_customer").set_value("NXC-0010")
    app.selectbox(key="agent_kind").select(kind)
    app.text_input[2].set_value("NXL-0010")
    next(b for b in app.button if b.label == "Prepare for approval").click().run()
    assert not app.exception
    assert len(calls) == 1
    assert calls[0].url.path == "/agent/runs"
    body = json.loads(calls[0].content)
    assert body["action_details"]["request_type"] == kind
    assert body["action_details"]["loan_account_id"] == "NXL-0010"
    assert body["customer_id"] == "NXC-0010" and body["as_of"]
    assert calls[0].headers["Authorization"] == "Bearer owner-test-credential"
    assert app.text_input(key="agent_run_id").value
    assert app.json


def load_run(app: AppTest, run_view: dict) -> None:
    app.text_input(key="agent_run_id").set_value(run_view["run_id"])
    next(b for b in app.button if b.label == "Load saved run").click().run()
    assert not app.exception


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_reviewer_posts_loaded_hash_only_after_confirmation(
    screen, run_view: dict, decision: str
) -> None:
    app, calls = screen
    load_run(app, run_view)
    next(r for r in app.radio if r.label == "Reviewer decision").set_value(decision)
    next(b for b in app.button if b.label == "Submit reviewer decision").click().run()
    assert len(calls) == 1
    assert app.warning
    app.checkbox[0].check()
    next(b for b in app.button if b.label == "Submit reviewer decision").click().run()
    assert not app.exception
    assert len(calls) == 2
    assert calls[-1].url.path == f"/agent/runs/{run_view['run_id']}/approval"
    assert json.loads(calls[-1].content) == {"decision": decision, "proposal_hash": "a" * 64}


def test_credential_change_clears_loaded_proposal(screen, run_view: dict) -> None:
    app, calls = screen
    load_run(app, run_view)
    assert app.json
    app.text_input(key="agent_token").set_value("different-reviewer").run()
    assert not app.exception and not app.json
    assert len(calls) == 1
    assert not any(b.label == "Submit reviewer decision" for b in app.button)
    app.text_input(key="agent_token").set_value("").run()
    assert app.info and len(calls) == 1


def test_uncertain_action_retry_keeps_same_run_and_never_starts_new(screen, run_view: dict) -> None:
    app, calls = screen
    run_view["status"] = "unconfirmed"
    load_run(app, run_view)
    next(b for b in app.button if b.label == "Retry the same approved operation").click().run()
    assert not app.exception
    assert [(c.method, c.url.path) for c in calls] == [
        ("GET", f"/agent/runs/{run_view['run_id']}"),
        ("POST", f"/agent/runs/{run_view['run_id']}/retry"),
    ]


def test_invalid_customer_and_run_id_do_not_call_api(screen) -> None:
    app, calls = screen
    app.text_input(key="agent_customer").set_value("not-a-customer")
    next(b for b in app.button if b.label == "Prepare for approval").click().run()
    assert app.error and not calls
    app.text_input(key="agent_run_id").set_value("not-a-uuid")
    next(b for b in app.button if b.label == "Load saved run").click().run()
    assert app.error and not calls and not app.exception


@pytest.mark.parametrize("failure", ["http", "schema", "network", "storage"])
def test_api_failures_hide_stale_data_and_raw_errors(
    screen, run_view: dict, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    app, calls = screen
    load_run(app, run_view)

    def fail(request: httpx.Request) -> httpx.Response:
        if failure == "network":
            raise httpx.ConnectError("raw-private-diagnostic", request=request)
        if failure == "storage":
            return httpx.Response(503, json={"detail": "Agent run storage unavailable"})
        return (
            httpx.Response(503, json={"detail": "raw-private-diagnostic"})
            if failure == "http"
            else httpx.Response(200, json={"bad": "raw-private-diagnostic"})
        )

    with httpx.Client(base_url="http://agent.test", transport=httpx.MockTransport(fail)) as client:
        monkeypatch.setattr(ui, "api_client", lambda _: client)
        next(b for b in app.button if b.label == "Load saved run").click().run()
    assert not app.exception and not app.json and app.error
    assert "raw-private-diagnostic" not in app.error[0].value
    if failure == "storage":
        assert "MongoDB/Atlas connection" in app.error[0].value
        assert "load any saved run" in app.error[0].value
    assert len(calls) == 1


def test_terminal_submitted_receipt_has_no_approval_or_retry(screen, run_view: dict) -> None:
    app, _ = screen
    run_view["status"] = "submitted"
    response = run_view["response"]
    response["status"] = "submitted"
    response["submission"] = {
        "service_request": {
            "service_request_id": "SR-001",
            "customer_id": "NXC-0010",
            "request_type": "CALLBACK",
            "status": "RECEIVED",
            "created_at": datetime.now(UTC).isoformat(),
        },
        "evidence": response["validation"]["evidence"],
    }
    AgentRunResponse.model_validate(run_view)
    load_run(app, run_view)
    assert app.success and "not completion" in app.success[0].value
    assert not any(
        b.label in ("Submit reviewer decision", "Retry the same approved operation")
        for b in app.button
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "Credential rejected"),
        (403, "customer access"),
        (404, "not accessible"),
        (409, "approval expiry"),
        (422, "request details and notes"),
        (503, "RAG and banking"),
        (500, "could not complete"),
    ],
)
def test_preparation_reports_safe_actionable_http_errors(
    screen, monkeypatch: pytest.MonkeyPatch, status: int, expected: str
) -> None:
    app, _ = screen
    calls: list[httpx.Request] = []

    def fail(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json={"detail": "raw-private-diagnostic"})

    with httpx.Client(base_url="http://agent.test", transport=httpx.MockTransport(fail)) as client:
        monkeypatch.setattr(ui, "api_client", lambda _: client)
        app.text_input(key="agent_customer").set_value("NXC-0010")
        next(b for b in app.button if b.label == "Prepare for approval").click().run()
    assert not app.exception and app.error
    assert expected in app.error[0].value
    assert f"HTTP {status}" in app.error[0].value
    assert "raw-private-diagnostic" not in app.error[0].value
    assert len(calls) == 1 and calls[0].url.path == "/agent/runs"
    assert not app.json


@pytest.mark.parametrize("operation", ["approval", "retry"])
def test_failed_decision_or_retry_keeps_error_visible_and_saved_id(
    screen, run_view: dict, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    app, _ = screen
    if operation == "retry":
        run_view["status"] = "unconfirmed"
    load_run(app, run_view)
    if operation == "approval":
        app.checkbox[0].check()
    calls: list[httpx.Request] = []

    def fail(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(403, json={"detail": "raw-private-diagnostic"})

    with httpx.Client(base_url="http://agent.test", transport=httpx.MockTransport(fail)) as client:
        monkeypatch.setattr(ui, "api_client", lambda _: client)
        label = (
            "Submit reviewer decision"
            if operation == "approval"
            else "Retry the same approved operation"
        )
        next(b for b in app.button if b.label == label).click().run()
    assert not app.exception and app.error and not app.json
    assert "HTTP 403" in app.error[0].value
    assert "raw-private-diagnostic" not in app.error[0].value
    assert app.text_input(key="agent_run_id").value == run_view["run_id"]
    assert len(calls) == 1
    assert calls[0].url.path == f"/agent/runs/{run_view['run_id']}/{operation}"


def test_agent_chat_keeps_request_for_clarification_and_displays_suggestions(
    screen, run_view: dict
) -> None:
    app, calls = screen
    run_view.update(status="needs_clarification", proposal_hash=None)
    run_view["response"] = {
        "intent": "missing_information",
        "status": "needs_clarification",
        "message": "Please provide the customer ID.",
        "missing_fields": ["customer_id"],
        "suggestions": ["Provide your authorized synthetic customer ID in NXC-0000 format."],
    }
    app.chat_input[0].set_value("Please arrange a callback.").run()
    assert not app.exception
    assert "authorized synthetic customer ID" in app.info[0].value
    first = json.loads(calls[0].content)
    assert first["query"] == "Please arrange a callback."
    assert first["action_details"] is None
    app.chat_input[0].set_value("NXC-0010").run()
    second = json.loads(calls[1].content)
    assert second["query"] == "Please arrange a callback.\nClarification: NXC-0010"
    assert second["as_of"] == first["as_of"]
    assert all(c.url.path == "/agent/runs" for c in calls)
    assert all(c.headers["Authorization"] == "Bearer owner-test-credential" for c in calls)


@pytest.mark.parametrize("reset", ["credential", "new", "complete", "form", "load"])
def test_agent_chat_does_not_reuse_clarification_after_reset(
    screen, run_view: dict, reset: str
) -> None:
    app, calls = screen
    app.session_state["agent_pending_query"] = "Previous customer's request"
    if reset == "credential":
        app.text_input(key="agent_token").set_value("different-credential").run()
    elif reset == "new":
        next(b for b in app.button if b.label == "New agent conversation").click().run()
    elif reset == "complete":
        app.chat_input[0].set_value("Complete the previous clarification").run()
    elif reset == "form":
        app.text_input(key="agent_customer").set_value("NXC-0010")
        next(b for b in app.button if b.label == "Prepare for approval").click().run()
    else:
        load_run(app, run_view)
    calls.clear()
    app.chat_input[0].set_value("What services are permitted?").run()
    assert not app.exception
    assert json.loads(calls[0].content)["query"] == "What services are permitted?"


def test_agent_chat_rejects_oversized_combined_request_without_api_call(screen) -> None:
    app, calls = screen
    app.session_state["agent_pending_query"] = "x" * 1990
    app.chat_input[0].set_value("NXC-0010, please").run()
    assert not app.exception and app.error
    assert "2,000 characters" in app.error[0].value
    assert not calls


def test_agent_chat_renders_policy_evidence_and_is_available_from_workspace(
    screen, run_view: dict
) -> None:
    app, calls = screen
    run_view.update(status="answered", proposal_hash=None)
    evidence = run_view["response"]["validation"]["evidence"][0]
    run_view["response"] = {
        "intent": "policy_question",
        "status": "answered",
        "message": "Callback requests are permitted [1].",
        "answer": {
            "as_of": "2026-10-01",
            "status": "answered",
            "answer": "Callback requests are permitted [1].",
            "citations": [
                {
                    "evidence_id": 1,
                    "quote": evidence["quote"],
                    "matched_child": evidence["citation"],
                }
            ],
            "generation_ms": 1,
            "retrieval_timings": {"search_ms": 1, "parent_ms": 1, "total_ms": 2},
        },
    }
    app.radio(key="workspace").set_value("Agent chat").run()
    app.chat_input[0].set_value("What services are permitted?").run()
    assert not app.exception
    assert calls[0].url.path == "/v2/agent/runs"
    assert app.text[0].value == evidence["quote"]
    assert not any(b.label == "Submit reviewer decision" for b in app.button)


@pytest.fixture
def unified_screen(monkeypatch: pytest.MonkeyPatch, run_view: dict):
    calls = []
    responses = {
        "public": {
            "intent": "policy_question",
            "status": "answered",
            "message": "Published policy answer",
        },
        "run": run_view,
    }

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if responses.get("failure") == "http":
            return httpx.Response(503, json={"detail": "private diagnostic"})
        if responses.get("failure") == "shape":
            return httpx.Response(200, json={"unexpected": "private diagnostic"})
        return httpx.Response(
            200, json=responses["public"] if request.url.path == "/v2/agent" else responses["run"]
        )

    with httpx.Client(
        base_url="http://agent.test", transport=httpx.MockTransport(respond)
    ) as client:
        monkeypatch.setattr(ui, "api_client", lambda _: client)
        app = AppTest.from_file(Path("app/streamlit_app.py").resolve(), default_timeout=15).run()
        assert not app.exception
        yield app, calls, responses


def test_default_agent_chat_has_one_prompt_without_service_form_or_required_login(
    unified_screen,
) -> None:
    app, calls, _ = unified_screen
    assert app.radio(key="workspace").value == "Agent chat"
    assert len(app.chat_input) == 1
    assert not app.selectbox
    assert not any(button.label == "Prepare for approval" for button in app.button)
    assert not calls
    app.chat_input[0].set_value("Maximum loan amount for nexa home loan").run()
    assert not app.exception
    assert calls[0].url.path == "/v2/agent"
    assert "Authorization" not in calls[0].headers
    assert "Published policy answer" in str([item.value for item in app.markdown])
    app.chat_input[0].set_value("eligibiliby for the Autoloan").run()
    assert len(app.chat_message) == 4
    assert json.loads(calls[1].content)["query"] == "eligibiliby for the Autoloan"
    app.button(key="new_chat").click().run()
    assert not app.chat_message and not app.exception


@pytest.mark.parametrize("failure", ["http", "shape"])
def test_unified_chat_keeps_failed_turn_and_safe_error(unified_screen, failure: str) -> None:
    app, calls, responses = unified_screen
    responses["failure"] = failure
    app.chat_input[0].set_value("Maximum loan amount for nexa home loan").run()
    assert not app.exception and app.error
    assert "private diagnostic" not in str([item.value for item in app.error])
    assert len(calls) == 1 and len(app.chat_message) == 2


def test_unified_chat_continues_clarification_and_clears_customer_text_on_identity_change(
    unified_screen,
) -> None:
    app, calls, responses = unified_screen
    responses["public"] = {
        "intent": "missing_information",
        "status": "needs_clarification",
        "message": "Provide your customer ID.",
        "missing_fields": ["customer_id"],
        "suggestions": ["Use NXC-0000 format."],
    }
    app.chat_input[0].set_value(
        "Get at most five transactions from an owned account for 1–14 November 2026"
    ).run()
    assert app.info and "NXC-0000" in app.info[0].value
    app.chat_input[0].set_value("NXC-0001").run()
    assert "Clarification: NXC-0001" in json.loads(calls[1].content)["query"]
    app.text_input(key="agent_token").set_value("owner-synthetic-credential").run()
    assert not app.chat_message
    app.chat_input[0].set_value("Prepare a callback for NXC-0010").run()
    assert calls[-1].url.path == "/v2/agent/runs"
    assert json.loads(calls[-1].content)["query"] == "Prepare a callback for NXC-0010"
    assert app.json


def test_unified_chat_can_load_review_and_update_a_saved_proposal(
    unified_screen, run_view: dict
) -> None:
    app, calls, responses = unified_screen
    app.text_input(key="agent_token").set_value("reviewer-synthetic-credential").run()
    app.text_input(key="agent_run_id").set_value("invalid")
    next(button for button in app.button if button.label == "Load saved run").click().run()
    assert app.error and not calls
    app.text_input(key="agent_run_id").set_value(run_view["run_id"])
    next(button for button in app.button if button.label == "Load saved run").click().run()
    assert app.json and not app.exception
    responses["run"]["status"] = "rejected"
    responses["run"]["response"]["status"] = "rejected"
    responses["run"]["response"]["message"] = "Proposal rejected; no action performed"
    app.checkbox[0].check()
    next(
        button for button in app.button if button.label == "Submit reviewer decision"
    ).click().run()
    assert not app.exception
    assert calls[-1].url.path.endswith("/approval")
    assert "Proposal rejected" in str([item.value for item in app.markdown])


def test_unified_chat_combined_query_is_bounded(unified_screen) -> None:
    app, calls, _ = unified_screen
    app.session_state["agent_pending_query"] = "x" * 1990
    app.chat_input[0].set_value("NXC-0001, November 2026").run()
    assert not calls and app.error and not app.exception


def test_service_workspace_still_shows_policy_citations(screen, run_view: dict) -> None:
    app, _ = screen
    source = run_view["response"]["validation"]["evidence"][0]
    run_view["status"] = "answered"
    run_view["response"] = {
        "intent": "policy_question",
        "status": "answered",
        "message": "Published terms [1]",
        "answer": {
            "as_of": "2026-11-15",
            "status": "answered",
            "answer": "Published terms [1]",
            "citations": [
                {"evidence_id": 1, "quote": source["quote"], "matched_child": source["citation"]}
            ],
            "generation_ms": 1,
            "retrieval_timings": {"search_ms": 1, "parent_ms": 1, "total_ms": 2},
        },
    }
    app.chat_input[0].set_value("Explain the policy").run()
    assert not app.exception
    assert app.text[0].value == source["quote"]


@pytest.mark.parametrize("kind", ["transactions", "transactions_last_page", "case", "service"])
def test_unified_chat_displays_banking_results(unified_screen, kind: str) -> None:
    app, _, responses = unified_screen
    response = responses["run"]["response"]
    if kind.startswith("transactions"):
        responses["run"].update(status="answered", proposal_hash=None)
        response.update(
            intent="customer_context",
            status="answered",
            message="Found one transaction",
            validation=None,
            transactions={
                "account_id": "NXA-100001",
                "items": [
                    {
                        "txn_id": "NXT-1",
                        "account_id": "NXA-100001",
                        "txn_date": "2026-11-10",
                        "amount": 100,
                        "direction": "debit",
                        "channel": "UPI",
                        "narration": "Synthetic purchase",
                        "category": "shopping",
                        "balance_after": 900,
                    }
                ],
                "next_cursor": "5" if kind == "transactions" else None,
            },
        )
    else:
        responses["run"]["status"] = "submitted"
        response["status"] = "submitted"
        evidence = response["validation"]["evidence"]
        if kind == "case":
            response["case_submission"] = {
                "case": {
                    "case_id": "NXCASE-000001",
                    "customer_id": "NXC-0010",
                    "category": "LOAN_ENQUIRY",
                    "priority": "LOW",
                    "summary": "Home-loan documents enquiry",
                    "related_application_id": None,
                    "status": "OPEN",
                    "created_at": datetime.now(UTC).isoformat(),
                    "notes": [],
                    "escalation": None,
                },
                "evidence": evidence,
            }
        else:
            response["submission"] = {
                "service_request": {
                    "service_request_id": "SR-001",
                    "customer_id": "NXC-0010",
                    "request_type": "CALLBACK",
                    "status": "RECEIVED",
                    "created_at": datetime.now(UTC).isoformat(),
                },
                "evidence": evidence,
            }
    app.text_input(key="agent_token").set_value("synthetic-owner-credential").run()
    app.chat_input[0].set_value("Show the banking result").run()
    assert not app.exception
    if kind.startswith("transactions"):
        assert app.dataframe
    else:
        assert app.success
