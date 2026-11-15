from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from app.api.dependencies import banking_client
from app.core.config import AgentSettings
from app.mcp import banking_server
from app.models.banking_mcp import BANKING_TOOLS, ServiceWrite, TransactionRead, write_grant
from app.models.validation import ValidationAccess
from app.services import banking_mcp

ACCESS = ValidationAccess(customer_ids=frozenset({"NXC-0010"}), principal_id="owner")
PROPOSAL = {
    "customer_id": "NXC-0010",
    "request_type": "CALLBACK",
    "notes": "Please arrange a callback.",
}
KEY = "test-operation-key-001"


@pytest.fixture
def bank():
    name = "mcp_bank_" + uuid4().hex
    spec = importlib.util.spec_from_file_location(
        name, "nexa_synthetic_data/api/mock_server/server.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_all_read_tools_scope_ownership_and_pagination(bank) -> None:
    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=bank.app),
            base_url="http://bank/",
            headers={"Authorization": "Bearer synthetic"},
        ) as http:
            server = banking_server.build_banking_server(http, ACCESS)
            async with Client(server) as client:
                tools = await client.list_tools()
                assert {t.name for t in tools} == BANKING_TOOLS
                for name in banking_server.CUSTOMER_TOOLS:
                    result = await client.call_tool(name, {"request": {"customer_id": "NXC-0010"}})
                    assert result.structured_content is not None
                    assert result.structured_content["body"]["customer_id"] == "NXC-0010"
                    with pytest.raises(ToolError):
                        await client.call_tool(name, {"request": {"customer_id": "NXC-0023"}})
                accounts = bank.CUSTOMERS["NXC-0010"]["accounts"]
                account = next(a["account_id"] for a in accounts if a["type"] != "loan")
                result = await client.call_tool(
                    "list_transactions",
                    {
                        "request": {
                            "customer_id": "NXC-0010",
                            "account_id": account,
                            "limit": 1,
                            "from_date": "2025-01-01",
                            "to_date": "2027-01-01",
                            "cursor": "0",
                        }
                    },
                )
                assert result.structured_content is not None
                assert len(result.structured_content["body"]["items"]) <= 1
                for arguments in [
                    {"customer_id": "NXC-0010", "account_id": "foreign-account"},
                    {"customer_id": "NXC-0010", "account_id": account, "limit": 201},
                    {"customer_id": "NXC-0010", "account_id": account, "cursor": "invalid"},
                ]:
                    with pytest.raises(ToolError):
                        await client.call_tool("list_transactions", {"request": arguments})
                with pytest.raises(ToolError):
                    await client.call_tool(
                        "create_service_request",
                        {"request": {"payload": PROPOSAL, "idempotency_key": KEY}},
                    )
                with pytest.raises(ToolError):
                    await client.call_tool("_mock_reset", {})
                assert bank.COUNTERS["sr"] == 0

    asyncio.run(run())


def test_write_grants_replay_conflicts_and_all_case_tools(bank) -> None:
    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=bank.app),
            base_url="http://bank/",
            headers={"Authorization": "Bearer synthetic"},
        ) as http:

            async def call(name, arguments):
                async with Client(
                    banking_server.build_banking_server(http, ACCESS, write_grant(name, arguments))
                ) as client:
                    return await client.call_tool(name, {"request": arguments})

            case = {
                "payload": {
                    "customer_id": "NXC-0010",
                    "category": "COMPLAINT",
                    "priority": "NORMAL",
                    "summary": "Synthetic complaint",
                    "related_application_id": None,
                    "channel": "ASSISTANT",
                },
                "idempotency_key": KEY,
            }
            created = await call("create_case", case)
            assert created.structured_content is not None
            case_id = created.structured_content["body"]["case_id"]
            case_read = {"customer_id": "NXC-0010", "case_id": case_id}
            async with Client(banking_server.build_banking_server(http, ACCESS)) as client:
                read = await client.call_tool("get_case", {"request": case_read})
                assert (
                    read.structured_content is not None
                    and "etag" in read.structured_content["headers"]
                )
                bank.CASES[case_id]["customer_id"] = "NXC-0023"
                with pytest.raises(ToolError):
                    await client.call_tool("get_case", {"request": case_read})
                bank.CASES[case_id]["customer_id"] = "NXC-0010"
            note = {**case_read, "payload": {"text": "Synthetic case note"}, "idempotency_key": KEY}
            await call("add_case_note", note)
            await call("add_case_note", note)
            assert bank.COUNTERS["note"] == 1
            escalation = {
                **case_read,
                "payload": {
                    "reason": "COMPLAINT",
                    "summary": "Please review complaint",
                    "preferred_contact": "CALLBACK",
                },
                "idempotency_key": KEY,
            }
            await call("escalate_case", escalation)
            assert bank.COUNTERS["esc"] == 1
            sr = ServiceWrite.model_validate(
                {"payload": PROPOSAL, "idempotency_key": KEY}
            ).model_dump(mode="json")
            first = await call("create_service_request", sr)
            replay = await call("create_service_request", sr)
            assert first.structured_content is not None and replay.structured_content is not None
            assert first.structured_content["body"] == replay.structured_content["body"]
            assert replay.structured_content["headers"]["idempotent-replayed"] == "true"
            assert bank.COUNTERS["sr"] == 1
            async with Client(
                banking_server.build_banking_server(
                    http, ACCESS, write_grant("create_service_request", sr)
                )
            ) as client:
                for changed in [
                    {**sr, "idempotency_key": "a-different-operation"},
                    {**sr, "payload": {**sr["payload"], "notes": "Altered notes"}},
                ]:
                    with pytest.raises(ToolError):
                        await client.call_tool("create_service_request", {"request": changed})
            conflict = await call(
                "create_service_request",
                {**sr, "payload": {**sr["payload"], "notes": "Altered notes"}},
            )
            assert (
                conflict.structured_content is not None
                and conflict.structured_content["status_code"] == 422
            )

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["timeout", "http", "shape", "identity", "accounts", "receipt"])
def test_server_errors_are_safe(kind: str) -> None:
    def respond(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("private upstream diagnostic")
        if kind == "http":
            return httpx.Response(503, headers={"Retry-After": "1"}, json={"private": "diagnostic"})
        if kind == "shape":
            return httpx.Response(200, json=[])
        if kind == "accounts":
            return httpx.Response(200, json={"customer_id": "NXC-0010"})
        if kind == "receipt":
            return httpx.Response(
                201,
                json={
                    "service_request_id": "NXSR-001",
                    "customer_id": "NXC-0010",
                    "request_type": "STATEMENT_COPY",
                    "loan_account_id": None,
                    "status": "RECEIVED",
                    "created_at": "2026-11-06T00:00:00Z",
                },
            )
        return httpx.Response(200, json={"customer_id": "NXC-0023"})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="http://bank/"
        ) as http:
            sr = ServiceWrite.model_validate(
                {"payload": PROPOSAL, "idempotency_key": KEY}
            ).model_dump(mode="json")
            async with Client(
                banking_server.build_banking_server(
                    http, ACCESS, write_grant("create_service_request", sr)
                )
            ) as client:
                name = "create_service_request" if kind == "receipt" else "list_accounts"
                args = sr if kind == "receipt" else {"customer_id": "NXC-0010"}
                result = await client.call_tool(name, {"request": args}, raise_on_error=False)
                if kind == "http":
                    assert result.structured_content is not None
                    assert result.structured_content["status_code"] == 503
                    assert result.structured_content["headers"]["retry-after"] == "1"
                else:
                    assert result.is_error
                assert "private upstream diagnostic" not in str(result)

    asyncio.run(run())


def test_agent_dependency_routes_over_protocol_and_retains_receipt(bank, monkeypatch) -> None:
    config = AgentSettings(
        _env_file=None, banking_api_token="synthetic", banking_base_url="http://bank/"
    )
    constructions = []

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=bank.app),
            base_url="http://bank/",
            headers={"Authorization": "Bearer synthetic"},
        ) as backend:

            def transport(**kwargs):
                constructions.append(kwargs)
                env = kwargs["env"]
                scope = ValidationAccess(customer_ids=json.loads(env["BANKING_MCP_CUSTOMER_IDS"]))
                return banking_server.build_banking_server(
                    backend, scope, env["BANKING_MCP_WRITE_GRANT"] or None
                )

            monkeypatch.setattr(banking_mcp, "StdioTransport", transport)
            async for client in banking_client(config, ACCESS):
                assert client is not None
                accounts = await client.get("v1/customers/NXC-0010/accounts")
                assert accounts.json()["customer_id"] == "NXC-0010"
                receipt = await client.post(
                    "v1/service-requests", json=PROPOSAL, headers={"Idempotency-Key": KEY}
                )
                assert receipt.status_code == 201
                assert receipt.json()["request_type"] == "CALLBACK"
                assert len(constructions) == 2
                assert all(item["keep_alive"] is False for item in constructions)
                assert constructions[0]["env"]["BANKING_MCP_WRITE_GRANT"] == ""
                assert bank.COUNTERS["sr"] == 1

    asyncio.run(run())


def test_transport_maps_the_whole_banking_contract(bank, monkeypatch) -> None:
    config = AgentSettings(
        _env_file=None, banking_api_token="synthetic", banking_base_url="http://bank/prefix/"
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=bank.app),
            base_url="http://bank/",
            headers={"Authorization": "Bearer synthetic"},
        ) as backend:

            def transport(**kwargs):
                env = kwargs["env"]
                return banking_server.build_banking_server(
                    backend, ACCESS, env["BANKING_MCP_WRITE_GRANT"] or None
                )

            monkeypatch.setattr(banking_mcp, "StdioTransport", transport)
            async for client in banking_client(config, ACCESS):
                assert client is not None
                for suffix in banking_server.CUSTOMER_TOOLS.values():
                    response = await client.get("v1/customers/NXC-0010" + suffix)
                    response.raise_for_status()
                account = next(
                    a["account_id"]
                    for a in bank.CUSTOMERS["NXC-0010"]["accounts"]
                    if a["type"] != "loan"
                )
                response = await client.get(
                    f"v1/accounts/{account}/transactions",
                    params={"customer_id": "NXC-0010", "limit": 1},
                )
                assert response.status_code == 200
                created = await client.post(
                    "v1/cases",
                    headers={"Idempotency-Key": KEY},
                    json={
                        "customer_id": "NXC-0010",
                        "category": "COMPLAINT",
                        "priority": "NORMAL",
                        "summary": "Synthetic complaint",
                    },
                )
                case_id = created.json()["case_id"]
                read = await client.get(f"v1/cases/{case_id}", params={"customer_id": "NXC-0010"})
                assert read.json()["case_id"] == case_id
                for suffix, payload in [
                    ("notes", {"text": "A case note"}),
                    (
                        "escalations",
                        {"reason": "COMPLAINT", "summary": "Please review this complaint"},
                    ),
                ]:
                    response = await client.post(
                        f"v1/cases/{case_id}/{suffix}",
                        params={"customer_id": "NXC-0010"},
                        headers={"Idempotency-Key": KEY},
                        json=payload,
                    )
                    assert response.status_code == 201
                for method, path in [
                    ("DELETE", "v1/cases/NXCASE-000001"),
                    ("POST", "v1/transfers"),
                    ("GET", "v1/customers/NXC-0010/unknown"),
                    ("GET", "/wrong-prefix"),
                ]:
                    with pytest.raises(httpx.ConnectError, match="could not be confirmed"):
                        await client.request(method, path, json={})

    asyncio.run(run())


def test_transport_requires_scope_and_rejects_surprise_discovery(monkeypatch) -> None:
    config = AgentSettings(_env_file=None, banking_base_url="http://bank/")
    fake = Mock()
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=False)
    fake.list_tools = AsyncMock(return_value=[Mock(name="surprise")])
    monkeypatch.setattr(banking_mcp, "Client", lambda *args, **kwargs: fake)

    async def run():
        for access in [None, ACCESS]:
            async with httpx.AsyncClient(
                transport=banking_mcp.BankingMCPTransport(config, access), base_url="http://bank/"
            ) as client:
                with pytest.raises(httpx.ConnectError):
                    await client.get("v1/customers/NXC-0010/accounts")

    asyncio.run(run())
    with pytest.raises(ValueError, match="start date"):
        TransactionRead(
            customer_id="NXC-0010",
            account_id="account",
            from_date="2026-11-06",
            to_date="2026-01-01",
        )


@pytest.mark.parametrize("failure", ["account_http", "case_http", "transactions"])
def test_related_resource_failures_do_not_trigger_writes(failure) -> None:
    calls = []

    def respond(request):
        calls.append(request)
        if failure == "transactions" and request.url.path.endswith("/accounts"):
            return httpx.Response(
                200,
                json={
                    "customer_id": "NXC-0010",
                    "accounts": [{"account_id": "owned"}],
                    "loans": [],
                },
            )
        if failure == "transactions":
            return httpx.Response(200, json={"items": "invalid"})
        return httpx.Response(404, json={"private": "not found"})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="http://bank/"
        ) as http:
            args = {
                "customer_id": "NXC-0010",
                "case_id": "NXCASE-000001",
                "payload": {"text": "Case note"},
                "idempotency_key": KEY,
            }
            async with Client(
                banking_server.build_banking_server(
                    http, ACCESS, write_grant("add_case_note", args)
                )
            ) as client:
                if failure == "case_http":
                    result = await client.call_tool("add_case_note", {"request": args})
                else:
                    result = await client.call_tool(
                        "list_transactions",
                        {"request": {"customer_id": "NXC-0010", "account_id": "owned"}},
                        raise_on_error=False,
                    )
                if failure == "transactions":
                    assert result.is_error
                else:
                    assert (
                        result.structured_content is not None
                        and result.structured_content["status_code"] == 404
                    )
                assert all(req.method == "GET" for req in calls)

    asyncio.run(run())


def test_stdio_startup_and_entrypoint(monkeypatch) -> None:
    import runpy

    server = Mock(run_async=AsyncMock())
    monkeypatch.setattr(banking_server, "build_banking_server", lambda *args: server)
    monkeypatch.setattr(banking_server, "configure_logging", Mock())
    monkeypatch.setattr(banking_server.structlog, "configure", Mock())
    monkeypatch.setattr(banking_server, "get_agent_settings", lambda: AgentSettings(_env_file=None))
    with pytest.raises(ValueError, match="credentials"):
        asyncio.run(banking_server.main())
    for grant in [None, "bound-grant"]:
        monkeypatch.setattr(
            banking_server,
            "get_agent_settings",
            lambda grant=grant: AgentSettings(
                _env_file=None, banking_api_token="synthetic", banking_mcp_write_grant=grant
            ),
        )
        asyncio.run(banking_server.main())
    server.run_async.assert_awaited_with(transport="stdio", show_banner=False)
    runner = Mock(side_effect=lambda coroutine: coroutine.close())
    monkeypatch.setattr(asyncio, "run", runner)
    runpy.run_path(banking_server.__file__, run_name="__main__")
    runner.assert_called_once()


@pytest.mark.parametrize("failure", ["schema", "timeout", "path"])
def test_adapter_deadline_and_invalid_envelope_fail_without_fallback(monkeypatch, failure) -> None:
    from types import SimpleNamespace

    config = AgentSettings(
        _env_file=None, banking_base_url="http://bank/prefix/", agent_http_timeout_seconds=0.01
    )
    fake = Mock()
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=False)

    async def discover():
        if failure == "timeout":
            await asyncio.sleep(0.1)
        return [SimpleNamespace(name=name) for name in BANKING_TOOLS]

    fake.list_tools = discover
    fake.call_tool = AsyncMock(return_value=SimpleNamespace(is_error=False, structured_content={}))
    monkeypatch.setattr(banking_mcp, "Client", lambda *args, **kwargs: fake)

    async def run():
        transport = banking_mcp.BankingMCPTransport(config, ACCESS)
        url = (
            "http://bank/wrong-prefix"
            if failure == "path"
            else "http://bank/prefix/v1/customers/NXC-0010/accounts"
        )
        with pytest.raises(httpx.ConnectError, match="could not be confirmed"):
            await transport.handle_async_request(httpx.Request("GET", url))

    asyncio.run(run())


def test_http_protocol_scopes_grants_and_replays(bank, monkeypatch) -> None:
    import httpx2
    from fastmcp.client.transports import StreamableHttpTransport
    from starlette.middleware import Middleware

    from app.mcp.banking_http import BankingHTTPAuth, request_session, signed_headers

    config = AgentSettings(
        _env_file=None,
        banking_api_token="synthetic",
        banking_base_url="http://bank/",
        banking_mcp_transport="http",
        banking_mcp_token="t" * 48,
        banking_mcp_signing_key="s" * 48,
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=bank.app),
            base_url="http://bank/",
            headers={"Authorization": "Bearer synthetic"},
        ) as backend:
            server = banking_server.build_banking_server(
                backend,
                ValidationAccess(customer_ids=frozenset()),
                session_provider=request_session,
            )
            app = server.http_app(
                path="/mcp",
                stateless_http=True,
                json_response=True,
                middleware=[Middleware(BankingHTTPAuth, settings=config)],
            )

            def factory(**kwargs):
                return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), **kwargs)

            async with app.router.lifespan_context(app):
                monkeypatch.setattr(banking_mcp, "mcp_http_client", factory)

                async def exercise(cid):
                    access = ValidationAccess(customer_ids=frozenset({cid}), principal_id=cid)
                    async for client in banking_client(config, access):
                        assert client is not None
                        result = await client.get(f"v1/customers/{cid}/accounts")
                        assert result.json()["customer_id"] == cid
                        other = "NXC-0023" if cid == "NXC-0010" else "NXC-0010"
                        with pytest.raises(httpx.ConnectError):
                            await client.get(f"v1/customers/{other}/accounts")

                await asyncio.gather(exercise("NXC-0010"), exercise("NXC-0023"))
                async for client in banking_client(config, ACCESS):
                    assert client is not None
                    first = await client.post(
                        "v1/service-requests", json=PROPOSAL, headers={"Idempotency-Key": KEY}
                    )
                    replay = await client.post(
                        "v1/service-requests", json=PROPOSAL, headers={"Idempotency-Key": KEY}
                    )
                    assert first.status_code == 201 and first.json() == replay.json()
                    assert bank.COUNTERS["sr"] == 1
                sr = ServiceWrite.model_validate(
                    {"payload": PROPOSAL, "idempotency_key": KEY}
                ).model_dump(mode="json")
                # A valid read context cannot turn itself into a write.
                transport = StreamableHttpTransport(
                    str(config.banking_mcp_url),
                    headers=signed_headers(config, ACCESS, None),
                    httpx_client_factory=factory,
                )
                async with Client(transport) as client:
                    with pytest.raises(ToolError):
                        await client.call_tool("create_service_request", {"request": sr})
                changed = {**sr, "payload": {**sr["payload"], "notes": "Changed notes"}}
                transport = StreamableHttpTransport(
                    str(config.banking_mcp_url),
                    headers=signed_headers(
                        config, ACCESS, write_grant("create_service_request", sr)
                    ),
                    httpx_client_factory=factory,
                )
                async with Client(transport) as client:
                    with pytest.raises(ToolError):
                        await client.call_tool("create_service_request", {"request": changed})
                # Token-only Inspector sessions are bounded by server configuration and read-only.
                transport = StreamableHttpTransport(
                    str(config.banking_mcp_url),
                    headers={"Authorization": "Bearer " + "t" * 48},
                    httpx_client_factory=factory,
                )
                async with Client(transport) as client:
                    with pytest.raises(ToolError, match="BANKING_MCP_CUSTOMER_IDS"):
                        await client.call_tool(
                            "list_accounts", {"request": {"customer_id": "NXC-0010"}}
                        )
                config.banking_mcp_customer_ids = frozenset({"NXC-0010"})
                async with Client(transport) as client:
                    result = await client.call_tool(
                        "list_accounts", {"request": {"customer_id": "NXC-0010"}}
                    )
                    assert result.structured_content is not None
                    with pytest.raises(ToolError, match="owner/reviewer workflow"):
                        await client.call_tool("create_service_request", {"request": sr})
                assert bank.COUNTERS["sr"] == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure",
    ["missing", "token", "signature", "expired", "future", "oversized", "schema", "backend"],
)
def test_http_auth_rejects_bad_context_before_protocol(failure, monkeypatch) -> None:
    import hashlib
    import hmac
    import time

    from app.mcp.banking_http import BankingHTTPAuth, signed_headers

    config = AgentSettings(
        _env_file=None, banking_mcp_token="t" * 48, banking_mcp_signing_key="s" * 48
    )
    headers = signed_headers(config, ACCESS, None)
    if failure == "missing":
        headers = {}
    if failure == "token":
        headers["Authorization"] = "Bearer incorrect"
    if failure == "signature":
        headers["X-Nexa-Banking-Signature"] = "invalid"
    if failure in ("expired", "future", "oversized", "schema", "backend"):
        data = json.loads(headers["X-Nexa-Banking-Context"])
        if failure in ("expired", "future"):
            data["expires_at"] = int(time.time()) + (-1 if failure == "expired" else 3600)
        if failure == "backend":
            data["backend_identity"] = "0" * 64
        if failure == "schema":
            data["extra_permission"] = True
        headers["X-Nexa-Banking-Context"] = (
            json.dumps(data) if failure != "oversized" else "x" * 17000
        )
        headers["X-Nexa-Banking-Signature"] = hmac.new(
            ("s" * 48).encode(), headers["X-Nexa-Banking-Context"].encode(), hashlib.sha256
        ).hexdigest()
    handler = AsyncMock()
    app = BankingHTTPAuth(handler, config)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://mcp/"
        ) as client:
            response = await client.post("/mcp", headers=headers, json={})
            assert response.status_code == (401 if failure in ("missing", "token") else 403)
            assert "detail" in response.json()

    asyncio.run(run())
    handler.assert_not_called()


def test_http_auth_lifespan_credentials_and_redirect_policy() -> None:
    from app.mcp.banking_http import BankingHTTPAuth, signed_headers

    settings = AgentSettings(_env_file=None)
    with pytest.raises(ValueError, match="credentials"):
        signed_headers(settings, ACCESS, None)
    with pytest.raises(ValueError, match="credentials"):
        BankingHTTPAuth(AsyncMock(), settings)
    handler = AsyncMock()
    middleware = BankingHTTPAuth(
        handler,
        AgentSettings(_env_file=None, banking_mcp_token="t" * 48, banking_mcp_signing_key="s" * 48),
    )
    asyncio.run(middleware({"type": "lifespan"}, AsyncMock(), AsyncMock()))
    handler.assert_awaited_once()
    client = banking_mcp.mcp_http_client(follow_redirects=True)
    assert not client.follow_redirects
    asyncio.run(client.aclose())


def test_http_server_startup_requires_auth_and_uses_request_scope(monkeypatch) -> None:
    server = Mock(run_async=AsyncMock())
    builder = Mock(return_value=server)
    monkeypatch.setattr(banking_server, "build_banking_server", builder)
    monkeypatch.setattr(banking_server, "configure_logging", Mock())
    monkeypatch.setattr(banking_server.structlog, "configure", Mock())
    config = AgentSettings(
        _env_file=None, banking_api_token="synthetic", banking_mcp_transport="http"
    )
    monkeypatch.setattr(banking_server, "get_agent_settings", lambda: config)
    with pytest.raises(ValueError, match="credentials"):
        asyncio.run(banking_server.main())
    config = AgentSettings(
        _env_file=None,
        banking_api_token="synthetic",
        banking_mcp_transport="http",
        banking_mcp_token="t" * 48,
        banking_mcp_signing_key="s" * 48,
    )
    asyncio.run(banking_server.main())
    assert server.run_async.await_args.kwargs["transport"] == "http"
    assert server.run_async.await_args.kwargs["host"] == "127.0.0.1"
    assert server.run_async.await_args.kwargs["stateless_http"] is True
    assert "session_provider" in builder.call_args.kwargs


@pytest.mark.parametrize("mismatch", ["account", "before", "after", "oversize", "duplicate"])
def test_direct_transaction_tool_rejects_rows_outside_the_authorized_query(mismatch: str) -> None:
    row = {
        "txn_id": "one",
        "account_id": "owned",
        "txn_date": "2026-11-05",
        "amount": 1,
        "direction": "debit",
        "channel": "branch",
        "narration": "private transaction text",
        "category": "other",
        "balance_after": 9,
    }
    if mismatch == "account":
        row["account_id"] = "foreign"
    elif mismatch in {"before", "after"}:
        row["txn_date"] = "2026-10-31" if mismatch == "before" else "2026-11-15"
    rows = [row, {**row, "txn_id": "two"}] if mismatch == "oversize" else [row]
    if mismatch == "duplicate":
        rows = [row, row]

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        body = (
            {"customer_id": "NXC-0010", "accounts": [{"account_id": "owned"}], "loans": []}
            if request.url.path.endswith("/accounts")
            else {"items": rows, "next_cursor": None}
        )
        return httpx.Response(200, json=body)

    async def run() -> None:
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(respond), base_url="http://bank/"
            ) as http,
            Client(banking_server.build_banking_server(http, ACCESS)) as client,
        ):
            result = await client.call_tool(
                "list_transactions",
                {
                    "request": {
                        "customer_id": "NXC-0010",
                        "account_id": "owned",
                        "limit": 2 if mismatch == "duplicate" else 1,
                        "from_date": "2026-11-01",
                        "to_date": "2026-11-14",
                    }
                },
                raise_on_error=False,
            )
            assert result.is_error
            assert "private transaction text" not in str(result.content)

    asyncio.run(run())
