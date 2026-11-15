from __future__ import annotations

import asyncio
import json
import runpy
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import RunnableLambda
from mcp.shared.exceptions import MCPError

from app.core.config import AgentSettings, Settings
from app.mcp import client as policy_client
from app.mcp import server as policy_server
from app.models.agent import AgentRequest, IntentDecision
from app.models.answer import AnswerDraft
from app.models.retrieval import RetrieveRequest
from app.services import agent as agent_service
from app.services import agent_mcp_retrieval as mcp_retrieval

REQUEST = {"query": "Maximum NexaHome loan amount?", "as_of": "2026-10-01", "product_id": "NEXA-HL"}
RESPONSE = {
    "as_of": "2026-10-01",
    "results": [],
    "amendments": [
        {
            "rank": 1,
            "score": 0.9,
            "text": "An amendment",
            "parent_text": "The complete amendment",
            "citation": {
                "doc_id": "CIR-2026-07",
                "title": "Prepayment circular",
                "policy_version": "1.0",
                "clause": "5.2",
                "effective_date": "2026-07-01",
            },
        }
    ],
    "timings": {"search_ms": 1.0, "parent_ms": 2.0, "total_ms": 3.0},
}


def test_real_protocol_discovery_contract_prompt_and_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        assert request.url.path == "/retrieve"
        return httpx.Response(200, json=RESPONSE)

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="http://rag/", transport=httpx.MockTransport(respond)
        ) as http:
            server = policy_server.build_mcp_server(http)
            monkeypatch.setattr(policy_client, "StdioTransport", lambda **kwargs: server)
            report = await policy_client.inspect_policy(RetrieveRequest.model_validate(REQUEST))
            assert len(calls) == 1
            assert calls[0] == RetrieveRequest.model_validate(REQUEST).model_dump(mode="json")
            assert report["retrieval"] == RESPONSE
            assert report["resources"] == ["nexa://retrieval/contract/v1"]
            assert report["prompts"] == ["explain_policy"]
            contract = report["contract"]
            assert contract["request_schema"] == RetrieveRequest.model_json_schema()
            assert contract["request_schema"]["additionalProperties"] is False
            assert report["tools"][0]["annotations"]["readOnlyHint"] is True
            assert "request" in report["tools"][0]["inputSchema"]["properties"]
            prompt = report["explanation_prompt"]["messages"][0]["content"]["text"]
            assert "2026-10-01" in prompt and "Cite the document" in prompt

            async with Client(server) as client:
                for arguments in [
                    {"request": {"query": "x"}},
                    {"request": {**REQUEST, "as_of": "not-a-date"}},
                    {"request": {**REQUEST, "top_k": 21}},
                    {"request": {**REQUEST, "customer_ids": ["NXC-0010"]}},
                    {"request": REQUEST, "can_approve": True},
                ]:
                    with pytest.raises(ToolError):
                        await client.call_tool("retrieve_policy", arguments)
                with pytest.raises(ToolError):
                    await client.call_tool("submit_service_request", {})
                with pytest.raises(MCPError, match="explicit as_of date"):
                    await client.get_prompt("explain_policy", {"request_json": "{}"})
            assert len(calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["http", "timeout", "date", "shape"])
def test_upstream_failures_are_protocol_errors_without_details(failure: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("private diagnostic detail", request=request)
        if failure == "http":
            return httpx.Response(503, text="private diagnostic detail")
        return httpx.Response(
            200, json={**RESPONSE, "as_of": "2026-09-01"} if failure == "date" else {}
        )

    async def run() -> None:
        async with (
            httpx.AsyncClient(
                base_url="http://rag/", transport=httpx.MockTransport(respond)
            ) as http,
            Client(policy_server.build_mcp_server(http)) as client,
        ):
            with pytest.raises(ToolError, match="Policy retrieval failed") as error:
                await client.call_tool("retrieve_policy", {"request": REQUEST})
            assert "private diagnostic" not in str(error.value)

    asyncio.run(run())


def test_client_rejects_unapproved_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        async with httpx.AsyncClient(base_url="http://rag/") as http:
            server = policy_server.build_mcp_server(http)

            @server.tool
            def unexpected_action() -> str:
                return "unapproved"

            monkeypatch.setattr(policy_client, "StdioTransport", lambda **kwargs: server)
            with pytest.raises(ValueError, match="only the approved"):
                await policy_client.inspect_policy(RetrieveRequest.model_validate(REQUEST))

    asyncio.run(run())


def test_client_rejects_wrong_date_from_a_different_server(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        async with httpx.AsyncClient(base_url="http://rag/") as http:
            server = policy_server.build_mcp_server(http)

            @server.tool(name="retrieve_policy")
            def wrong_date(request: dict) -> dict:
                return {**RESPONSE, "as_of": "2026-09-01"}

            monkeypatch.setattr(policy_client, "StdioTransport", lambda **kwargs: server)
            with pytest.raises(ValueError, match="MCP response date"):
                await policy_client.inspect_policy(RetrieveRequest.model_validate(REQUEST))

    asyncio.run(run())


def test_stdio_startup_keeps_logs_off_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    server = Mock(run_async=AsyncMock())
    logging = Mock()
    configure = Mock()
    monkeypatch.setattr(policy_server, "get_agent_settings", lambda: AgentSettings(_env_file=None))
    monkeypatch.setattr(policy_server, "configure_logging", logging)
    monkeypatch.setattr(policy_server.structlog, "configure", configure)
    monkeypatch.setattr(policy_server, "build_mcp_server", lambda client: server)
    asyncio.run(policy_server.main())
    server.run_async.assert_awaited_once_with(transport="stdio", show_banner=False)
    logging.assert_called_once_with("INFO")
    assert configure.call_args is not None
    assert "logger_factory" in configure.call_args.kwargs


def test_client_cli_writes_the_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    output = tmp_path / "demo.json"
    inspector = AsyncMock(return_value={"retrieval": RESPONSE})
    monkeypatch.setattr(policy_client, "inspect_policy", inspector)
    monkeypatch.setattr(
        policy_client.sys,
        "argv",
        ["client", "--query", "Limits?", "--as-of", "2026-10-01", "--output", str(output)],
    )
    policy_client.main()
    assert json.loads(output.read_text()) == {"retrieval": RESPONSE}
    assert inspector.await_args is not None
    assert inspector.await_args.args[0].as_of == date(2026, 10, 1)


def test_client_entrypoint_exposes_cli_help(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy_client.sys, "argv", ["client", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(policy_client.__file__, run_name="__main__")
    assert exit_info.value.code == 0


def test_server_entrypoint_runs_async_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Mock(side_effect=lambda coroutine: coroutine.close())
    monkeypatch.setattr(asyncio, "run", runner)
    runpy.run_path(policy_server.__file__, run_name="__main__")
    runner.assert_called_once()


@pytest.mark.parametrize("intent", ["policy_question", "missing_information", "forbidden"])
@pytest.mark.parametrize("invalid_quote", [False, True])
def test_agent_uses_mcp_only_after_scope_and_guardrails(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, intent: str, invalid_quote: bool
) -> None:
    calls: list[dict] = []
    prompts: list[str] = []
    transport_factory = Mock()

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=RESPONSE)

    def structured(schema: type, **kwargs: Any) -> RunnableLambda:
        def output(prompt: Any) -> Any:
            if schema is IntentDecision:
                return IntentDecision(
                    intent=intent,
                    product_id=None,
                    as_of=None,
                    customer_id=None,
                    action=None,
                    product_required=True,
                    missing_fields=["as_of"] if intent == "missing_information" else [],
                )
            prompts.append(str(prompt.to_messages()[-1].content))
            return AnswerDraft.model_validate(
                {
                    "status": "answered",
                    "answer": "An amendment [1].",
                    "evidence": [
                        {
                            "evidence_id": 1,
                            "quote": "Invented quote"
                            if invalid_quote
                            else "The complete amendment",
                        }
                    ],
                }
            )

        return RunnableLambda(output)

    llm = Mock(spec=BaseChatModel)
    llm.with_structured_output.side_effect = structured
    monkeypatch.setattr(agent_service, "get_chat_model", lambda _: llm)
    config = AgentSettings(_env_file=None, agent_retrieval_transport="mcp")
    request = AgentRequest.model_validate({**REQUEST, "jurisdiction": "IN", "top_k": 7})
    if intent == "missing_information":
        request = request.model_copy(update={"as_of": None})

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="http://rag/", transport=httpx.MockTransport(respond)
        ) as http:
            transport_factory.return_value = policy_server.build_mcp_server(http)
            monkeypatch.setattr(mcp_retrieval, "StdioTransport", transport_factory)
            result = await agent_service.orchestrate(request, settings, config, http)
            if intent == "policy_question":
                assert len(calls) == 1
                assert calls[0] == {**REQUEST, "jurisdiction": "IN", "top_k": 7}
                assert result.status == ("insufficient_evidence" if invalid_quote else "answered")
                assert result.answer is not None
                if invalid_quote:
                    assert result.answer.citations == []
                else:
                    assert result.answer.citations[0].matched_child.doc_id == "CIR-2026-07"
                assert "The complete amendment" in prompts[0]
                assert transport_factory.call_args is not None
                kwargs = transport_factory.call_args.kwargs
                assert kwargs["keep_alive"] is False
                assert kwargs["env"] == {
                    "RAG_BASE_URL": str(config.rag_base_url),
                    "AGENT_HTTP_TIMEOUT_SECONDS": str(config.agent_http_timeout_seconds),
                }
            else:
                assert result.status == (
                    "forbidden" if intent == "forbidden" else "needs_clarification"
                )
                transport_factory.assert_not_called()
                assert calls == [] and prompts == []

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["http", "date", "schema", "discovery", "timeout"])
def test_mcp_agent_adapter_fails_safely(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="private upstream diagnostic")

    async def run() -> None:
        async with httpx.AsyncClient(
            base_url="http://rag/", transport=httpx.MockTransport(respond)
        ) as http:
            server = policy_server.build_mcp_server(http)
            if failure != "http":

                @server.tool(name="retrieve_policy")
                async def bad_response(request: dict) -> dict:
                    if failure == "timeout":
                        await asyncio.sleep(1)
                    if failure == "schema":
                        return {}
                    return {**RESPONSE, "as_of": "2025-01-01"}

            if failure == "discovery":

                @server.tool
                def unapproved_action() -> str:
                    raise AssertionError("An unapproved tool must never execute")

            monkeypatch.setattr(mcp_retrieval, "StdioTransport", lambda **kwargs: server)
            config = AgentSettings(_env_file=None, agent_http_timeout_seconds=0.2)
            with pytest.raises(ValueError, match="^MCP policy retrieval unavailable$"):
                await mcp_retrieval.retrieve_over_mcp(
                    RetrieveRequest.model_validate(REQUEST), config
                )

    asyncio.run(run())


@pytest.mark.parametrize(
    "error",
    [
        MCPError(-1, "private detail"),
        ToolError("private detail"),
        OSError("private detail"),
        RuntimeError("private detail"),
    ],
)
def test_mcp_connection_errors_are_safe(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    fake = Mock()
    fake.__aenter__ = AsyncMock(side_effect=error)
    fake.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(mcp_retrieval, "Client", lambda *args, **kwargs: fake)
    with pytest.raises(ValueError, match="^MCP policy retrieval unavailable$"):
        asyncio.run(
            mcp_retrieval.retrieve_over_mcp(
                RetrieveRequest.model_validate(REQUEST), AgentSettings(_env_file=None)
            )
        )


def test_retrieval_transport_setting_is_explicit() -> None:
    assert AgentSettings(_env_file=None).agent_retrieval_transport == "http"
    with pytest.raises(ValueError):
        AgentSettings(_env_file=None, agent_retrieval_transport="untrusted")
