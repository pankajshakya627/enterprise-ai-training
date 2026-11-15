"""Construct the Agent FastAPI application without importing the RAG entry point."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.agent import router, v2_router
from app.api.agent_runs import router as runs_router
from app.api.agent_runs import v2_router as v2_runs_router
from app.core.lifespan import lifespan


def create_agent_app() -> FastAPI:
    # Reuse Phase 1's logging, Datadog and tokenizer startup, without its routes.
    """Create the Agent application with validation and durable-run routes.

    Returns:
        Agent FastAPI application with validation, durable runs and operations routes.

    Example:
        app = create_agent_app()
    """
    app = FastAPI(title="NexaAssist Agent Service", lifespan=lifespan)
    app.include_router(router)
    app.include_router(runs_router)
    app.include_router(v2_router)
    app.include_router(v2_runs_router)
    return app


app = create_agent_app()
