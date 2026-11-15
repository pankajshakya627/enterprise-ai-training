BASE_REF ?= HEAD
UV ?= uv
REPORTS = app/evaluation/reports
RUN = $(UV) run --locked --no-sync
AI_REPORT ?= app/evaluation/reports/phase1-current.json
AI_BASELINE ?= evaluation/datasets/phase1_dense_baseline.json
ENV_FILE ?= .env
MCP_OUTPUT ?= /tmp/nexa-mcp-demo.json
APP_RUN = $(RUN) --env-file "$(ENV_FILE)"
RAG_COMMAND = $(APP_RUN) env DD_SERVICE=nexaassist-api ddtrace-run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
BANK_COMMAND = $(APP_RUN) env DD_SERVICE=nexaassist-bank ddtrace-run python -m uvicorn server:app --app-dir nexa_synthetic_data/api/mock_server --host 127.0.0.1 --port 8080
AGENT_COMMAND = $(APP_RUN) env DD_SERVICE=nexaassist-agent ddtrace-run python -m uvicorn app.agent_main:app --host 127.0.0.1 --port 8001
UI_COMMAND = $(APP_RUN) env DD_SERVICE=nexaassist-ui ddtrace-run python -m streamlit run app/streamlit_app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
WITH_LOCAL_BANK ?= 1
WITH_BANKING_MCP ?= 0
BANKING_MCP_COMMAND = $(APP_RUN) env BANKING_MCP_TRANSPORT=http DD_SERVICE=nexaassist-banking-mcp ddtrace-run python -m app.mcp.banking_server
COMPOSE = docker compose --env-file "$(ENV_FILE)"

.PHONY: check-fast check-task check-full check-ai
check-fast:
	$(RUN) python -m app.evaluation.floor_guard --base "$(BASE_REF)"
	$(RUN) ruff check app tests
	$(RUN) ruff format --check app tests
	$(RUN) mypy app tests

check-task: check-fast
	mkdir -p $(REPORTS)
	$(RUN) pytest tests/ -q --cov=app --cov-fail-under=84.13 --cov-report=term:skip-covered --cov-report=xml:$(REPORTS)/coverage.xml
	$(RUN) diff-cover $(REPORTS)/coverage.xml --compare-branch "$(BASE_REF)" --include-untracked --include 'app/**' --fail-under 100 --total-percent-float

check-full: check-task
	SEMGREP_SETTINGS_FILE=$(REPORTS)/semgrep-settings.yml $(UV) tool run --python 3.13 --from semgrep==1.182.1 semgrep scan --config p/default --config p/owasp-top-ten --config p/secrets --metrics off --disable-version-check --disable-nosem --severity ERROR --strict --json --output $(REPORTS)/security.json app
	$(RUN) python -m app.evaluation.security_gate $(REPORTS)/security.json

# Live provider/Atlas calls: run explicitly with approved data and configured credentials.
check-ai:
	test -f "$(AI_BASELINE)"
	$(RUN) python -m app.evaluation.run --output "$(AI_REPORT)"
	$(RUN) python -m app.evaluation.phase_gate check --report "$(AI_REPORT)" --baseline "$(AI_BASELINE)"

.PHONY: help setup check-env prereqs run run-all run-rag run-bank run-agent run-ui
.PHONY: health ingest docker-ready datadog-up datadog-down reranker-up reranker-down
.PHONY: run-mcp mcp-demo mcp-inspect check-mcp run-http run-banking-mcp check-http-mcp

help:
	@printf '%s\n' \
	  'make setup          Install locked dependencies and create .env if missing' \
	  'make prereqs        Check installed runtime dependencies and settings' \
	  'make run            Run RAG, bank, Agent and UI together; Ctrl+C stops them' \
	  'make run-all        Alias for make run' \
	  'make run-hosted     Run RAG, Agent and UI using the configured Horizon/Render bank' \
	  'make run-http       Run the stack with banking MCP over HTTP on :9002' \
	  'make run-banking-mcp  Run the authenticated HTTP banking MCP server' \
	  'make run-rag        RAG API on 127.0.0.1:8000' \
	  'make run-bank       Synthetic banking API on 127.0.0.1:8080' \
	  'make run-agent      LangGraph Agent API on 127.0.0.1:8001' \
	  'make run-ui         Streamlit on 127.0.0.1:8501' \
	  'make run-mcp        Local stdio MCP server; launched by an MCP client' \
	  'make mcp-demo       Discover/read/prompt/retrieve via Python; RAG must be running' \
	  'make mcp-inspect    Discover tools with official Inspector (requires Node/npm)' \
	  'make check-mcp      Offline MCP protocol and failure tests' \
	  'make health         Check all four local HTTP services' \
	  'make ingest         Ingest registered policies; calls OpenAI and writes Atlas' \
	  'make datadog-up     Start the configured Docker Datadog Agent' \
	  'make datadog-down   Stop the Docker Datadog Agent' \
	  'make reranker-up    Start the optional verified GGUF reranker' \
	  'make reranker-down  Stop the optional reranker' \
	  'make check-fast / check-task / check-full / check-ai  Existing quality gates' \
	  'See README.md: Run the services with Make. Override ENV_FILE for another dotenv file.'

setup:
	@command -v $(UV) >/dev/null || { printf '%s\n' 'Install uv first: https://docs.astral.sh/uv/'; exit 1; }
	$(UV) sync --python 3.14 --locked --extra ui --extra evaluation
	@if [ ! -e "$(ENV_FILE)" ] && [ ! -L "$(ENV_FILE)" ]; then cp -n .env.example "$(ENV_FILE)"; fi
	@printf '%s\n' 'Dependencies installed. Configure credentials, URLs and named reviewers in $(ENV_FILE); see README.md.'

check-env:
	@command -v $(UV) >/dev/null || { printf '%s\n' 'Install uv first: https://docs.astral.sh/uv/'; exit 1; }
	@test -f "$(ENV_FILE)" || { printf '%s\n' 'Missing $(ENV_FILE). Run make setup, then configure it using README.md.'; exit 1; }

prereqs: check-env
	@$(APP_RUN) python -c 'import sys, streamlit, ragas, fastmcp, langchain_mongodb, langgraph.checkpoint.mongodb; \
	  from app.core.config import Settings, AgentSettings, UISettings; \
	  assert sys.version_info >= (3, 14); \
	  Settings(_env_file=None); AgentSettings(_env_file=None); UISettings(_env_file=None)' >/dev/null 2>&1 || \
	  { printf '%s\n' 'Runtime dependencies or settings are invalid. Run make setup and check the required settings in README.md. Credential values are hidden.'; exit 1; }
	@printf '%s\n' 'Local dependencies and settings are valid. Atlas indexes, corpus ingestion, provider access and reviewer entitlements require the README setup.'

# uv forwards TERM to its command; keep only this launch's child PIDs for cleanup.
run: prereqs
	@set -eu; \
	  pids=''; \
	  cleanup() { trap - INT TERM; if [ -n "$$pids" ]; then kill $$pids 2>/dev/null || :; fi; wait; }; \
	  trap cleanup EXIT; trap 'exit 130' INT; trap 'exit 143' TERM; \
	  printf '%s\n' 'Starting RAG :8000, Agent :8001 and UI :8501; local bank/MCP start only when enabled. Ctrl+C stops this stack.'; \
	  if [ "$(WITH_LOCAL_BANK)" = "1" ]; then \
	    $(BANK_COMMAND) & pids="$$pids $$!"; \
	  fi; \
	  if [ "$(WITH_BANKING_MCP)" = "1" ]; then \
	    $(BANKING_MCP_COMMAND) & pids="$$pids $$!"; \
	  fi; \
	  $(RAG_COMMAND) & pids="$$pids $$!"; \
	  $(AGENT_COMMAND) & pids="$$pids $$!"; \
	  $(UI_COMMAND) & pids="$$pids $$!"; \
	  while :; do \
	    for pid in $$pids; do \
	      if ! kill -0 "$$pid" 2>/dev/null; then \
	        status=0; wait "$$pid" || status=$$?; \
	        printf 'Service process %s exited (%s); stopping the stack.\n' "$$pid" "$$status"; \
	        [ "$$status" -ne 0 ] || status=1; exit "$$status"; \
	      fi; \
	    done; \
	    sleep 1; \
	  done

# Hosted banking is external; keep local bank/MCP processes out of this stack.
run-hosted: WITH_LOCAL_BANK = 0
run-hosted: WITH_BANKING_MCP = 0
run-hosted: check-hosted-mcp run

check-hosted-mcp: check-env
	@$(APP_RUN) python -c 'from app.core.config import AgentSettings; s = AgentSettings(); assert s.banking_transport == "mcp" and s.banking_mcp_transport == "http" and s.banking_mcp_auth_mode == "oauth" and s.banking_mcp_url.scheme == "https" and s.banking_base_url.scheme == "https" and s.banking_mcp_signing_key and s.banking_mcp_oauth_storage_key' >/dev/null 2>&1 || \
	  { printf '%s\n' 'Configure hosted HTTPS banking URLs, OAuth cache encryption and signing settings; see README.md.'; exit 1; }

run-http: WITH_BANKING_MCP = 1
run-http: APP_RUN = $(RUN) --env-file "$(ENV_FILE)" env BANKING_MCP_TRANSPORT=http
run-http: check-http-mcp run

check-http-mcp: check-env
	@$(APP_RUN) python -c 'from app.core.config import AgentSettings; s = AgentSettings(); assert s.banking_mcp_token and s.banking_mcp_signing_key' >/dev/null 2>&1 || \
	  { printf '%s\n' 'Configure private BANKING_MCP_TOKEN and BANKING_MCP_SIGNING_KEY in your environment file; see app/mcp/BANKING.md.'; exit 1; }

run-banking-mcp: check-http-mcp
	exec $(BANKING_MCP_COMMAND)

run-all: run

run-rag: prereqs
	exec $(RAG_COMMAND)

run-bank: check-env
	exec $(BANK_COMMAND)

run-agent: prereqs
	exec $(AGENT_COMMAND)

run-ui: prereqs
	exec $(UI_COMMAND)

# Do not echo shell commands onto the stdio protocol channel.
run-mcp: check-env
	@exec $(APP_RUN) python -m app.mcp.server

mcp-demo: check-env
	$(APP_RUN) python -m app.mcp.client \
	  --query 'What is the maximum loan amount for a NexaHome Loan?' \
	  --as-of 2026-10-01 --product-id NEXA-HL --output "$(MCP_OUTPUT)"

mcp-inspect: check-env
	@command -v npx >/dev/null || { printf '%s\n' 'Install Node.js with npm to use MCP Inspector.'; exit 1; }
	npx -y @modelcontextprotocol/inspector@2.13.0 --cli --method tools/list \
	  -- $(APP_RUN) python -m app.mcp.server

check-mcp:
	$(RUN) pytest tests/test_mcp.py -q

health:
	@command -v curl >/dev/null || { printf '%s\n' 'Install curl to use make health.'; exit 1; }
	@status=0; for endpoint in \
	  8000/health 8080/openapi.json 8001/health 8501/_stcore/health; do \
	    if curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:$$endpoint" >/dev/null; then \
	      printf 'OK   http://127.0.0.1:%s\n' "$$endpoint"; \
	    else printf 'DOWN http://127.0.0.1:%s\n' "$$endpoint"; status=1; fi; \
	  done; exit "$$status"

ingest: prereqs
	$(APP_RUN) env DD_SERVICE=nexaassist-ingestion ddtrace-run python -m app.services.ingestion nexa_synthetic_data/document_register.json

docker-ready:
	@command -v docker >/dev/null || { printf '%s\n' 'Install Docker with Compose v2 first.'; exit 1; }
	@docker compose version >/dev/null
	@docker info >/dev/null 2>&1 || { printf '%s\n' 'Start Docker Desktop/the Docker daemon, then retry.'; exit 1; }

datadog-up: check-env docker-ready
	$(COMPOSE) -f app/compose.datadog.yaml up -d

datadog-down: check-env docker-ready
	$(COMPOSE) -f app/compose.datadog.yaml down

reranker-up: check-env docker-ready
	$(COMPOSE) -f app/compose.reranker.yaml up -d

reranker-down: check-env docker-ready
	$(COMPOSE) -f app/compose.reranker.yaml down

.PHONY: run-hosted check-hosted-mcp
