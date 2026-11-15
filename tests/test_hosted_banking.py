from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from cryptography.fernet import Fernet
from mcp.shared.auth import OAuthToken
from pydantic import HttpUrl

from app.core.config import AgentSettings
from app.mcp import horizon_login
from app.mcp.banking_http import signed_headers
from app.models.banking_mcp import BANKING_TOOLS
from app.models.validation import ValidationAccess
from app.services import banking_mcp


def configuration(tmp_path: Path) -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        banking_base_url="https://bank.example/",
        banking_mcp_transport="http",
        banking_mcp_auth_mode="oauth",
        banking_mcp_url="https://mcp.example/mcp",
        banking_mcp_signing_key="s" * 48,
        banking_mcp_oauth_directory=tmp_path / "tokens",
        banking_mcp_oauth_storage_key=Fernet.generate_key().decode(),
    )


def test_oauth_scope_keeps_authorization_header_for_gateway(tmp_path: Path) -> None:
    config = configuration(tmp_path)
    access = ValidationAccess(customer_ids=frozenset({"NXC-0010"}))
    headers = signed_headers(config, access, "exact-test-grant")
    assert "Authorization" not in headers
    assert json.loads(headers["X-Nexa-Banking-Context"])["grant"] == "exact-test-grant"
    assert headers["X-Nexa-Banking-Signature"]


def test_encrypted_oauth_cache_and_noninteractive_runtime(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("fastmcp.client.auth.oauth.find_available_port", lambda **kw: 54321)
    config = configuration(tmp_path)

    async def run() -> None:
        first = banking_mcp.banking_oauth(config, interactive=True)
        first._bind(str(config.banking_mcp_url))
        await first.token_storage_adapter.set_tokens(
            OAuthToken(access_token="synthetic-access", token_type="Bearer", expires_in=3600)
        )
        second = banking_mcp.banking_oauth(config)
        second._bind(str(config.banking_mcp_url))
        token = await second.token_storage_adapter.get_tokens()
        assert token is not None and token.access_token == "synthetic-access"
        assert all(
            b"synthetic-access" not in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
        )
        assert config.banking_mcp_oauth_directory.stat().st_mode & 0o777 == 0o700
        with pytest.raises(RuntimeError, match="Authorize Horizon first"):
            await second.redirect_handler("https://authorization.example")
        with pytest.raises(ValueError, match="encryption key"):
            banking_mcp.banking_oauth(
                config.model_copy(update={"banking_mcp_oauth_storage_key": None})
            )

    asyncio.run(run())


def test_login_requires_https_and_exact_tool_contract(tmp_path: Path, monkeypatch) -> None:
    config = configuration(tmp_path)
    monkeypatch.setattr(horizon_login, "get_agent_settings", lambda: config)
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.list_tools.return_value = [SimpleNamespace(name=name) for name in BANKING_TOOLS]
    monkeypatch.setattr(horizon_login, "Client", lambda *args, **kwargs: client)
    asyncio.run(horizon_login.login())
    client.list_tools.return_value = []
    with pytest.raises(ValueError, match="Unexpected"):
        asyncio.run(horizon_login.login())
    config.banking_mcp_url = HttpUrl("http://localhost/mcp")
    with pytest.raises(ValueError, match="HTTPS"):
        asyncio.run(horizon_login.login())


def test_oauth_adapter_supplies_gateway_auth_and_signed_scope(tmp_path: Path, monkeypatch) -> None:
    config = configuration(tmp_path)
    access = ValidationAccess(customer_ids=frozenset({"NXC-0010"}))
    captures = []
    auth = Mock()
    monkeypatch.setattr(banking_mcp, "banking_oauth", lambda settings: auth)

    def transport(**kwargs):
        captures.append(kwargs)
        return Mock()

    monkeypatch.setattr(banking_mcp, "StreamableHttpTransport", transport)
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.list_tools.return_value = [SimpleNamespace(name=name) for name in BANKING_TOOLS]
    client.call_tool.return_value = SimpleNamespace(
        is_error=False,
        structured_content={"status_code": 200, "body": {"customer_id": "NXC-0010"}, "headers": {}},
    )
    monkeypatch.setattr(banking_mcp, "Client", lambda *args, **kwargs: client)

    async def run():
        adapter = banking_mcp.BankingMCPTransport(config, access)
        response = await adapter.handle_async_request(
            httpx.Request("GET", "https://bank.example/v1/customers/NXC-0010")
        )
        assert response.status_code == 200
        assert captures[0]["auth"] is auth
        assert "Authorization" not in captures[0]["headers"]
        config.banking_mcp_url = HttpUrl("http://localhost/mcp")
        with pytest.raises(httpx.ConnectError):
            await adapter.handle_async_request(
                httpx.Request("GET", "https://bank.example/v1/customers/NXC-0010")
            )

    asyncio.run(run())


def test_saved_runs_are_bound_to_the_oauth_connection(settings, tmp_path):
    from app.services.agent_runs import AgentRunService

    config = configuration(tmp_path)
    service = AgentRunService(
        settings, config, Mock(), None, Mock(), ValidationAccess(customer_ids=frozenset())
    )
    hosted = service._bank_scope()
    config.banking_mcp_auth_mode = "token"
    assert service._bank_scope() != hosted


def test_login_module_entrypoint(monkeypatch) -> None:
    import runpy

    invoked = Mock(side_effect=lambda coroutine: coroutine.close())
    monkeypatch.setattr(asyncio, "run", invoked)
    runpy.run_path(str(Path(horizon_login.__file__)), run_name="__main__")
    assert invoked.call_count == 1
