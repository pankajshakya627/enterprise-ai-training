# NexaAssist MCP servers

This guide covers the policy and banking MCP servers. The policy server uses stdio; the banking server supports stdio and authenticated Streamable HTTP.

FastMCP 4.1.6 exposes the existing RAG HTTP service over local stdio. It adds one retrieval tool, a schema resource and an explanation prompt. Retrieval logic, Atlas access, the LangGraph workflow and banking approvals remain in their existing layers.

From the repository root, `make run-rag` starts the required HTTP service;
`make mcp-demo` then runs the Python client and saves `/tmp/nexa-mcp-demo.json`.
Override `MCP_OUTPUT` to retain reports elsewhere. `make mcp-inspect` runs
Inspector discovery, and `make check-mcp` runs the offline protocol tests.
All runtime targets accept `ENV_FILE`. For a host-owned stdio process, use
`make --no-print-directory run-mcp`; it is separate from the four-service
`make run` stack. The direct commands below also work.

The examples cover three capabilities: typed discovery, calls from independent clients, and access to shared contract schemas and prompt instructions. Custom adapters can provide the same integrations; MCP defines their common protocol.

## Start the existing service

Run from the repository root. The existing `.env` supplies the configured model and Atlas settings. Keep that file private.

```sh
uv sync --locked
make run-rag
```

The adapter uses `AgentSettings.rag_base_url` (default `http://127.0.0.1:8000/`) and the existing HTTP timeout. It needs the RAG service running; it does not connect directly to Atlas. In another terminal:

```sh
curl --fail http://127.0.0.1:8000/health
uv run --locked --no-sync --env-file .env python -m app.mcp.client \
  --query 'What is the maximum loan amount for a NexaHome Loan?' \
  --as-of 2026-10-01 --product-id NEXA-HL \
  --output /tmp/nexa-python-client.json
```

The Python client launches `python -m app.mcp.server` as a separate process, lists capabilities, reads the resource, obtains the prompt, calls retrieval, validates its response date and saves JSON. It rejects an unexpected tool set. The server reserves stdout for MCP and logs to stderr.

## Independent client: official MCP Inspector

Node.js 22.19.0 or newer and npm/npx are required by the pinned Inspector 2.13.0. Run every command below from the repository root, where `pyproject.toml` and the private `.env` live. The web commands open Inspector; the `--cli` commands print a result and exit. See the [official Inspector guide](https://modelcontextprotocol.io/docs/tools/inspector) and [web-client guide](https://modelcontextprotocol.io/docs/2026-07-28/tools/inspector/web).

### Policy MCP: launch from the Inspector web UI

Keep the RAG backend running in one terminal:

```sh
make run-rag
```

From the repository root in another terminal, launch Inspector and let it start
the policy MCP subprocess:

```sh
npx -y @modelcontextprotocol/inspector@2.13.0 --web \
  uv run --locked --no-sync --env-file .env python -m app.mcp.server
```

Open the session URL printed by Inspector. Select the policy server and connect
if it is not already connected. Under **Tools**, select `retrieve_policy` and
use **Edit as JSON** with the complete arguments:

```json
{
  "request": {
    "query": "What is the maximum loan amount for a NexaHome Loan?",
    "as_of": "2026-10-01",
    "product_id": "NEXA-HL"
  }
}
```

Expect one tool, `retrieve_policy`. **Resources** contains
`nexa://retrieval/contract/v1`; **Prompts** contains `explain_policy`, whose
`request_json` argument is a JSON string containing the inner request above.
Only calling the retrieval tool requires the RAG backend; discovery, the schema
resource and the prompt template do not query Atlas.

### Banking MCP: launch from the Inspector web UI over stdio

Keep the synthetic banking backend running in one terminal:

```sh
make run-bank
```

The private `.env` must contain `BANKING_API_TOKEN` and the Inspector's permitted
read scope, for example `BANKING_MCP_CUSTOMER_IDS=["NXC-0010"]` for the authorized
synthetic demo customer. From the repository root in another terminal:

```sh
CLIENT_PORT=6284 MCP_SANDBOX_PORT=6285 MCP_APP_ORIGIN_PORT=6288 \
  npx -y @modelcontextprotocol/inspector@2.13.0 --web \
  uv run --locked --no-sync --env-file .env \
  env BANKING_MCP_TRANSPORT=stdio BANKING_MCP_WRITE_GRANT= \
  python -m app.mcp.banking_server
```

Inspector starts and owns the banking MCP subprocess. The `env` arguments come
after dotenv loading so `stdio` overrides this project's HTTP configuration;
the write grant is empty for this read-only Inspector session. Do not also run
`make run-banking-mcp` for this stdio example.

Expect twelve tools. Select `list_accounts` and paste these complete arguments
in **Edit as JSON**:

```json
{"request": {"customer_id": "NXC-0010"}}
```

In form mode, enter only `{"customer_id":"NXC-0010"}` inside the `request`
field. Discovery does not grant customer access. An empty customer scope blocks
reads, and write tools require an exact application-issued grant; use the
existing owner/reviewer **Service requests** workflow for submissions. See
[banking authorization](BANKING.md) and the [Inspector payload catalog](INSPECTOR_EXAMPLES.md).

### Banking MCP: connect Inspector to the HTTP server

HTTP Inspector connections use an already running MCP listener. Run these in
three separate terminals from the repository root:

```sh
# Terminal 1: synthetic bank on port 8080.
make run-bank
```

```sh
# Terminal 2: banking MCP on port 9002.
make run-banking-mcp
```

```sh
# Terminal 3: banking Inspector web UI on port 6284.
CLIENT_PORT=6284 MCP_SANDBOX_PORT=6285 MCP_APP_ORIGIN_PORT=6288 \
  npx -y @modelcontextprotocol/inspector@2.13.0 --web
```

If `make run-http` already owns the bank and MCP listener, use those processes
and launch only Inspector. In Inspector's **Servers** screen, add an HTTP
(Streamable HTTP) server with these settings:

| Field | Value |
| --- | --- |
| Server URL | `http://127.0.0.1:9002/mcp` |
| Authorization header | `Authorization: Bearer <value of BANKING_MCP_TOKEN from the private .env>` |
| Protocol era, if requested | `legacy` for these FastMCP servers |

Enter the connection token privately in the server's header settings. It is
different from Inspector's own session token in its printed URL. Do not put
`BANKING_MCP_SIGNING_KEY`, `BANKING_API_TOKEN` or a write grant into Inspector.
Connect, list tools, and call `list_accounts` with the same JSON shown above.
An unauthenticated connection returns 401; token-only sessions retain the
configured read scope and cannot submit writes.

### Enter either stdio launch command manually

To configure the server from Inspector itself, launch the blank web UI with
`npx -y @modelcontextprotocol/inspector@2.13.0 --web`, then add a **stdio** server:

| Field | Policy MCP | Banking MCP |
| --- | --- | --- |
| Command | `uv` | `uv` |
| Arguments | `run --locked --no-sync --env-file .env python -m app.mcp.server` | `run --locked --no-sync --env-file .env env BANKING_MCP_TRANSPORT=stdio BANKING_MCP_WRITE_GRANT= python -m app.mcp.banking_server` |
| Working directory | Repository root containing `pyproject.toml` | Same repository root |

The policy example uses Inspector web port **6274**, sandbox **6275** and app
origin **6278**. The banking examples use **6284**, **6285** and **6288**, so a
policy Inspector and a banking Inspector can run side by side. Choose either
the banking stdio example or its HTTP example for port 6284. Open each
Inspector using its own printed session URL; each has a separate session token.

If `MCP Inspector PORT IS IN USE` appears, another Inspector already owns that
web port. Reuse that Inspector, stop it with Ctrl+C, or assign a free
`CLIENT_PORT` with distinct `MCP_SANDBOX_PORT` and `MCP_APP_ORIGIN_PORT` values.
See the [official port settings](https://modelcontextprotocol.io/docs/2026-07-28/tools/inspector/configuration#web-backend-environment-variables).

For both servers in one UI, stop the command-launched Inspector on port 6274,
launch the blank Inspector command above, and add both server entries.
Closing/disconnecting a stdio connection stops its MCP subprocess; separately
launched HTTP backends remain running.

### Policy MCP: command-line Inspector

```sh
npx -y @modelcontextprotocol/inspector@2.13.0 --cli \
  --method tools/list \
  -- .venv/bin/python -m app.mcp.server

npx -y @modelcontextprotocol/inspector@2.13.0 --cli \
  --method tools/call --tool-name retrieve_policy \
  --tool-arg 'request={"query":"What is the maximum loan amount for a NexaHome Loan?","as_of":"2026-10-01","product_id":"NEXA-HL"}' \
  -- .venv/bin/python -m app.mcp.server

npx -y @modelcontextprotocol/inspector@2.13.0 --cli \
  --method resources/read --uri nexa://retrieval/contract/v1 \
  -- .venv/bin/python -m app.mcp.server

npx -y @modelcontextprotocol/inspector@2.13.0 --cli \
  --method prompts/get --prompt-name explain_policy \
  --prompt-args 'request_json="{\"query\":\"Maximum NexaHome loan amount?\",\"as_of\":\"2026-10-01\",\"product_id\":\"NEXA-HL\"}"' \
  -- .venv/bin/python -m app.mcp.server
```

`request` is an object for tools/call. `request_json` is a JSON-encoded string for prompts/get, hence the extra quoting. Inspector reads local settings from the repository's `.env` through the server's settings model.

## Expected observations

- `tools/list`: only `retrieve_policy`, with typed input/output schemas. The tool arguments contain a required `request` object, including required `query` and `as_of`.
- `resources/read`: `request_schema` and `response_schema` generated from existing Pydantic models. Tool discovery supplies invocation schemas; clients can also read them from this resource.
- `prompts/get`: dated, citation-oriented instructions. Fetching this template does not invoke a model or prove prompt adherence.
- `tools/call`: typed evidence including `as_of`, `results`, `amendments` and `timings`. The recorded synthetic-policy query returned clause 4.1 from POL-HL-V3, maximum INR 7.5 crore, five results and one amendment.

Recorded live responses are under `app/evaluation/results/mcp_session_20261104/`. Both Python and Inspector used the configured real RAG HTTP service and Atlas corpus. The repository's banking policies are synthetic. The independent client tested here is Inspector, not Claude Desktop.

## Failure demonstration

```sh
npx -y @modelcontextprotocol/inspector@2.13.0 --cli \
  --method tools/call --tool-name retrieve_policy \
  --tool-arg 'request={"query":"Limits?"}' \
  -- .venv/bin/python -m app.mcp.server
```

Expect `isError: true`, a missing `request.as_of` validation error and a nonzero Inspector exit. An error result contains no usable evidence. Deterministic tests also cover unknown fields, malformed dates, an out-of-range top_k, upstream failures and wrong response dates:

```sh
uv run --locked --no-sync pytest tests/test_mcp.py -q
make check-task
```

## Boundaries

This server is for trusted local clients and an already trusted local RAG endpoint. It does not implement remote authentication, tenant authorization, banking writes or automatic LangGraph tool binding. `readOnlyHint` is descriptive metadata. The host decides whether to load resources, use prompts or call a tool; discovered capabilities are not automatically authorized. Do not expose this setup as a remote service without designing those controls.

MCP does not improve retrieval ranking, fix the previously observed NRLR answer-quality failure, or replace durable reviewer approvals. Those are separate follow-up sessions. No GitHub operations or commits were performed for this implementation.

Official references: [FastMCP tools](https://gofastmcp.com/servers/tools), [client](https://gofastmcp.com/clients/client), [resources](https://gofastmcp.com/servers/resources), [prompts](https://gofastmcp.com/servers/prompts), [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector).

## Next session: LangGraph retrieves through MCP

The existing graph now supports an explicit local MCP transport. Set this in
`.env` (or the environment passed to `make run-agent`), then restart the Agent:

```dotenv
AGENT_RETRIEVAL_TRANSPORT=mcp
```

The default is `http`. An MCP failure ends the request safely; it does not fall
back to HTTP or discover additional operations. Keep the RAG service running:

```sh
make run-rag
# In another terminal, with the setting above:
make run-agent
```

Do not separately start `make run-mcp` for this flow. The retrieval adapter owns
its stdio server subprocess and closes it after each retrieval. Starting a subprocess for each retrieval adds startup latency, but gives the
adapter responsibility for closing it. The server receives only the configured RAG URL and HTTP timeout;
model or banking credentials are not forwarded through the transport environment.
The local server can still read the project's private `.env` via its settings.

```mermaid
sequenceDiagram
    participant UI as Streamlit / HTTP client
    participant API as Agent FastAPI
    participant G as LangGraph
    participant M as Local FastMCP subprocess
    participant R as Existing RAG API
    UI->>API: POST /agent (query and explicit scope)
    API->>G: orchestrate / build_workflow
    G->>G: Screen input, classify, resolve scope
    Note over G: Forbidden or missing scope stops before MCP
    G->>M: initialize, tools/list (JSON-RPC over stdio)
    G->>M: tools/call retrieve_policy(request)
    M->>R: POST /retrieve (HTTP JSON)
    R-->>M: RetrieveResponse
    M-->>G: Structured evidence, amendments, citations
    G->>G: Validate schema/date; retain evidence
    G->>G: build_answer_chain consumes retained evidence
    G-->>API: Answer with validated quotes/status
    API-->>UI: AgentResponse JSON
```

Try a policy question without needing banking credentials:

```sh
curl --fail-with-body http://127.0.0.1:8001/agent \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is the maximum NexaHome loan amount?","as_of":"2026-10-01","product_id":"NEXA-HL","jurisdiction":"IN"}'
```

The same path is used by the existing Streamlit Agent UI; no UI transport switch
is needed. The configured model and indexed corpus determine whether the answer
is supported. A valid protocol exchange alone does not establish answer quality.

| Component | Method / responsibility |
|---|---|
| `app/core/config.py` | `AgentSettings.agent_retrieval_transport` accepts only `http` or `mcp`; server configuration, not request-controlled |
| `app/services/agent.py` | `build_workflow.retrieve` selects the adapter inside the existing Runnable |
| `app/services/agent_mcp_retrieval.py` | `retrieve_over_mcp` owns subprocess, checks discovered tool names, calls the fixed tool, validates full schema and date |
| `app/mcp/server.py` | `build_mcp_server.retrieve_policy` reuses existing `retrieve_over_http` |
| `app/services/agent_retrieval.py` | Sends the unchanged `POST /retrieve` request through `httpx.AsyncClient` |
| `app/agents/orchestrator.py` | Screens/routes, retains evidence, and calls the grounded answer or Validator branch |
| `app/chains/answer.py` | `build_answer_chain` keeps exact quote validation and answer statuses; its evidence Runnable does no new remote retrieval |

One policy question makes one `tools/call` and one `POST /retrieve`. Discovery
uses separate protocol messages without retrieving evidence. An approved action can
retrieve again during the existing pre-execution revalidation step: that checks
fresh facts and the exact approved proposal. Human approval, named identities,
leases and idempotency remain in the existing run/action services. This MCP
server exposes no banking write tools.

The configured HTTP timeout also bounds discovery, initialization and the MCP
call together; the graph retains its outer workflow deadline. The adapter stops on protocol errors, wrong dates, invalid schemas or upstream
errors. It returns a generic error without upstream diagnostic details. There are no adapter
retries. Remote MCP authentication and durable remote-server deployment are a
later session, not features provided by this local stdio integration.

```sh
make check-mcp
make check-task
make check-full
```

The offline tests use real in-process MCP protocol handling, HTTP mocks and fake
models. They check one retrieval, retained amendments/parent context/citations,
quote rejection, forbidden and missing scope, allowlisted discovery, deadlines,
and sanitized protocol/connection errors. Live model/Atlas verification is a
separate check.

## Banking tools

The separate [banking MCP server](BANKING.md) exposes all twelve existing bank
operations. It uses independently scoped sessions and exact write grants, so the
policy server above remains retrieval-only. Banking MCP is now the Agent's
default banking transport; its existing approval and retry workflow remains in force.
