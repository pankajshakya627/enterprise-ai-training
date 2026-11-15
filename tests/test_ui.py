from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import mongomock
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.routing import compile_path

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

import app.core.lifespan as lifespan_module
import app.main as main_module
from app.api import answer as answer_api
from app.api import retrieval as retrieval_api
from app.core.config import Settings, get_settings
from app.database import feedback as feedback_database
from app.models.answer import AnswerResponse
from app.models.evaluation import METRIC_NAMES, AnswerQuality, EvaluatedAnswerResponse, QualityScore
from app.models.retrieval import RetrieveResponse
from app.ui import app as ui


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Iterator[httpx.Client]:
    settings = settings.model_copy(
        update={"feedback_reviewer_token": SecretStr("reviewer-test-token-000000000000000000")}
    )
    retrieved = RetrieveResponse.model_validate(
        {
            "as_of": "2026-10-19",
            "results": [
                {
                    "rank": 1,
                    "score": 0.9,
                    "text": "Maximum is 7.5 crore.",
                    "parent_text": "Maximum is 7.5 crore.",
                    "citation": {
                        "doc_id": "POL-HL-V3",
                        "title": "Home Loan",
                        "policy_version": "3",
                        "clause": "4.1",
                        "effective_date": "2025-10-01",
                    },
                }
            ],
            "timings": {"search_ms": 1, "parent_ms": 1, "total_ms": 2},
        }
    )
    answer = AnswerResponse.model_validate(
        {
            "as_of": retrieved.as_of,
            "status": "answered",
            "answer": "The maximum is 7.5 crore [1].",
            "citations": [
                {
                    "evidence_id": 1,
                    "quote": retrieved.results[0].text,
                    "matched_child": retrieved.results[0].citation.model_dump(),
                }
            ],
            "generation_ms": 10,
            "retrieval_timings": retrieved.timings.model_dump(),
        }
    )
    monkeypatch.setattr(lifespan_module, "get_settings", lambda: settings)
    monkeypatch.setattr(answer_api, "answer_policy", AsyncMock(return_value=answer))
    monkeypatch.setattr(
        answer_api,
        "answer_with_quality",
        AsyncMock(
            return_value=EvaluatedAnswerResponse(
                answer=answer,
                quality=AnswerQuality(
                    metrics={
                        name: QualityScore(status="scored", value=0.9) for name in METRIC_NAMES
                    },
                    reference_source="provided",
                    evaluated_at=datetime.now(UTC),
                    datadog_status="queued",
                    datadog_url="https://app.datadoghq.com/llm/traces",
                ),
            )
        ),
    )
    monkeypatch.setattr(retrieval_api, "retrieve_policy", AsyncMock(return_value=retrieved))
    storage: Any = mongomock.MongoClient().db
    monkeypatch.setattr(feedback_database, "_database", lambda _: storage)
    api = main_module.create_app()
    api.dependency_overrides[get_settings] = lambda: settings
    with TestClient(api) as asgi_client:

        def dispatch(request: httpx.Request) -> httpx.Response:
            response = asgi_client.request(
                request.method, str(request.url), content=request.content, headers=request.headers
            )
            return httpx.Response(
                response.status_code,
                content=response.content,
                headers=response.headers,
                request=request,
            )

        with httpx.Client(
            base_url="http://testserver", transport=httpx.MockTransport(dispatch)
        ) as client:
            monkeypatch.setattr(ui, "api_client", lambda _: client)
            yield client


def policy_screen() -> AppTest:
    app = AppTest.from_file(Path("app/streamlit_app.py").resolve())
    app.session_state["workspace"] = "Policy chat"
    return app.run()


@pytest.mark.parametrize("decision", ["accepted", "rejected"])
def test_existing_ui_endpoint_coverage(client: httpx.Client, decision: str) -> None:
    responses: list[httpx.Response] = []
    client.event_hooks["response"].append(responses.append)
    app = policy_screen()
    assert not app.exception
    assert app.radio(key="workspace").value == "Policy chat"
    assert len(app.chat_input) == 1
    assert not responses  # Initial rendering must not issue API calls.

    app.toggle(key="evaluate_quality").set_value(False)
    app.date_input[0].set_value(date(2026, 10, 19))
    app.chat_input[0].set_value("What is the loan limit?").run()
    assert not app.exception
    assert any("7.5 crore" in item.value for item in app.markdown)
    assert any("Maximum is 7.5 crore." in item.value for item in app.text)
    next(button for button in app.button if button.label == "Send feedback").click().run()
    assert not app.exception
    assert responses[-1].status_code == 201
    assert app.success[0].value == "Feedback saved for review."

    saved = responses[-1].json()
    assert saved["request"]["query"] == "What is the loan limit?"
    assert saved["helpful"] is True
    assert saved["submitter_id"] == app.session_state["feedback_submitter_id"]
    assert saved["correction"] == ""

    app.radio(key="workspace").set_value("Review feedback").run()
    app.text_input(key="reviewer_token").input("reviewer-test-token-000000000000000000").run()
    next(
        button for button in app.button if button.label == "Load feedback for review"
    ).click().run()
    assert not app.exception
    next(widget for widget in app.text_input if widget.label == "Reviewer name").input("Instructor")
    next(widget for widget in app.text_area if widget.label == "Review note").input(
        "Source checked."
    )
    app.selectbox[0].select(decision)
    next(button for button in app.button if button.label == "Save review").click().run()
    assert not app.exception
    assert any("Reviewed by Instructor: Source checked." in item.value for item in app.caption)

    app.radio(key="workspace").set_value("Policy chat").run()
    app.toggle(key="evaluate_quality").set_value(True)
    app.chat_input[0].set_value("Evaluate this loan answer").run()
    assert not app.exception
    assert not app.metric
    app.segmented_control(key="response_mode").set_value("Evidence")
    app.chat_input[0].set_value("What is the loan limit?").run()
    assert not app.exception
    assert any("Maximum is 7.5 crore." in item.value for item in app.text)

    # OpenAPI includes operations inside FastAPI's nested router objects.
    paths = main_module.app.openapi()["paths"]
    assert paths
    uncovered = set()
    for path, operations in paths.items():
        pattern, _, _ = compile_path(path)
        for method in operations:
            if not any(
                response.request.method == method.upper()
                and pattern.fullmatch(response.request.url.path)
                and response.is_success
                for response in responses
            ):
                uncovered.add((method.upper(), path))
    assert uncovered == {
        ("GET", "/health"),
        ("GET", "/metrics"),
        ("GET", "/metrics/latency"),
    }


def test_ui_reports_service_unavailability(
    monkeypatch: pytest.MonkeyPatch, client: httpx.Client
) -> None:

    def unavailable(*args: Any, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("private connection details")

    monkeypatch.setattr(client, "post", unavailable)
    app = policy_screen()
    app.chat_input[0].set_value("What is the fee?").run()
    assert not app.exception
    assert "unavailable" in app.error[0].value
    assert "private" not in app.error[0].value


def test_live_quality_is_hidden_but_datadog_link_and_evaluation_remain(
    monkeypatch: pytest.MonkeyPatch,
    client: httpx.Client,
) -> None:
    answer = AnswerResponse.model_validate(
        client.post("/answer", json={"query": "Question", "as_of": "2026-10-19"}).json()
    )
    metrics = {name: QualityScore(status="scored", value=0.0) for name in METRIC_NAMES}
    for name in ("context_precision", "context_recall"):
        metrics[name] = QualityScore(status="unavailable", reason="Provide a reference answer.")
    evaluate = AsyncMock(
        return_value=EvaluatedAnswerResponse(
            answer=answer,
            quality=AnswerQuality(
                metrics=metrics,
                reference_source="none",
                evaluated_at=datetime.now(UTC),
                datadog_status="queued",
                datadog_url="https://app.datadoghq.com/llm/traces?query=service%3Anexaassist-api",
            ),
        )
    )
    monkeypatch.setattr(answer_api, "answer_with_quality", evaluate)
    app = policy_screen()
    assert app.toggle(key="evaluate_quality").value
    app.text_area(key="quality_reference").input("Reference for this question")
    app.chat_input[0].set_value("Question").run()
    assert not app.exception
    assert not app.metric
    assert not any("RAGAS answer quality" in item.value for item in app.markdown)
    assert not any("Provide a reference" in item.value for item in app.caption)
    assert not any("queued for Datadog" in item.value for item in app.caption)
    assert app.link_button[0].value == "View evaluation in Datadog"
    assert app.text_area(key="quality_reference").value == ""
    assert evaluate.await_args is not None
    assert evaluate.await_args.args[0].reference_answer == "Reference for this question"
    app.run()
    assert evaluate.await_count == 1


def test_chat_history_filters_and_feedback_are_isolated(client: httpx.Client) -> None:
    responses: list[httpx.Response] = []
    client.event_hooks["response"].append(responses.append)
    app = policy_screen()
    app.date_input(key="policy_date").set_value(date(2026, 10, 19))
    app.text_input(key="product_id").input("NEXA-HL")
    app.slider(key="source_count").set_value(3)
    app.chat_input[0].set_value("What is the loan limit?").run()
    first_chat = app.session_state["active_chat"]
    assert len(app.chat_message) == 2
    first_request = responses[0].request.content
    assert b'"product_id":"NEXA-HL"' in first_request
    assert b'"top_k":3' in first_request

    next(widget for widget in app.radio if widget.label == "Was this answer helpful?").set_value(
        "No"
    )
    next(
        widget for widget in app.text_area if widget.label == "Correction or missing evidence"
    ).input("Please check this clause.")
    next(button for button in app.button if button.label == "Send feedback").click().run()
    assert responses[-1].json()["helpful"] is False
    app.chat_input[0].set_value("What about processing fees?").run()
    assert not app.exception
    assert len(app.chat_message) == 4
    assert (
        next(
            widget for widget in app.text_area if widget.label == "Correction or missing evidence"
        ).value
        == ""
    )
    assert (
        next(widget for widget in app.radio if widget.label == "Was this answer helpful?").value
        == "Yes"
    )

    request_count = len(responses)
    app.button(key="new_chat").click().run()
    assert not app.chat_message
    assert len(responses) == request_count
    app.chat_input[0].set_value("Another policy question").run()
    assert len(app.chat_message) == 2
    request_count = len(responses)
    app.button(key=f"chat_{first_chat}").click().run()
    assert not app.exception
    assert len(app.chat_message) == 4
    assert len(responses) == request_count
    assert app.text_input(key="product_id").value == "NEXA-HL"


def test_failed_question_retries_original_scope(
    monkeypatch: pytest.MonkeyPatch, client: httpx.Client
) -> None:
    post = client.post
    monkeypatch.setattr(
        client,
        "post",
        lambda *args, **kwargs: httpx.Response(
            503, request=httpx.Request("POST", "http://testserver/answer")
        ),
    )
    app = policy_screen()
    app.text_input(key="product_id").input("NEXA-HL")
    app.chat_input[0].set_value("What is the limit?").run()
    assert app.error
    app.text_input(key="product_id").input("NEXA-AL").run()
    monkeypatch.setattr(client, "post", post)
    next(button for button in app.button if button.label == "Try again").click().run()
    assert not app.exception
    assert not app.error
    assert len(app.chat_message) == 2
    active = app.session_state["active_chat"]
    assert (
        app.session_state["conversations"][active]["turns"][0]["request"]["product_id"] == "NEXA-HL"
    )


def test_suggestion_and_blank_input(client: httpx.Client) -> None:
    responses: list[httpx.Response] = []
    client.event_hooks["response"].append(responses.append)
    app = policy_screen()
    app.chat_input[0].set_value("   ").run()
    assert app.warning
    assert not responses
    next(button for button in app.button if button.label == "Home loan limits").click().run()
    assert not app.exception
    assert len(app.chat_message) == 2
    assert len(responses) == 1
    assert not any(button.label == "Home loan limits" for button in app.button)


def test_reference_and_correction_survive_immediate_submission(client: httpx.Client) -> None:
    import json

    responses: list[httpx.Response] = []
    client.event_hooks["response"].append(responses.append)
    app = policy_screen()
    reference = "Expected answer pasted immediately before sending."
    app.date_input[0].set_value(date(2026, 10, 19))
    app.text_area(key="quality_reference").input(reference)
    app.chat_input[0].set_value("Check this policy").run()
    evaluated = next(r for r in responses if r.request.url.path == "/answer/evaluated")
    assert json.loads(evaluated.request.content)["reference_answer"] == reference
    assert app.text_area(key="quality_reference").value == ""
    correction = "Correction pasted immediately before submitting feedback."
    next(w for w in app.text_area if w.label == "Correction or missing evidence").input(correction)
    next(b for b in app.button if b.label == "Send feedback").click().run()
    submitted = next(r for r in responses if r.request.url.path == "/feedback")
    assert submitted.json()["correction"] == correction
    assert json.loads(submitted.request.content)["submission_id"]
    assert not app.exception


def test_changing_reviewer_credentials_clears_loaded_records(client: httpx.Client) -> None:
    app = policy_screen()
    app.radio(key="workspace").set_value("Review feedback").run()
    app.text_input(key="reviewer_token").input("reviewer-test-token-000000000000000000").run()
    app.session_state["feedback_records"] = [{"private_test_record": True}]
    app.text_input(key="reviewer_token").input("wrong token").run()
    assert not app.exception
    with pytest.raises(KeyError):
        app.session_state["feedback_records"]
