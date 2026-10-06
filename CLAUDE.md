# CLAUDE.md - Project Instructions for Claude Agent

> This file is the single source of truth for how you (Claude) must work on this project.
> Read it before writing ANY code. Follow it strictly.

## 1. Project Context & Build Order

This is a production-grade AI application built in strictly separated LAYERS. Do NOT build everything at once.

**Primary Order of Implementation:**
1.  **Layer 1 (Foundation): LangChain + MongoDB** - Core LLM logic, RAG chains, tools, prompts, and MongoDB Atlas vector operations. This is where the primary intelligence lives.
2.  **Layer 2 (Serving): FastAPI** - Wrap Layer 1 with production REST APIs. No LLM logic inside routers.
3.  **Layer 3 (UI): Streamlit** - UI on top of FastAPI. No business logic in the UI.
4.  **Layer 4 (Future): LangGraph** - Convert chains to stateful graphs / agents only when complexity demands it.
5.  **Layer 5 (Future): FastMCP** - Expose tools and resources via MCP server.
6.  **Layer 6 (Continuous): Observability & Evaluations** - Unit tests, LLM evaluations (LangSmith/Ragas), and operational telemetry (Datadog).

**Tech Stack:**
- **Primary:** LangChain / LangChain-Core
- **Vector DB:** MongoDB Atlas Vector Search (`langchain-mongodb`)
- **API:** FastAPI + Pydantic v2
- **UI:** Streamlit
- **Observability:** Datadog (APM & LLM Observability) + LangSmith
- **Evaluation:** LangSmith, Ragas, DeepEval, pytest

## 2. CORE PRINCIPLES - NON-NEGOTIABLE

### 2.1 Minimal Implementation (YAGNI & KISS)
- Write the **minimum code needed** to satisfy the CURRENT layer/task.
- DO NOT add extra features, helpers, or abstractions for future layers.
- If a function is not used right now, DO NOT create it. No redundant wrappers.

### 2.2 No Assumptions & Evidence-Based Debugging
- NEVER assume model names, prompts, vector DB cluster structures, API contracts, or eval metrics. Ask for clarification or use `TODO: Requires clarification - [question]`.
- Do not hallucinate library methods. Check official docs first.
- **When fixing errors:** Read the exact traceback or Pydantic validation error. Do not guess the fix.

### 2.3 Documentation First
Before coding, rely on these specific approaches:
- **LangChain:** Use LCEL (`prompt | llm | parser`) and `langchain-core` primitives. No deprecated `LLMChain`. 
- **MongoDB Atlas:** Use `MongoDBAtlasVectorSearch` from the official `langchain_mongodb` package. Do NOT use deprecated `langchain_community` wrappers.
- **FastAPI:** Use `APIRouter`, `Annotated`, `Depends`, and Pydantic v2 with `extra='forbid'`.
- **Observability:** Standardize `ddtrace` for Datadog telemetry and standard LangChain tracing environment variables.

## 3. Architecture & Project Structure

Adhere to this directory structure. Do not create files outside unless explicitly instructed.

```text
/
├── pyproject.toml      # Modern dependency and tool configuration
├── .env.example        # Template for required environment variables (Mongo, Datadog, LangSmith)
├── /tests              # Traditional unit and integration tests (pytest)
├── /app
│   ├── /chains         # Layer 1: Pure LangChain - prompts, llms, rag chains, retrievers
│   ├── /database       # Layer 1: MongoDB clients and Vector Search index configurations
│   ├── /services       # Business logic bridging chains -> API
│   ├── /api            # Layer 2: FastAPI - routers, dependencies, schemas
│   ├── /ui             # Layer 3: Streamlit - app.py, components, state
│   ├── /agents         # Layer 4: LangGraph - state, nodes, graphs (future)
│   ├── /mcp            # Layer 5: FastMCP - server.py, tools (future)
│   ├── /evaluation     # Layer 6: Evals - datasets, rag evals, agent evals, app evals
│   ├── /core           # Config (pydantic-settings), telemetry initialization, logging
│   ├── /models         # Shared Pydantic schemas (request/response)
│   ├── main.py         # FastAPI app factory
│   └── streamlit_app.py# Streamlit entry point

```

**Strict Layer Rules:**

* `chains/` must have ZERO FastAPI or Streamlit imports. Pure LangChain.
* `api/` must have ZERO Streamlit imports. Only calls `services/` or `chains/`.
* `ui/` must have ZERO LangChain chain definitions. It only calls `api/` or `services/` and renders.
* `database/` exclusively handles `pymongo` client initialization and `MongoDBAtlasVectorSearch` retrieval config.

## 4. Coding Standards - Production Grade

### 4.1 General Python & Config

* Python 3.14+, full type hints, `from __future__ import annotations`.
* Config via `pydantic-settings`. Never scatter `os.getenv` throughout the code.
* Async first for all I/O (LLM calls, database retrieval, API calls). Use `async def` and `motor` (async MongoDB) where applicable, or handle synchronous fallback safely.
* Explicit errors. No silent `except: pass`.

### 4.2 Vector Database (MongoDB Atlas)

* **Library:** STRICTLY use `from langchain_mongodb import MongoDBAtlasVectorSearch`.
* **Initialization:** Use connection strings. Pass `collection`, `embedding`, and `index_name` explicitly.
* **Search:** Leverage Atlas Vector Search capabilities like `similarity_score_threshold` and MMR. NEVER hallucinate index creation commands; document the needed manual index JSON definition in `database/indexes.md`.

### 4.3 Observability (Datadog + LangSmith)

* **Dual Tracking Strategy:**
* **Datadog:** Primary for APM, infrastructure, latency profiling, and generic LLM Observability (`ddtrace`). Ensure `ddtrace.patch_all()` is executed at application startup to trace FastAPI and PyMongo.
* **LangSmith:** Primary for deep LLM logic debugging, complex LCEL trajectory tracing, and dataset-driven evaluations.


* **Logging:** Use `structlog` configured to output JSON for Datadog ingestion. Never use standard `print()`.

### 4.4 Application Layers

* **Layer 1 (LangChain):** LLMs imported from specific integrations (e.g., `langchain_openai`). Prompts in `/chains/prompts/`. Always expose chains as `Runnable` for composability.
* **Layer 2 (FastAPI):** Thin routers. Inject logic via `Depends()`. Add `/health` and `/metrics` immediately. Use `StreamingResponse` for token streaming.
* **Layer 3 (Streamlit):** Keep UI stateless where possible. Use `st.cache_resource` for API/DB clients.

## 5. Development Workflow

I will prompt you to build incrementally. You must respect this phase order.

1. **Phase 1 - Foundation & Vector DB:** Setup `core/config.py`, initialize `langchain_mongodb` integration, set up `chains/llm.py`. Build ONE chain end-to-end and test via script.
2. **Phase 2 - FastAPI & Telemetry:** Wrap Phase 1 chains in `services/`. Expose via `api/` routers. Integrate `ddtrace` for Datadog and ensure LangSmith traces map cleanly. Add `main.py`.
3. **Phase 3 - Streamlit UI:** Build `streamlit_app.py` consuming FastAPI.
4. **Phase 4 - LangGraph Upgrade:** Migrate complex chains to graphs in `agents/`. Maintain FastAPI contract.
5. **Phase 5 - FastMCP:** Expose tools via MCP server in `mcp/`.
6. **Phase 6 - Testing & Evals:** Add datasets to `evaluation/` from day one.

## 6. EVALUATION & OBSERVABILITY STRATEGY

Production means measured, not just working.

### 6.1 Datadog (Operational Health)

* **APM:** Ensure FastAPI spans, MongoDB query times, and HTTP request latencies are logged.
* **LLM Observability:** Track token usage, prompt latency, and systemic error rates per model via the Datadog Agent.

### 6.2 LangSmith & Deep Evals (AI Quality)

* Keep golden datasets in `/evaluation/datasets/` as JSONL (`input`, `expected_output`, `context`).
* Enable LangSmith via environment variables: `LANGCHAIN_TRACING_V2=true` and `LANGCHAIN_PROJECT`.
* **RAG Eval:** Measure Context Precision, Recall, and Answer Relevancy using Ragas or DeepEval against the retrieved MongoDB documents.
* **Agent Eval:** Measure tool accuracy and trajectory traces.

### 6.3 Your Responsibility as Claude

1. When you create a chain, create 10 golden QAs in `evaluation/datasets/`.
2. When you configure the Vector Store, you must output the exact JSON required for the MongoDB Atlas Vector Search Index.
3. Always add the LangSmith `@traceable` decorator to complex custom functions that LCEL doesn't auto-trace.

## 7. What You MUST NOT Do

* ❌ Do not use Chroma, Pinecone, pgvector, or Milvus. ONLY use MongoDB Atlas Vector Search.
* ❌ Do not build UI (Streamlit) before the underlying LangChain/MongoDB logic works.
* ❌ Do not create a monolithic `utils.py` full of unused functions.
* ❌ Do not skip `ddtrace` or logging setup in the core infrastructure phase.
* ❌ Do not mix logic boundaries (e.g., MongoDB queries inside FastAPI routers).

## 8. Response Format

When responding to tasks, format your thought process inside XML tags, followed by the implementation:

**Implementation:**

* File(s) changed: `...`

```python
# Minimal, typed, production-grade code

```

## 9. Environment & Commands

* **Package Manager:** `uv` (preferred)
* **Datadog APM Runner:** `DD_SERVICE="ai-app" DD_ENV="dev" ddtrace-run uvicorn main:app --reload`
* **Run UI:** `streamlit run streamlit_app.py`
* **Run Evals:** `python -m evaluation.run --type rag` or `pytest tests/`
* **Lint/Format:** `ruff check . && mypy .`

---

**Final Rule:** Build in layers. Back everything with MongoDB Atlas Vector Search. Monitor operationally with Datadog and semantically with LangSmith. If in doubt, write LESS code, not more.

# context0 context handoff (Claude Code)

This project uses **context0** for context handoff between AI coding tools. You have access to the context0 MCP server; use its tools so the user can resume work in another tool (e.g. Cursor, Codex) without losing context.

## On session start

- Call **get_context** with the current repo root path and git branch (infer from project path and git: `git rev-parse --show-toplevel`, `git branch --show-current`).
- If a checkpoint is returned (`found: true`), read it carefully. It was written by a previous AI session. Use `done_text`, `next_text`, `blockers_text`, `tests_text`, and `files` to resume the task. Briefly confirm to the user what context you loaded and what you will do next.

## When the user ends the session or switches tools

When the user says "save context", "I'm switching", "save my session", or "I'm done for now":
- Call **save_context** with a structured summary of this session:
  - **done_text**: What was accomplished (concrete and specific).
  - **next_text**: What should happen next when work resumes.
  - **blockers_text**: Any blockers, waiting on, or open questions.
  - **tests_text**: Test status or commands to run (e.g. "cargo test", "npm test").
  - **files**: Key files that were created or changed (paths relative to repo root).
- Use the current repo path, branch, and commit SHA from git. Be concise; another AI agent will read this to resume.

## Optional

- **list_context** returns recent checkpoints for the same repo + branch if the user wants to see history or pick an older checkpoint.
