"""Authenticate the internal HTTP MCP hop and bind scope/grants to each request."""

from __future__ import annotations

import hashlib
import hmac
import time

from fastmcp.server.dependencies import get_http_request
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import AgentSettings
from app.models.validation import ValidationAccess


class BankingHTTPContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access: ValidationAccess
    expires_at: int
    grant: str | None = None
    backend_identity: str


def backend_identity(settings: AgentSettings) -> str:
    credential = settings.banking_api_token.get_secret_value() if settings.banking_api_token else ""
    return hashlib.sha256((str(settings.banking_base_url) + "\n" + credential).encode()).hexdigest()


def signed_headers(
    settings: AgentSettings, access: ValidationAccess, grant: str | None
) -> dict[str, str]:
    if settings.banking_mcp_signing_key is None or (
        settings.banking_mcp_auth_mode == "token" and settings.banking_mcp_token is None
    ):
        raise ValueError("HTTP MCP credentials are required")
    context = BankingHTTPContext(
        access=access,
        expires_at=int(time.time()) + 330,
        grant=grant,
        backend_identity=backend_identity(settings),
    )
    payload = context.model_dump_json()
    signature = hmac.new(
        settings.banking_mcp_signing_key.get_secret_value().encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "X-Nexa-Banking-Context": payload,
        "X-Nexa-Banking-Signature": signature,
    }
    if settings.banking_mcp_auth_mode == "token":
        assert settings.banking_mcp_token is not None
        headers["Authorization"] = "Bearer " + settings.banking_mcp_token.get_secret_value()
    return headers


def verify_signed_context(
    settings: AgentSettings, payload: str, signature: str
) -> BankingHTTPContext:
    if settings.banking_mcp_signing_key is None or len(payload) > 16384:
        raise ValueError("Invalid signed context")
    expected = hmac.new(
        settings.banking_mcp_signing_key.get_secret_value().encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        raise ValueError("Invalid context signature")
    context = BankingHTTPContext.model_validate_json(payload, strict=True)
    now = int(time.time())
    if (
        context.backend_identity != backend_identity(settings)
        or not now <= context.expires_at <= now + 330
    ):
        raise ValueError("Expired context or changed backend identity")
    return context


class BankingHTTPAuth:
    """Private Agent-to-MCP credential; external read-only clients get configured scope only."""

    def __init__(self, app: ASGIApp, settings: AgentSettings) -> None:
        if settings.banking_mcp_token is None or settings.banking_mcp_signing_key is None:
            raise ValueError("HTTP MCP credentials are required")
        self.app, self.settings = app, settings
        self.secret = settings.banking_mcp_token.get_secret_value()
        self.signing_key = settings.banking_mcp_signing_key.get_secret_value()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if not hmac.compare_digest(
            headers.get("authorization", "").encode(), ("Bearer " + self.secret).encode()
        ):
            await JSONResponse(
                {"detail": "MCP authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        try:
            payload = headers.get("x-nexa-banking-context")
            if payload is None:
                context = BankingHTTPContext(
                    access=ValidationAccess(
                        customer_ids=self.settings.banking_mcp_customer_ids,
                        principal_id="mcp-read-only",
                    ),
                    expires_at=int(time.time()) + 330,
                    backend_identity=backend_identity(self.settings),
                )
            else:
                context = verify_signed_context(
                    self.settings, payload, headers.get("x-nexa-banking-signature", "")
                )
            scope.setdefault("state", {})["banking_context"] = context
        except ValueError:
            await JSONResponse({"detail": "MCP request context rejected"}, status_code=403)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)


def request_session() -> tuple[ValidationAccess, str | None]:
    context: BankingHTTPContext = get_http_request().state.banking_context
    return context.access, context.grant
