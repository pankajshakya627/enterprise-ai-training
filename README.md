# NexaAssist

The [agent architecture and structural refactor report](C4-Documentation/AGENT_REFACTOR_REPORT.md)
includes the worked request flow, code tours, before/after checks and release blockers.
The [release fixes report](C4-Documentation/RELEASE_FIXES_REPORT.md) records the
subsequent checker/packaging repairs and passing full local quality gate.
The [core fixes report](C4-Documentation/CORE_FIXES_REPORT.md) covers the subsequent
LangGraph/FastAPI cleanup, MongoDB ingestion safeguards and their verification.

A policy, product and lending advisor for the staff of NexaBank (a fictional bank), built as the hands-on codebase for the **Enterprise Agentic RAG** training program. Everything is synthetic.

NexaAssist turns policy PDFs into versioned clause chunks and parent sections in
MongoDB Atlas. Staff use the Streamlit chat UI to ask dated policy questions,
inspect quoted sources, and submit feedback. FastAPI serves retrieval, answers,
live RAGAS evaluation and feedback review. LangChain runs the underlying logic;
the default answer model is `gpt-6-luna`.

## Unified Agent chat and MCP

**Agent chat** opens by default with one message input for policy questions,
owned-account transactions, and case or service requests. Missing details produce
clarification and suggestions. Banking actions require scoped credentials;
submissions require a different authorized reviewer to approve the exact proposal.
Policy questions can be asked without banking credentials.

The agent can retrieve policy evidence through the local stdio MCP server and
use the banking MCP over stdio or authenticated HTTP. The hosted banking endpoint
supports OAuth with an encrypted local token cache. Configure `.env` from
[.env.example](.env.example), then follow the
[chat harness guide](app/agents/README.md#unified-chat-harness),
[policy MCP guide](app/mcp/README.md), and
[banking MCP guide](app/mcp/BANKING.md).

```bash
make run             # Local stack; defaults to local stdio banking MCP
make run-hosted      # RAG, Agent and UI using configured hosted banking MCP
make run-mcp         # Policy stdio server, owned by its MCP client
make mcp-demo        # Read-only policy MCP demo; RAG must be running
make check-mcp       # Offline policy MCP protocol and failure tests
```

Earlier milestone notes below describe the implementation at those dates.

### Phase 1 and Phase 2 training implementation — 27 October 2026

The [session and future-development map](C4-Documentation/phase1-phase2-development-map.md)
maps all twenty sessions to their code, tests, demonstrations and extension
points. Phase 1 has a fresh full-dataset RAGAS/judge baseline and a fail-closed
comparison gate; the documented Atlas text index is now provisioned for live
hybrid retrieval. The trained local reranker has a pinned, non-root runtime in
[compose.reranker.yaml](app/compose.reranker.yaml).

The separate Agent Service now integrates **Orchestrator → HTTP Retriever →
Validator → durable approval → Action** for all five service-request types.
`POST /agent` remains validation-only. Start a durable workflow with
`POST /agent/runs`; a different authorized reviewer approves or rejects its
exact proposal before a bank write. MongoDB stores checkpoints and operation
keys; retries preserve the original operation and banking identity.

Direct `/agent/submit` is disabled by default and retained behind
`AGENT_ALLOW_DIRECT_SUBMIT=true` for compatibility only. The ordinary RAG API
and Streamlit policy workflow remain available. Streamlit's **Service requests**
workspace now prepares, loads, approves/rejects and recovers durable runs through
the Agent API. Input screening runs before model/tool calls; model-call, output
and conservative token-work limits bound each workflow. Case tools, MCP and
production deployment remain separate extensions.

See the [workflow guide](app/agents/README.md),
[syllabus gap analysis](.planning/phase2-four-agent-integration.md) and
[current action sequence](C4-Documentation/c4-dynamic-approved-service-request.md).
The [four-agent component and protocol guide](C4-Documentation/FOUR_AGENT_WORKFLOW_README.md)
maps methods, payloads, REST/HTTP calls, local graph handoffs, persistence,
approval and retry behavior. Its [editable training presentation](C4-Documentation/presentations/NexaAssist_Four_Agent_Workflow_ppt169_20261027/README.md)
covers the complete implemented workflow and API boundaries.
The [latest Phase 1/2 verification report](app/evaluation/results/phase12_completion_20261027/REPORT.md)
records 525 tests, real MongoDB/UI checks, both live golden baselines and the
remaining production boundaries.
Run `make check-full` for the blocking quality contract; CI also tests the
workflow with real MongoDB and the supplied bank OpenAPI contract.
`make check-ai` explicitly calls configured providers/Atlas and holds the
reviewed full-dataset baseline; see the development map before running it.

### Historical Phase 1 status — 21 October 2026

| Area | Available now |
|---|---|
| Retrieval | Dense search, optional Atlas hybrid search, institution/date/product scope, validated parent expansion and complete linked circulars |
| Answers | Quoted citations, a token budget, one citation-repair attempt, scope clarification and safe abstention |
| Streamlit | Chat history within the current session, Answer/Evidence modes, references, feedback and a protected review workspace |
| Evaluation | Live and batch RAGAS/judge workflows; **RAGAS scores appear in Datadog only**, with a link from the chat |
| Feedback | MongoDB persistence, retry protection, reviewer-token access and optional Datadog `thumbs` feedback |
| Operations | Docker Datadog Agent setup, APM/LLM tracing, JSON logs, health, retrieval counters and latency percentiles |
| Validation | **121 application tests and 22 mock API tests passed**; Ruff and mypy passed in the saved verification run |

The latest targeted live checks covered twelve policy scenarios, including the
fee change across 1 July 2026, earlier sanctions, opt-in conditions and conflicting
scope. The final fee evaluation returned a correct answer and four scored RAGAS
metrics queued for Datadog. Earlier cloud visibility was confirmed separately;
cloud receipt was not rechecked in this run. These observations do not establish
full-dataset quality or production readiness.

See the [fix and validation report](app/evaluation/results/fixes_20261021/FIX_REPORT.md)
and [saved summary](app/evaluation/results/fixes_20261021/SUMMARY.json) for evidence,
including failed intermediate samples. Known gaps include a layout shift when
reloading a previously open sidebar, occasional date-intent abstention, and
unverified sustained load/cloud deployment. Hybrid quality comparisons and a
compatible local reranker remain open. LangGraph agents and an application MCP
server are future layers.

| | |
|---|---|
| Language | Python 3.14+ |
| Orchestration | LangChain (LCEL runnables) |
| API and UI | FastAPI/Pydantic v2 and Streamlit |
| Vector store | MongoDB Atlas Vector Search via `langchain-mongodb` |
| Embeddings | OpenAI `text-embedding-3-small`, 1536 dimensions |
| PDF parsing | `pdfplumber` (tables and headings), `unstructured` (element types) |
| Chunking | Clause-aware rules in `app/chains/chunking.py`, `langchain-text-splitters` for oversized clauses |
| Config and logging | `pydantic-settings`, `structlog` (JSON), optional `ddtrace` |
| Quality and telemetry | RAGAS, a structured LLM judge, Datadog |
| Package manager | `uv` |

## Table of contents

1. [Architecture](#1-architecture)
2. [Project layout](#2-project-layout)
3. [How a document flows through the system](#3-how-a-document-flows-through-the-system)
4. [Classes and functions](#4-classes-and-functions)
5. [Data model: the chunk record](#5-data-model-the-chunk-record)
6. [Getting started](#6-getting-started)
7. [Using the project, including how to execute the code](#7-using-the-project)
8. [Testing and quality checks](#8-testing-and-quality-checks)
9. [Design decisions](#9-design-decisions)
10. [Roadmap](#10-roadmap)

For a file-by-file walkthrough, use [APP_COMPONENT_GUIDE.md](APP_COMPONENT_GUIDE.md).
Start all four services with [Streamlit and endpoint coverage](#streamlit-and-endpoint-coverage);
use [DATADOG_README.md](DATADOG_README.md) for tracing and scores.

Open the [Datadog Field Guide](DATADOG_FIELD_GUIDE.html) locally for diagrams,
flashcards and a code-backed walkthrough of this repo's tracing, prompt versions,
evaluations and feedback. The HTML works offline; Datadog links require your account.

---

## 1. Architecture

Open the latest [Archify architecture](.archify/architecture-nexaassist-20261021-214925/nexaassist.html)
for the current answer, RAGAS, feedback, ingestion and Datadog paths. Choose
**Live** for animation and select a component for its verified code references.
The [source notes](.archify/architecture-nexaassist-20261021-214925/SOURCE_NOTES.md)
explain the local snapshot and the limits of this illustrated topology.

Open the [interactive architecture explorer](C4-Documentation/nexaassist-architecture.html)
for animated C4 views, the document pipeline and clickable code mappings.
The [reading guide](C4-Documentation/architecture-explorer.md) explains the flows
and current boundaries. Download/open the HTML in a browser; GitHub shows its source.
Use the [static preview](C4-Documentation/nexaassist-architecture-preview.png)
for a quick view without opening the interactive file.

Ingestion and retrieval share data stores but run as separate processes. The API
does not wait for an ingestion job to finish before serving a question. They
still share Atlas capacity, so heavy indexing can affect request latency.

```mermaid
flowchart LR
    subgraph WRITE["Write path: built in Sessions 1 and 2"]
        direction LR
        SRC["Policy PDFs and circulars"] --> LOAD["Load via document register"]
        LOAD --> PARSE["Parse: tables, headings"]
        PARSE --> CHUNK["Chunk by clause"]
        CHUNK --> ENRICH["Enrich: header, metadata,<br/>parent_id, checksum"]
        ENRICH --> COMPARE["Compare checksums"]
        COMPARE --> EMBED["Embed only<br/>new or changed"]
    end

    EMBED --> ATLAS[("MongoDB Atlas<br/>chunks + vector index")]
    ENRICH --> PARENTS[("MongoDB Atlas<br/>parent sections")]

    subgraph READ["Read path: Session 4"]
        direction LR
        CALLER["Streamlit or API caller"] --> RET["FastAPI retrieval / answer services"]
        RET --> QEMB["Embed query<br/>same model"]
        QEMB --> SEARCH["Vector search + filters"]
        SEARCH --> EXPAND["Validate parents and load linked circulars"]
        EXPAND --> CITED["Cited evidence or validated answer"]
    end

    ATLAS -.-> SEARCH
    PARENTS -.-> EXPAND
```

Why version metadata matters: a circular can supersede a single clause of a manual. If the write path does not stamp `policy_version` and `effective_date` on every chunk, the read path cannot tell a current clause from a superseded one, and similarity search alone will happily return the old one.

### Layered build order

The project is built one layer at a time (see [CLAUDE.md](CLAUDE.md)). Dependencies only point downward.

```mermaid
flowchart TB
    L6["Layer 6: Evaluation and observability"] -.-> L1
    L5["Layer 5: FastMCP server"] --> L4
    L4["Layer 4: LangGraph agents"] --> L2
    L3["Layer 3: Streamlit UI"] --> L2
    L2["Layer 2: FastAPI services"] --> L1
    L1["Layer 1: LangChain + MongoDB Atlas"]

    classDef built fill:#d1fae5,stroke:#047857,color:#064e3b
    classDef todo fill:#f3f4f6,stroke:#9ca3af,color:#374151
    class L1,L2,L3,L6 built
    class L4,L5 todo
```

Green means implemented. Selected Atlas, model and browser paths have been
verified live; the validation report records their limits. Future layers are grey.

### C4 model views

The architecture is also documented with the [C4 model](https://c4model.com/): a
system landscape, then context, container and component diagrams, and a dynamic
diagram for one request. Elements marked planned or not integrated remain future
work. These views describe the local application, rather than a verified cloud
deployment (see [Known limitations](#known-limitations)).

The diagrams use Mermaid flowcharts and sequence diagrams rather than Mermaid's experimental `C4Context` syntax, because that syntax hard-codes line and label colors that are hard to read on a dark background. Every node here sets its own fill, border and text color, so the diagrams read in both GitHub light and dark themes.

#### Diagram key (applies to all C4 diagrams below)

```mermaid
flowchart LR
    kp(["Person"]):::person
    ks["Software system in scope"]:::system
    kc["Container"]:::container
    kk["Component"]:::component
    kd[("Data store")]:::store
    ke["External system"]:::external
    kn["Planned or not integrated"]:::planned

    kp -->|"Solid arrow: unidirectional call, labeled with purpose and [technology]"| ks
    kc -.->|"Dashed arrow: optional or not yet built"| ke

    classDef person fill:#08427b,stroke:#93c5fd,color:#ffffff,stroke-width:2px
    classDef system fill:#1e40af,stroke:#bfdbfe,color:#ffffff,stroke-width:2px
    classDef container fill:#2563eb,stroke:#bfdbfe,color:#ffffff,stroke-width:2px
    classDef component fill:#bfdbfe,stroke:#1d4ed8,color:#0f172a,stroke-width:2px
    classDef store fill:#2563eb,stroke:#bfdbfe,color:#ffffff,stroke-width:2px
    classDef external fill:#475569,stroke:#cbd5e1,color:#ffffff,stroke-width:2px
    classDef planned fill:#e2e8f0,stroke:#475569,color:#0f172a,stroke-width:2px,stroke-dasharray:5 4
    linkStyle default stroke:#8b949e,stroke-width:2px
```

Abbreviations: LCEL is the LangChain Expression Language, APM is application performance monitoring, and `as_of` is the date a question is answered for.

#### System landscape: NexaBank

Scope: the part of the fictional NexaBank estate that this repository touches. A landscape is a context diagram without a single focus, so it also shows what is planned or only mocked.

```mermaid
---
title: "System landscape: NexaBank (implemented slice)"
---
flowchart TB
    staff(["NexaBank staff<br/>[Person]<br/>Loan officers and policy specialists"]):::person

    subgraph BANK["NexaBank (fictional) - enterprise boundary"]
        nexa["NexaAssist<br/>[Software System]<br/>Retrieves date-correct policy evidence<br/>and generates cited answers"]:::system
        planned["Planned layers<br/>[Software System: not built]<br/>LangGraph agents,<br/>FastMCP server"]:::planned
        core["Case and Core API<br/>[Software System: mock only]<br/>OpenAPI spec and mock server<br/>in nexa_synthetic_data/api"]:::planned
    end

    atlas["MongoDB Atlas<br/>[External System]<br/>Vector Search and document storage"]:::external
    openai["OpenAI API<br/>[External System]<br/>Embeddings and chat model"]:::external
    obs["Datadog<br/>[External Systems, optional]<br/>APM, LLM traces, evaluations"]:::external

    staff -->|"Asks policy questions [HTTPS/JSON]"| nexa
    nexa -->|"Stores and searches policy chunks [pymongo]"| atlas
    nexa -->|"Embeds text, generates answers [HTTPS]"| openai
    nexa -.->|"Sends traces and metrics [ddtrace]"| obs
    planned -.->|"Will call the NexaAssist API [HTTPS/JSON]"| nexa
    nexa -.->|"Not integrated yet"| core

    classDef person fill:#08427b,stroke:#93c5fd,color:#ffffff,stroke-width:2px
    classDef system fill:#1e40af,stroke:#bfdbfe,color:#ffffff,stroke-width:2px
    classDef external fill:#475569,stroke:#cbd5e1,color:#ffffff,stroke-width:2px
    classDef planned fill:#e2e8f0,stroke:#475569,color:#0f172a,stroke-width:2px,stroke-dasharray:5 4
    style BANK fill:none,stroke:#8b949e,stroke-width:2px,stroke-dasharray:6 4
    linkStyle default stroke:#8b949e,stroke-width:2px
```

#### Level 1: System context

Scope: the NexaAssist software system. Primary elements: the people who use it and the systems it depends on.

```mermaid
---
title: "System context: NexaAssist"
---
flowchart TB
    staff(["NexaBank staff<br/>[Person]<br/>Asks as-of policy questions"]):::person
    ops(["Ingestion operator<br/>[Person]<br/>Runs policy ingestion"]):::person

    nexa["NexaAssist<br/>[Software System]<br/>Ingests policy PDFs into versioned clause chunks.<br/>Returns filtered, cited evidence and answers."]:::system

    files["Policy corpus<br/>[External System: files]<br/>document_register.json,<br/>policy and circular PDFs"]:::external
    atlas["MongoDB Atlas<br/>[External System]<br/>Hosts the vector index and collections"]:::external
    openai["OpenAI API<br/>[External System]<br/>text-embedding-3-small and chat model"]:::external
    obs["Datadog<br/>[External Systems, optional]<br/>APM, LLM traces, evaluations"]:::external

    staff -->|"Uses Streamlit or the REST API [HTTP/JSON locally]"| nexa
    ops -->|"Runs the ingestion command [shell]"| nexa
    nexa -->|"Reads register and PDFs [filesystem]"| files
    nexa -->|"Writes chunks, searches with filters [pymongo]"| atlas
    nexa -->|"Embeds text, generates answers [HTTPS]"| openai
    nexa -.->|"Sends traces and metrics [ddtrace]"| obs

    classDef person fill:#08427b,stroke:#93c5fd,color:#ffffff,stroke-width:2px
    classDef system fill:#1e40af,stroke:#bfdbfe,color:#ffffff,stroke-width:2px
    classDef external fill:#475569,stroke:#cbd5e1,color:#ffffff,stroke-width:2px
    linkStyle default stroke:#8b949e,stroke-width:2px
```

#### Level 2: Containers

Scope: NexaAssist. Primary elements: the separately running processes and data stores. The write path and the read path never call each other; they share only the data stores.

```mermaid
---
title: "Containers: NexaAssist"
---
flowchart TB
    staff(["NexaBank staff<br/>[Person]"]):::person
    ops(["Ingestion operator<br/>[Person]"]):::person
    files["Policy corpus<br/>[External System: files]<br/>register and PDFs"]:::external
    openai["OpenAI API<br/>[External System]<br/>Embeddings and chat model"]:::external
    obs["Datadog<br/>[External System, optional]<br/>Operational and LLM observability"]:::external

    subgraph NEXA["NexaAssist [Software System boundary]"]
        direction TB
        ui["Streamlit UI<br/>[Container: Python]<br/>Chat, evidence and feedback review"]:::container
        cli["Ingestion CLI<br/>[Container: Python, asyncio]<br/>python -m app.services.ingestion.<br/>Parses, chunks by clause, embeds only<br/>new or changed chunks, retires stale chunks."]:::container
        api["NexaAssist API<br/>[Container: FastAPI, uvicorn]<br/>Retrieval, answers, live evaluation,<br/>feedback and operational endpoints"]:::container
        evaluation["Evaluation CLI<br/>[Container: Python]<br/>Golden cases, judge and RAGAS"]:::container
        agent["Docker Datadog Agent<br/>[Container: Agent 7]<br/>APM TCP 18126; DogStatsD UDP 18125<br/>published on host loopback"]:::container
        chunks[("Chunks collection and vector index<br/>[Data store: MongoDB Atlas]<br/>One document per clause chunk,<br/>with version and date metadata")]:::store
        parents[("Parent sections collection<br/>[Data store: MongoDB Atlas]<br/>Whole sections keyed by parent_id,<br/>not embedded")]:::store
        feedback[("Feedback collection<br/>[Data store: MongoDB Atlas]<br/>Submissions and reviewer decisions")]:::store
    end

    ops -->|"Runs [shell]"| cli
    staff -->|"Chat and review [HTTP locally]"| ui
    ui -->|"Six API operations [HTTP/JSON]"| api
    staff -->|"Direct REST access [HTTP/JSON locally]"| api
    cli -->|"Reads register and PDFs [filesystem]"| files
    cli -->|"Embeds changed chunks [HTTPS]"| openai
    cli -->|"Upserts and deletes chunks [pymongo]"| chunks
    cli -->|"Upserts sections after changed chunks [pymongo]"| parents
    api -->|"Embeds query, generates answer [HTTPS]"| openai
    api -->|"Scoped search and linked circular reads [pymongo]"| chunks
    api -->|"Reads parent sections [pymongo]"| parents
    api -->|"Save, list and review [pymongo]"| feedback
    evaluation -->|"Read policy and reference text [pymongo]"| chunks
    evaluation -->|"Generate, judge and score [HTTPS]"| openai
    api -.->|"APM and LLM telemetry [ddtrace]"| agent
    ui -.->|"Traces and runtime metrics [ddtrace]"| agent
    cli -.->|"Optional telemetry [ddtrace]"| agent
    evaluation -.->|"Evaluation submissions [ddtrace]"| agent
    agent -.->|"Datadog intake [HTTPS]"| obs

    classDef person fill:#08427b,stroke:#93c5fd,color:#ffffff,stroke-width:2px
    classDef container fill:#2563eb,stroke:#bfdbfe,color:#ffffff,stroke-width:2px
    classDef store fill:#2563eb,stroke:#bfdbfe,color:#ffffff,stroke-width:2px
    classDef external fill:#475569,stroke:#cbd5e1,color:#ffffff,stroke-width:2px
    style NEXA fill:none,stroke:#8b949e,stroke-width:2px,stroke-dasharray:6 4
    linkStyle default stroke:#8b949e,stroke-width:2px
```

#### Level 3: Components of the NexaAssist API (read path)

Scope: the API container. The layers follow the rules in [CLAUDE.md](CLAUDE.md): routers call services, services call chains, and only `database/` touches `pymongo`.

```mermaid
---
title: "Components: NexaAssist API"
---
flowchart TB
    staff(["NexaBank staff<br/>[Person]"]):::person
    openai["OpenAI API<br/>[External System]"]:::external
    chunks[("Chunks collection<br/>and vector index<br/>[Data store]")]:::store
    parents[("Parent sections<br/>collection<br/>[Data store]")]:::store

    subgraph API["NexaAssist API [Container: FastAPI]"]
        direction TB
        rops["Operations router<br/>[Component: APIRouter]<br/>Health, counters and latency percentiles"]:::component
        rret["Retrieval router<br/>[Component: APIRouter]<br/>POST /retrieve"]:::component
        rans["Answer router<br/>[Component: APIRouter]<br/>POST /answer and /answer/evaluated"]:::component
        rfeedback["Feedback router and reviewer dependency<br/>[Component: api/feedback.py, auth.py]<br/>Bearer token on listing/review"]:::component
        sret["Retrieval service<br/>[Component: services/retrieval.py]<br/>retrieve_policy and counters"]:::component
        sans["Answer service<br/>[Component: services/answer.py]<br/>answer_policy"]:::component
        seval["Live evaluation service<br/>[Component: services/evaluation.py]<br/>RAGAS on the final answer and exact context"]:::component
        sfeedback["Feedback service<br/>[Component: services/feedback.py]<br/>Retry-safe save and optional Datadog feedback"]:::component
        cret["Retrieval chain<br/>[Component: LCEL runnable]<br/>Institution, date and product scope,<br/>validated parents and circulars"]:::component
        amendments["Linked circular lookup<br/>[Component: database/retrieval.py]<br/>Complete indexed instruments;<br/>reject inconsistent metadata"]:::component
        cans["Answer chain<br/>[Component: LCEL runnable]<br/>Context budget, citation checks,<br/>one repair draft, clarification or abstention"]:::component
        prompt["Answer prompt<br/>[Component: ChatPromptTemplate]"]:::component
        factory["Model and embedding factories<br/>[Component: chains/llm.py, embeddings.py]"]:::component
        mongo["Store factories<br/>[Component: database/mongo.py]<br/>get_vector_store, get_parent_store"]:::component
        core["Settings, logging, telemetry<br/>[Component: core/]<br/>pydantic-settings, structlog, ddtrace"]:::component
    end

    staff -->|"HTTP/JSON locally"| rret
    staff -->|"HTTP/JSON locally"| rans
    staff -->|"HTTP/JSON locally"| rops
    rret -->|"Calls"| sret
    rans -->|"Calls"| sans
    rans -->|"Evaluated requests"| seval
    staff -->|"Feedback; bearer token for review [HTTP/JSON]"| rfeedback
    rfeedback -->|"Calls"| sfeedback
    sfeedback -->|"Atomic save and review [pymongo]"| feedbackstore["Feedback collection"]:::store
    seval -->|"Retrieve once"| sret
    seval -->|"Replay stored evidence for generation"| cans
    rops -->|"Reads counters"| sret
    sans -->|"Passes retrieval as a runnable"| cans
    cans -->|"Plain /answer retrieval"| sret
    cans -->|"Formats messages with"| prompt
    sret -->|"Builds and invokes"| cret
    sret -->|"Gets clients from"| factory
    sret -->|"Gets stores from"| mongo
    cret -->|"Load linked circulars"| amendments
    amendments -->|"Scoped reads and source validation [pymongo]"| chunks
    sans -->|"Gets chat model from"| factory
    factory -->|"Embeds query, generates answer [HTTPS]"| openai
    mongo -->|"Vector search [pymongo]"| chunks
    mongo -->|"Reads sections [pymongo]"| parents
    core -.->|"Configures every component"| rops

    classDef person fill:#08427b,stroke:#93c5fd,color:#ffffff,stroke-width:2px
    classDef component fill:#bfdbfe,stroke:#1d4ed8,color:#0f172a,stroke-width:2px
    classDef store fill:#2563eb,stroke:#bfdbfe,color:#ffffff,stroke-width:2px
    classDef external fill:#475569,stroke:#cbd5e1,color:#ffffff,stroke-width:2px
    style API fill:none,stroke:#8b949e,stroke-width:2px,stroke-dasharray:6 4
    linkStyle default stroke:#8b949e,stroke-width:2px
```

#### Level 3: Components of the Ingestion CLI (write path)

Scope: the ingestion container. The chain steps run in the order shown.

```mermaid
---
title: "Components: Ingestion CLI"
---
flowchart LR
    ops(["Ingestion operator<br/>[Person]"]):::person
    files["Policy corpus<br/>[External System: files]"]:::external
    openai["OpenAI API<br/>[External System]"]:::external
    chunks[("Chunks collection<br/>and vector index<br/>[Data store]")]:::store
    parents[("Parent sections<br/>collection<br/>[Data store]")]:::store

    subgraph CLI["Ingestion CLI [Container: Python]"]
        direction LR
        entry["Register loader<br/>[Component: services/ingestion.py]<br/>requests_from_register, ingest"]:::component
        parse["parse<br/>[Component: chains/parsing.py]<br/>Title, text and table elements"]:::component
        chunk["chunk<br/>[Component: chains/chunking.py]<br/>chunk_by_clause"]:::component
        enrich["enrich<br/>[Component: chains/ingestion.py]<br/>Header, metadata, chunk_id,<br/>parent_id, checksum"]:::component
        compare["compare<br/>[Component: chains/ingestion.py]<br/>Checksums against stored chunks"]:::component
        upsert["embed_and_upsert<br/>[Component: chains/ingestion.py]<br/>Embeds changed chunks only"]:::component
        stale["delete_stale_chunks<br/>[Component: database/mongo.py]"]:::component
    end

    ops -->|"python -m app.services.ingestion [shell]"| entry
    entry -->|"Reads register [filesystem]"| files
    entry -->|"One IngestRequest per document"| parse
    parse -->|"Reads PDF [pdfplumber]"| files
    parse -->|"Elements"| chunk
    chunk -->|"Clause chunks"| enrich
    enrich -->|"Prepare parent sections in memory"| upsert
    enrich -->|"Child documents"| compare
    compare -->|"Reads stored checksums [pymongo]"| chunks
    compare -->|"Changed chunks"| upsert
    upsert -->|"Embeds text [HTTPS]"| openai
    upsert -->|"Upserts chunks [pymongo]"| chunks
    upsert -->|"Then upserts parents and removes obsolete sections [pymongo]"| parents
    entry -->|"After each document"| stale
    stale -->|"Deletes chunks the source no longer has [pymongo]"| chunks

    classDef person fill:#08427b,stroke:#93c5fd,color:#ffffff,stroke-width:2px
    classDef component fill:#bfdbfe,stroke:#1d4ed8,color:#0f172a,stroke-width:2px
    classDef store fill:#2563eb,stroke:#bfdbfe,color:#ffffff,stroke-width:2px
    classDef external fill:#475569,stroke:#cbd5e1,color:#ffffff,stroke-width:2px
    style CLI fill:none,stroke:#8b949e,stroke-width:2px,stroke-dasharray:6 4
    linkStyle default stroke:#8b949e,stroke-width:2px
```

#### Dynamic diagram: answering one question

Scope: one `POST /answer` request, shown as a sequence between the containers and components above. Dynamic diagrams are for recurring or complicated interactions, and this is the one that matters most. It uses the sequence style, which the C4 model allows.

```mermaid
---
title: "Dynamic: POST /answer"
---
sequenceDiagram
    autonumber
    actor Staff as NexaBank staff
    participant API as NexaAssist API
    participant RS as Retrieval service
    participant AT as MongoDB Atlas
    participant OAI as OpenAI API
    participant AC as Answer chain

    Staff->>API: POST /answer (query, as_of, optional product_id) [HTTP/JSON locally]
    API->>AC: answer_policy builds the chain
    AC->>RS: retrieve_policy(request)
    RS->>OAI: embed the query [HTTPS]
    OAI-->>RS: query vector
    RS->>AT: scoped dense or configured hybrid search [pymongo]
    AT-->>RS: top_k child chunks
    RS->>AT: read parent sections by parent_id [pymongo]
    AT-->>RS: parent sections
    RS->>RS: reject parents with mismatched source scope
    RS->>AT: load linked effective circulars and check complete indexed metadata
    AT-->>RS: complete indexed circulars, including applicability
    RS-->>AC: ranked results, amendments, citations and timings
    alt no usable evidence or complete amendment cannot fit the budget
        AC-->>API: insufficient_evidence, no citations
    else hits found
        AC->>AC: budget amendments first, dedupe context and assign evidence IDs
        AC->>OAI: structured-output request with as_of, question and numbered context [HTTPS]
        OAI-->>AC: AnswerDraft (status, answer, evidence quotes)
        AC->>AC: check [n] markers equal evidence ids and every quote appears in its parent text
        opt citation validation fails on the first draft
            AC->>OAI: one repair draft using the same evidence
            OAI-->>AC: replacement draft
            AC->>AC: repeat the same citation checks
        end
        AC-->>API: answered, fixed needs_clarification question, or insufficient_evidence
    end
    API-->>Staff: AnswerResponse (status, answer, citations, timings) [HTTP/JSON locally]
```

With `/answer/evaluated`, the service retrieves once, generates from that stored
evidence, and scores only the final response. The ordinary `/answer` path does
not run RAGAS. The model interprets policy precedence from the supplied sources;
the application does not hardcode fee values or act as a general rule engine.

#### C4 checklist applied

| Check | How these diagrams meet it |
|---|---|
| Title | Every diagram has a title naming its type and scope |
| Type and scope | The type (landscape, context, container, component, dynamic) and the scoped system or container are in the title and the text above each diagram |
| Key | One shared key covers shapes, colors, solid and dashed arrows |
| Elements | Each shows a name, a type in brackets, the technology where it matters, and a short description |
| Relationships | Arrows are unidirectional, labeled with their purpose and `[technology]` for cross-process calls |
| Acronyms | Explained under the key |
| Theme | Fills, borders and text colors are explicit, with white text on dark fills and dark text on light fills, so both light and dark themes stay readable |

---

## 2. Project layout

```text
.
├── README.md                      Setup, contracts and current status
├── APP_COMPONENT_GUIDE.md         File-by-file implementation walkthrough
├── DATADOG_README.md              Docker Agent, traces, RAGAS and feedback
├── DEVELOPMENT_TOOLING_GUIDE.md   Development skills, hooks and MCP usage
├── CLAUDE.md                      Project layer rules
├── pyproject.toml / uv.lock        Dependencies and tool configuration
├── .env.example                   Safe configuration template
├── notebooks/                     Standalone lessons and ingestion walkthrough
├── evaluation/datasets/           Ten retrieval and ten answer teaching cases
├── app/
│   ├── core/                      Settings, JSON logging, telemetry and token/latency logging
│   ├── models/                    Ingestion, retrieval, answer, evaluation and feedback contracts
│   ├── chains/                    Parsing, chunking, ingestion, retrieval, context and generation
│   ├── database/                  MongoDB stores, hybrid/circular reads, feedback and index JSON
│   ├── services/                  Ingestion, retrieval, answers, live evaluation and feedback
│   ├── api/                       Thin routers, reviewer authentication and operational endpoints
│   ├── evaluation/                Diagnosis, batch/live scoring and saved result artifacts
│   ├── ui/app.py                  Streamlit chat and reviewer workspace
│   ├── .streamlit/config.toml     UI theme
│   ├── main.py                    FastAPI entry point
│   ├── streamlit_app.py           Streamlit entry point
│   ├── Dockerfile                 API image with evaluation dependencies
│   └── compose.datadog.yaml       Docker Datadog Agent for the local applications
├── tests/                         Controlled application and UI regression tests
└── nexa_synthetic_data/
    ├── document_register.json     Authoritative sources, versions and effective windows
    ├── policies/                  Policy/circular PDFs and training companions
    ├── golden_qa/                 97 source QAs; some require future application layers
    ├── catalog/ customers/ uploads/ api/
    └── README.md                  Supplied dataset assets and regeneration limits
```

### Layer rules

- `chains/` has no FastAPI or Streamlit imports.
- `database/` is the only place that creates a MongoDB client.
- `services/` bridges chains to the ingestion command and FastAPI.
- Settings are read once through `app/core/config.py`. No other module calls `os.getenv`.

### Module dependencies

```mermaid
flowchart TD
    SVC["services/ingestion.py"] --> CHAIN["chains/ingestion.py"]
    SVC --> EMB["chains/embeddings.py"]
    SVC --> DB["database/mongo.py"]
    SVC --> MODELS["models/ingestion.py"]
    SVC --> CFG["core/config.py"]
    SVC --> LOG["core/logging.py"]
    SVC --> TEL["core/telemetry.py"]

    CHAIN --> PARSE["chains/parsing.py"]
    CHAIN --> CHUNKING["chains/chunking.py"]
    CHAIN --> MODELS
    CHAIN --> CFG

    EMB --> CFG
    DB --> CFG
```

---

## 3. How a document flows through the system

One run of `python -m app.services.ingestion <register>` does this:

```mermaid
sequenceDiagram
    autonumber
    participant CLI as services.ingestion
    participant REG as document_register.json
    participant CH as Ingestion chain
    participant PDF as parse_document
    participant UNS as chunk_by_clause
    participant OAI as OpenAI Embeddings
    participant DB as Atlas: chunks
    participant PSEC as Atlas: parent sections

    CLI->>REG: read entries
    REG-->>CLI: register entries
    CLI->>CLI: requests_from_register filters authoritative entries and builds IngestRequests
    loop for each IngestRequest
        CLI->>CH: ainvoke(request)
        CH->>CH: reject request scope that differs from configuration
        CH->>PDF: parse
        PDF-->>CH: Title, NarrativeText, Table elements
        CH->>UNS: chunk
        UNS-->>CH: one chunk per clause (tables kept with their clause)
        CH->>CH: enrich: header line, metadata, chunk_id, parent_id, checksum
        CH->>DB: compare: read stored chunks by chunk_id
        DB-->>CH: chunks with metadata and checksums
        CH->>PSEC: read existing exact-document parent sections
        PSEC-->>CH: parents with scope metadata
        CH->>CH: reject conflicting or missing stored scope before writing
        alt changed chunks exist
            CH->>OAI: embed changed chunks
            OAI-->>CH: vectors
            CH->>DB: upsert changed chunks by chunk_id
        else all chunks are unchanged
            CH->>CH: skip embedding and vector upsert
        end
        CH->>PSEC: upsert current parents, then remove obsolete section keys
        CH-->>CLI: IngestResponse
        CLI->>DB: delete_stale_chunks: retire obsolete chunks in the configured corpus
        CLI->>CLI: log document_ingested
    end
```

### What each stage does

| # | Stage | Function | Result |
|---|---|---|---|
| 1 | Load | `requests_from_register` | One `IngestRequest` per authoritative document in the register. Files not in the register are never indexed. |
| 2 | Parse | `parse_document` | Ordered `Title`, `NarrativeText` and `Table` elements. Tables are extracted whole as HTML; page headers and footers are dropped. |
| 3 | Chunk | `chunk_by_clause` | One chunk per numbered clause. A table or footnote stays with the clause that introduces it, and a table is never split. Only a clause over `CHUNK_MAX_CHARACTERS` is split, at sentence boundaries, with `CHUNK_OVERLAP_CHARACTERS` of overlap. |
| 4 | Enrich + hash | `enrich` (inside the chain) | A LangChain `Document` per child chunk: identity header line, deterministic `chunk_id`, `parent_id`, clause fields, metadata and a `checksum`. Also one parent `Document` per section. |
| 5 | Compare | `compare` (inside the chain) | Rejects colliding chunks and existing exact-document parents with conflicting or missing identity/scope metadata, then selects children whose checksum changed. |
| 6 | Embed + upsert | `embed_and_upsert` (inside the chain) | Changed child vectors are written first, then parents are upserted and obsolete sections removed. Embedding failure preserves previous parents. |
| 7 | Retire | `delete_stale_chunks` (called by the service) | Deletes obsolete chunks of this document only when institution, jurisdiction and confidentiality match the configured corpus. |

Run one ingestion job at a time: chunk and parent writes span collections without
a transaction. Stored IDs remain unchanged. An identity collision or missing scope
stops ingestion before embedding or writing; inspect and back up those records
before correcting their provenance or choosing a distinct document ID. This guard
does not turn shared collections into a concurrent multi-tenant write service.

---

## 4. Classes and functions

### Class diagram

This view focuses on ingestion contracts. Answer, evaluation and feedback
contracts are covered in the request/response sections and the component guide.

```mermaid
classDiagram
    class Settings {
        +SecretStr mongodb_uri
        +str mongodb_db
        +str mongodb_collection
        +str vector_index_name
        +str mongodb_parent_collection
        +SecretStr openai_api_key
        +str embedding_model
        +int embedding_dimensions
        +int embedding_batch_size
        +int chunk_max_characters
        +int chunk_overlap_characters
        +str institution
        +str jurisdiction
        +str confidentiality_level
        +str log_level
        +bool dd_trace_enabled
    }

    class IngestRequest {
        +str source_path
        +str doc_id
        +str title
        +str institution
        +str product_id
        +str policy_version
        +date effective_date
        +date effective_to
        +str supersedes
        +str jurisdiction
        +str doc_type
        +str confidentiality_level
    }

    class IngestResponse {
        +str doc_id
        +int chunk_count
        +list~str~ chunk_ids
        +int embedded_count
        +int unchanged_count
        +str embedding_model
    }

    class Chunk {
        +str text
        +int section_number
        +str section
        +str clause
        +str clause_heading
        +bool has_table
    }

    class BaseSettings
    class BaseModel

    BaseSettings <|-- Settings
    BaseModel <|-- IngestRequest
    BaseModel <|-- IngestResponse
    IngestRequest ..> IngestResponse : chain turns one into the other
```

Both models use `extra="forbid"`, so an unknown or misspelled field raises a validation error instead of being silently dropped.

### `app/core/`

| Item | Signature | Purpose |
|---|---|---|
| `Settings` | class, extends `BaseSettings` | Validated backend configuration from `.env`. `SecretStr` redacts normal representations; extracted secret values must still be kept out of logs. SDK tracing variables are read separately by the SDKs. |
| `get_settings` | `() -> Settings` | Cached accessor; the app builds `Settings` once. |
| `configure_logging` | `(level: str) -> None` | Configures `structlog` to write JSON lines to stdout, ready for Datadog. |
| `init_telemetry` | `(enabled: bool) -> None` | Calls `ddtrace.patch_all()` when enabled so PyMongo is traced. Does nothing when disabled. |

### `app/models/ingestion.py`

| Item | Purpose |
|---|---|
| `IngestRequest` | The Ingestion Service contract: one source document plus its document-level metadata. |
| `IngestResponse` | What an ingest returns: the document, how many chunks, their IDs, how many were embedded and how many were skipped as unchanged, and the embedding model used. |

### `app/chains/`

| Item | Signature | Purpose |
|---|---|---|
| `parse_document` | `(path: str) -> list[Element]` | Entry point for parsing. PDFs go to `_parse_pdf`; other formats go to `unstructured.partition`. |
| `_parse_pdf` | `(path: str) -> list[Element]` | Uses `pdfplumber`. Tables come from ruling lines, headings from font size larger than the body text, and header and footer lines are cropped by page margin. Output is in reading order. |
| `_table_element` | `(rows) -> Table` | Builds a `Table` element with an HTML rendering, so each value stays attached to its column. |
| `get_embeddings` | `(settings) -> OpenAIEmbeddings` | Pins model, dimension and batch size from settings. The read path must use the same values. |
| `Chunk` | frozen dataclass | One child chunk before enrichment: text, section, clause number and heading, and whether it holds a table. |
| `chunk_by_clause` | `(elements, max_characters, overlap) -> list[Chunk]` | Hybrid chunking. Structure first: one chunk per numbered clause, tables kept with their clause. Size second: an oversized clause is split with `RecursiveCharacterTextSplitter`, tables staying whole. |
| `chunk_checksum` | `(text, metadata) -> str` | SHA-256 over the chunk text and metadata. The metadata includes `embedding_model`, so a model change forces a re-embed. |
| `parent_key_prefix` | `(doc_id) -> str` | Key prefix of a document's parent sections (`POL-HL-V3_s`). |
| `build_ingestion_chain` | `(vector_store, parent_store, settings) -> Runnable` | Composes the write path as one LCEL chain: `parse`, `chunk`, `enrich`, `compare`, `embed_and_upsert`. Input is `{"request": IngestRequest}`; run it with `ainvoke`. Each stage has a `run_name` to identify it during chain inspection. It depends only on the `VectorStore` and `BaseStore` interfaces, so tests pass in-memory stores. |

### `app/database/mongo.py`

| Item | Signature | Purpose |
|---|---|---|
| `get_vector_store` | `(settings, embedding) -> MongoDBAtlasVectorSearch` | Returns the chunk store with collection, embedding and index name passed explicitly. It never creates the index; that is manual (see [Section 6](#6-getting-started)). |
| `get_parent_store` | `(settings) -> MongoDBDocStore` | Key-value store of whole sections, keyed by `parent_id`, in the `MONGODB_PARENT_COLLECTION` collection. Not embedded, no search index. |
| `delete_stale_chunks` | `(collection, doc_id, keep_ids, *, settings) -> int` | Deletes obsolete chunks only within the corpus configured by `settings`. Returns how many were removed. |

### `app/services/ingestion.py`

| Item | Signature | Purpose |
|---|---|---|
| `DOC_TYPES` | `dict[str, str]` | Maps a `doc_id` prefix to a document type: `POL` is `policy`, `CIR` is `circular`. |
| `requests_from_register` | `(register_path, settings) -> list[IngestRequest]` | Reads `document_register.json`, keeps authoritative entries, and fills `institution`, `jurisdiction` and `confidentiality_level` from settings. |
| `ingest` | `async (requests) -> list[IngestResponse]` | Builds the stores and chain once, then for each request runs the chain, retires stale chunks, and logs one `document_ingested` event with `embedded_count`, `unchanged_count` and `stale_chunks_removed`. |
| `main` | `() -> None` | Command-line entry point. Takes the register path as its only argument. |

---

## 5. Data model: the chunk record

Each child chunk is stored in MongoDB as one document. Metadata sits at the top level beside the text and vector.

```json
{
  "_id": "POL-HL-V3_3.4_c01",
  "text": "[POL-HL-V3 | NexaHome Loan Policy v3.0 | 3. Eligibility]\n3.4 FOIR limit. The maximum FOIR depends on net monthly income as shown below.\n<table><tr><td>Net monthly income</td><td>Maximum FOIR</td></tr><tr><td>Below ₹1,00,000</td><td>50%</td></tr> ...</table>",
  "embedding": [0.012, -0.044, "... 1536 floats"],

  "doc_id": "POL-HL-V3",
  "title": "NexaHome Loan Policy",
  "chunk_id": "POL-HL-V3_3.4_c01",
  "parent_id": "POL-HL-V3_s03",
  "section": "3. Eligibility",
  "clause": "3.4",
  "clause_heading": "FOIR limit",
  "institution": "NexaBank",
  "product_id": "NEXA-HL",
  "policy_version": "3.0",
  "effective_date": "2025-10-01",
  "effective_to": null,
  "supersedes": "POL-HL-V2",
  "jurisdiction": "IN",
  "doc_type": "policy",
  "section_type": "table",
  "confidentiality_level": "internal",
  "embedding_model": "text-embedding-3-small",
  "checksum": "sha256:2f50cb38..."
}
```

The nine metadata fields from the training deck are `institution`, `product_id`, `policy_version`, `effective_date`, `supersedes`, `jurisdiction`, `doc_type`, `section_type` and `confidentiality_level`. Eight come from the request; `section_type` (`text` or `table`) is set per chunk.

Session 2 adds:

| Field | Meaning |
|---|---|
| header line in `text` | `[doc_id \| title vversion \| section]`. It is embedded with the clause, so a chunk read on its own still says where it is from. |
| `chunk_id` | `{doc_id}_{clause}_c{nn}`. Built from the clause, not a running position, so inserting a clause does not renumber (and re-embed) the rest. Text outside a numbered clause uses the section number, for example `POL-HL-V3_0_c01` for the front matter. |
| `clause`, `clause_heading`, `section` | The citable unit. The golden Q&A set cites sources as `{doc_id, clause}`. |
| `parent_id` | Key of the whole section in the parent collection: `{doc_id}_s{NN}`. |
| `checksum` | SHA-256 of text + metadata (including `embedding_model`). Ingestion skips a chunk whose stored checksum matches. |
| `effective_to_search` | ISO upper bound for Atlas as-of filtering; `9999-12-31` for open-ended policies while source `effective_to` stays null. |

### The parent record

Each section is stored once, as plain text, in the collection named by `MONGODB_PARENT_COLLECTION` (default `policy_sections`). It has no embedding. `/retrieve` searches children and returns the matching parent text.

```json
{
  "_id": "POL-HL-V3_s06",
  "page_content": "[POL-HL-V3 | NexaHome Loan Policy v3.0 | 6. Fees and charges]\n6.1 General. ...\n6.2 Processing fee. ...\n6.5 Other costs. ...",
  "parent_id": "POL-HL-V3_s06",
  "section": "6. Fees and charges",
  "doc_id": "POL-HL-V3",
  "policy_version": "3.0",
  "effective_date": "2025-10-01",
  "...": "the other document-level fields"
}
```

### Where the metadata comes from

```mermaid
flowchart LR
    REG["document_register.json"] -->|"doc_id, title, product_id,<br/>version, effective window,<br/>supersedes"| REQ["IngestRequest"]
    ENV[".env"] -->|"institution, jurisdiction,<br/>confidentiality_level"| REQ
    PFX["doc_id prefix"] -->|"doc_type"| REQ
    REQ -->|"model_dump"| META["chunk metadata"]
    PIPE["pipeline"] -->|"chunk_id, parent_id, clause,<br/>section_type, embedding_model,<br/>checksum"| META
```

---

## 6. Getting started

### Prerequisites

- **GNU Make and a POSIX shell** on macOS/Linux (or WSL on Windows). On macOS,
  install the Xcode Command Line Tools if `make` is missing.
- [`uv`](https://docs.astral.sh/uv/) installed. `make setup` selects Python 3.14
  and installs the locked application, UI, evaluation and development dependencies.
- An **OpenAI API key** with access to the configured embedding/chat/evaluation models.
- A reachable **MongoDB Atlas** cluster that supports Vector Search, a database
  user with permissions for the policy and workflow collections/indexes, and your
  machine's IP allowed by Atlas network access. Create the search indexes in
  [app/database/indexes.md](app/database/indexes.md) and ingest the corpus before use.
- **Service requests:** a nonempty `BANKING_API_TOKEN` plus distinct named owner
  and reviewer credentials in `AGENT_PRINCIPALS`, with the required customer access.
  The reviewer needs `can_approve=true`. See the
  [credential guide](C4-Documentation/SERVICE_REQUESTS_GUIDE.md).
- **Docker with Compose v2** and a running daemon for Datadog or the optional
  reranker. Datadog needs `DD_API_KEY`/`DD_SITE`; the reranker needs the verified
  GGUF file specified by `RERANKER_MODEL_FILE`.
- **curl** for `make health`, and free local ports **8000, 8080, 8001 and 8501**.

### Run the services with Make

Run these commands from the repository root. If the directory is synced by
Google Drive, set `UV_PROJECT_ENVIRONMENT` to a local virtual-environment
directory before setup, as described in the installation note below.

```bash
make help
make setup
# Edit .env with your credentials, corpus scope and owner/reviewer configuration.
# Create the Atlas search indexes using app/database/indexes.md.
make prereqs
```

`make setup` creates `.env` from `.env.example` only when it is missing; an
existing file is preserved. Shell activation of `.venv` is unnecessary.

`make prereqs` checks local runtime imports, Python version and the existing
Pydantic settings schemas. Credential values are hidden on validation failure.
It does not contact Atlas or OpenAI, confirm search-index readiness, or grant
customer/reviewer permissions. Replace template credentials and configure the
four service URLs shown in [Streamlit and endpoint coverage](#streamlit-and-endpoint-coverage).

For tracing (`DD_TRACE_ENABLED=true`, as in `.env.example`), start Docker and the
Datadog Agent first:

```bash
make datadog-up
```

For a local session without Datadog, set `DD_TRACE_ENABLED=false`,
`DD_LLMOBS_ENABLED=false` and `DD_RUNTIME_METRICS_ENABLED=false` in your local
configuration. If reranking is enabled, start its configured runtime before
retrieval. `make reranker-up` uses the existing llama.cpp compose profile; it
requires `RERANKER_MODEL_FILE` plus the matching `RERANK_API=llamacpp`,
`RERANK_BASE_URL=http://127.0.0.1:18516` and `RERANK_MODEL=qwen3-reranker` settings.
It does not start an Ollama runtime.

After the Atlas indexes are queryable, ingest the registered policies once:

```bash
make ingest       # Calls OpenAI embeddings and writes to Atlas; run one job at a time.
make run          # Or: make run-all
```

`make run` launches the banking mock, RAG API, Agent API and Streamlit together
in the foreground with interleaved logs. Leave this terminal open while they
start. In another terminal, check liveness and open the UI:

```bash
make health
# Open http://127.0.0.1:8501
```

Health checks cover the RAG/Agent `/health` endpoints, the bank's OpenAPI endpoint,
and Streamlit's documented [`/_stcore/health`](https://docs.streamlit.io/deploy/tutorials/docker)
endpoint. A healthy HTTP process still needs working Atlas/provider connections
and indexed policy evidence for requests to succeed.

Press **Ctrl+C** in the `make run` terminal to stop its four processes. If any
required service exits, the launcher stops its siblings and returns failure.
It signals only the processes started by that invocation, using
[uv's Unix signal forwarding](https://docs.astral.sh/uv/concepts/projects/run/#signal-handling).
Datadog and reranker containers stay running until you stop them explicitly.
The synthetic bank's data and idempotency cache are process-local: stopping or
restarting it resets them. Reconcile uncertain submissions before restarting
the bank; MongoDB saved runs alone do not preserve its replay cache.

| Command | Purpose |
|---|---|
| `make setup` | Install locked prerequisites and create missing dotenv configuration |
| `make prereqs` | Check local runtime dependencies and settings schemas |
| `make run` / `make run-all` | Run all four application services; Ctrl+C stops them |
| `make run-rag` | RAG FastAPI on port 8000, Datadog service `nexaassist-api` |
| `make run-bank` | Synthetic bank on port 8080, Datadog service `nexaassist-bank` |
| `make run-agent` | LangGraph FastAPI on port 8001, Datadog service `nexaassist-agent` |
| `make run-ui` | Streamlit on port 8501, Datadog service `nexaassist-ui` |
| `make health` | Check all four HTTP services; nonzero exit if any is unavailable |
| `make ingest` | Embed/index the authoritative register; explicit Atlas write |
| `make datadog-up` / `make datadog-down` | Start/stop the existing Datadog compose service |
| `make reranker-up` / `make reranker-down` | Start/stop the optional verified GGUF reranker |
| `make check-fast` / `make check-task` / `make check-full` | Existing local quality gates |
| `make check-ai` | Existing live scored evaluation gate; configured providers/Atlas required |

All new runtime/container targets accept `ENV_FILE`, for example
`make run ENV_FILE=".env.local"`. Existing quality-gate targets keep their current
configuration behavior. Bare `make` still runs `check-fast`.
If a port is occupied, inspect the existing listener or stop its own terminal
before launching a second stack. For the Datadog sampling-rule JSON error,
remove empty `DD_TRACE_SAMPLING_RULES`/`DD_SPAN_SAMPLING_RULES` values or set them
to the valid JSON array `[]`.

### Step 1: install

```bash
make setup
```

> **Heads-up:** `uv sync` creates `.venv` inside the project folder. If the folder is synced by Google Drive, point it elsewhere first:
> `export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/nexaassist"`

### Step 2: configure

`make setup` creates the template only when `.env` is missing. Edit `.env`.
These are required:

| Variable | Example | Meaning |
|---|---|---|
| `MONGODB_URI` | `mongodb+srv://user:pass@cluster.mongodb.net` | Atlas connection string |
| `MONGODB_DB` | `nexaassist` | Database name |
| `MONGODB_COLLECTION` | `policy_chunks` | Collection that holds chunks |
| `OPENAI_API_KEY` | `sk-...` | Embeddings, answer generation and model-based evaluation |
| `INSTITUTION` | `NexaBank` | Stamped on every chunk and enforced on retrieval/feedback |
| `JURISDICTION` | `IN` | Stamped on every chunk |
| `CONFIDENTIALITY_LEVEL` | `internal` | Stamped on every chunk |

These have defaults and are optional:

| Variable | Default | Meaning |
|---|---|---|
| `VECTOR_INDEX_NAME` | `policy_chunks_v1` | Must match the index you create in Atlas |
| `MONGODB_PARENT_COLLECTION` | `policy_sections` | Collection that holds whole sections (parent documents) |
| `MONGODB_FEEDBACK_COLLECTION` | `policy_feedback` | Feedback and reviewer decisions |
| `FEEDBACK_REVIEWER_TOKEN` | unset | At least 32 characters; required to enable feedback listing/review |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Pinned; never mix models in one index |
| `EMBEDDING_DIMENSIONS` | `1536` | Must match the Atlas index |
| `EMBEDDING_BATCH_SIZE` | `100` | Chunks per embedding request |
| `CHAT_MODEL` | `gpt-6-luna` | Answer-generation model |
| `EVAL_JUDGE_MODEL` | unset; falls back to `CHAT_MODEL` | Judge/RAGAS model; `.env.example` explicitly selects `gpt-6-sol` |
| `RETRIEVAL_MODE` / `TEXT_INDEX_NAME` | `dense` / `policy_text_v1` | Hybrid mode needs its separate manual text index |
| `RETRIEVAL_CANDIDATES` / `RERANK_ENABLED` | `20` / `false` | Candidate pool when reranking is enabled; score compatibility must pass first |
| `CONTEXT_MAX_TOKENS` / `TOKEN_ENCODING` | `6000` / `o200k_base` | Serialized evidence budget, excluding the rest of the prompt and output |
| `API_BASE_URL` / `UI_TIMEZONE` | `http://127.0.0.1:8000` / `Asia/Kolkata` | Streamlit connection and policy-date default |
| `CHUNK_MAX_CHARACTERS` | `1200` | Target size. Only a clause above it is split; a table can exceed it |
| `CHUNK_OVERLAP_CHARACTERS` | `150` | Text repeated between the pieces of a split clause |
| `LOG_LEVEL` | `INFO` | `structlog` level |
| `DD_TRACE_ENABLED` | `false` | Turn on only with a Datadog Agent running |

Datadog is the application's telemetry destination. LangSmith imports, decorators
and connection settings have been removed. The `langsmith` package remains an
indirect dependency required by LangChain; it is not configured as an application
telemetry destination.

### Step 3: create the Atlas Vector Search index (once, by hand)

Create the index named `policy_chunks_v1` using the exact JSON in
[app/database/indexes.md](app/database/indexes.md), then wait until it is queryable.
The definition includes `institution` as well as both sides of the effective-date
window. An existing index must include every filter field used by the API.
Adding a filter mapping alone does not require re-embedding when the stored
metadata is already present. Older chunks missing `effective_to_search` need
re-ingestion. Changing the embedding model or dimensions requires a new index
version and a full re-embedding.

### Step 4: start the Docker Datadog Agent for tracing

Set `DD_API_KEY` and the correct `DD_SITE` in `.env`. On this Mac, start Docker
Desktop and wait for `docker info` to succeed, then run:

```bash
make datadog-up
docker compose --env-file .env -f app/compose.datadog.yaml ps
```

The API and UI run on the Mac and send traces to
`DD_TRACE_AGENT_URL=http://127.0.0.1:18126`; runtime metrics use
`DD_DOGSTATSD_URL=udp://127.0.0.1:18125`. This setup uses the Docker Agent.
Start the applications with the commands in
[Streamlit and endpoint coverage](#streamlit-and-endpoint-coverage), which load
`.env` before `ddtrace-run`. See [the Datadog guide](DATADOG_README.md) for Agent
diagnostics, log collection and cloud setup. The reviewer token is a separate
application credential, not the Datadog API key.

---

## 7. Using the project

### How to execute the code

Run everything from the project root (the folder that contains `pyproject.toml`). There are three ways to run it, depending on what you have.

```mermaid
flowchart TD
    START{"Do you have OpenAI and<br/>Atlas credentials?"}
    START -->|Yes| A["Option A: full ingestion<br/>writes to Atlas"]
    START -->|No| B{"What do you want to see?"}
    B -->|"Prove the code works"| C["Option B: run the tests"]
    B -->|"See the pipeline run"| D["Option C: offline dry run<br/>or the notebook"]
```

| Option | Needs credentials | Writes to Atlas | Use it to |
|---|---|---|---|
| A. Full ingestion | Yes | Yes | Load the corpus for real |
| B. Tests | No | No | Check parsing, chunking, metadata and idempotency |
| C. Offline dry run | No | No | Watch all eight PDFs go through the real chain with fake embeddings |

#### Option A: full ingestion (needs `.env` and the Atlas index)

Complete [Section 6](#6-getting-started) first, then:

```bash
uv run --locked --no-sync --env-file .env ddtrace-run python -m app.services.ingestion nexa_synthetic_data/document_register.json
```

What happens, in order: settings load from `.env`, logging is configured, the register is read, and each of the eight documents is parsed, chunked, embedded and upserted. It finishes after eight `document_ingested` log lines (see below). To ingest a different register or a copy of it, pass its path as the argument.

#### Retrieval API (needs `.env`, re-ingested chunks, and the rebuilt index)

```bash
uv run --no-sync --env-file .env ddtrace-run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
curl -X POST http://127.0.0.1:8000/retrieve -H 'Content-Type: application/json' \
  -d '{"query":"What is the NexaHome processing fee?","as_of":"2026-10-01","top_k":5,"product_id":"NEXA-HL","jurisdiction":"IN"}'
```

`/retrieve` returns ranked child passages in `results`, complete linked circulars
in `amendments`, citations, parent text and timings in milliseconds. `as_of` is
required; `top_k` defaults to 5 and limits only `results` to 1–20 similarity hits.
Amendments are supplemental evidence, with score 0 rather than a similarity
score. Their null clause identifies a complete indexed instrument. The chain
uses institution/date/jurisdiction/confidentiality and optional product scope;
an invalid or missing parent falls back to its child. `parent_ms` includes
parent expansion and amendment lookup.

Invalid bodies return 422; retrieval failures or inconsistent amendment metadata
return a generic 503. `GET /health` reports process liveness, `/metrics` returns
process-local counters, and `/metrics/latency` returns sampled retrieval percentiles.

Answer/retrieval and feedback submission remain available without user login in
this localhost training app. Feedback listing/review require a reviewer bearer
token. These controls do not provide individual identities or tenant roles.
The lookup date selects effective documents; the model interprets clause-level
amendments and sanction/opt-in conditions from their text. It is not a general
policy-precedence engine.

#### Session 5: RAG production failure modes

Retrieval is already implemented. The next lesson reproduces **retrieval misses, stale indexed chunks, and embedding compatibility failures** using the existing response models and ingestion checksum function:

```bash
uv run python -m app.evaluation.rag_failure_modes
```

The command emits one structured JSON log with healthy and deliberately faulty examples. It does not load `.env`, contact Atlas or OpenAI, download a model, or change indexed data.

For thirteen examples using both real Atlas collections (four retrieval, five
staleness and four embedding cases), use the
[Session 5 runner](app/evaluation/rag_failure_examples.py).

The [Session 5 teaching deck](../PPTs/5.NexaAssist_Week1_Session5.pptx) lives in the
sibling `PPTs` directory of the training workspace.

Run the examples from the project root:

```bash
uv run python -m app.evaluation.rag_failure_examples       # all 13 cases
uv run python -m app.evaluation.rag_failure_examples R3    # child citations versus parent context
uv run python -m app.evaluation.rag_failure_examples S5    # missing/stale parent, local replay
```

This second runner reads the configured chunk and parent collections and makes OpenAI embedding calls for retrieval. Fault injections modify local copies only. It never changes stored data or indexes. The parent-aware observation in `app/evaluation/datasets/session5_atlas_parent_observation.json` includes 177 chunks, 80 parent sections, source freshness and both explicit-citation and expanded-context coverage. Ranks and scores can vary on another run.

| Function in `app/evaluation/rag_failure_modes.py` | Inputs | What it reports |
|---|---|---|
| `diagnose_retrieval` | Existing `RetrieveResponse` and expected document/clause references | Empty results and nonempty results that omit expected evidence |
| `diagnose_expanded_context` | Response, expected references and stored source chunks | Verbatim required-clause availability inside returned `parent_text`, separately from child citations |
| `diagnose_parent_links` | Stored child documents and parent map | Missing parents, policy-scope mismatches, missing child bodies and unreferenced parents |
| `diagnose_staleness` | Current source and stored `ChunkSnapshot` manifests | Missing, changed-checksum, and unexpected chunk IDs |
| `diagnose_embeddings` | Stored snapshots, expected embedding model and dimension | Model/dimension mismatches; an empty snapshot is not evaluated |

The functions can also consume real snapshots supplied by a caller, but this lesson does not add a live Atlas audit adapter. A source snapshot must be regenerated from current source chunks with the existing `chunk_checksum(text, metadata)` logic, excluding the `checksum` field itself. Compare complete manifests covering the same corpus and configuration; a sample cannot prove the whole index fresh. Stored dimensions must describe the actual vector length. `diagnose_staleness` checks stored chunk freshness, not Atlas Search index readiness or parent-section consistency.

Matching model names and dimensions establishes compatibility, **not absence of statistical embedding drift**. The report explicitly returns `distribution_drift: not_measured`. To investigate behavioral drift, compare the same golden queries and expected citations across versioned runs, then inspect corpus and incoming-query changes. No drift threshold is assumed.

The existing `/metrics` endpoint exposes process-local JSON counters.
`/metrics/latency` provides nearest-rank p50/p95/p99 retrieval timings from the last
1,000 successful requests in this process. Neither detects nonempty retrieval
misses. Fleet aggregation, latency histograms, alert thresholds, scheduled
ingestion and Atlas index-readiness checks remain production work.

#### Option B: run the tests (no credentials)

```bash
uv run pytest -q
```

The offline suite covers ingestion, retrieval, reranking, grounded answers,
evaluation, telemetry, feedback and Streamlit interactions. It uses controlled
provider responses and local stores; it does not measure live Atlas relevance.

#### Option C: offline dry run (no credentials)

Save this as `dry_run.py` in the project root and run `uv run python dry_run.py`. It uses the real parser and chain, but an in-memory store and fake embeddings, so nothing leaves your machine.

```python
import asyncio
from pathlib import Path

from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.stores import InMemoryStore
from langchain_core.vectorstores import InMemoryVectorStore

from app.chains.ingestion import build_ingestion_chain
from app.core.config import Settings
from app.services.ingestion import requests_from_register

settings = Settings(
    _env_file=None,
    mongodb_uri="mongodb://unused", mongodb_db="unused", mongodb_collection="unused",
    openai_api_key="unused",
    institution="NexaBank", jurisdiction="IN", confidentiality_level="internal",
)
store = InMemoryVectorStore(DeterministicFakeEmbedding(size=1536))
chain = build_ingestion_chain(store, InMemoryStore(), settings)


async def main() -> None:
    register = Path("nexa_synthetic_data/document_register.json")
    for request in requests_from_register(register, settings):
        response = await chain.ainvoke({"request": request})
        print(
            f"{response.doc_id:<13}{response.chunk_count:>4} chunks,"
            f"{response.embedded_count:>4} embedded"
        )
    print("total:", len(store.store))


asyncio.run(main())
```

Expected output:

```text
POL-HL-V1      31 chunks,  31 embedded
POL-HL-V2      31 chunks,  31 embedded
POL-HL-V3      32 chunks,  32 embedded
CIR-2026-07     8 chunks,   8 embedded
POL-PL-V1      18 chunks,  18 embedded
POL-LAP-V1     19 chunks,  19 embedded
POL-AL-V1      17 chunks,  17 embedded
POL-OPS-001    21 chunks,  21 embedded
total: 177
```

The notebook [phase1_session2_chunking.ipynb](notebooks/phase1_session2_chunking.ipynb) does the same thing step by step and falls back to this offline mode automatically.

#### What a successful full run looks like

One JSON log line per document, eight in total:

```json
{"doc_id": "POL-HL-V3", "chunk_count": 32, "embedded_count": 32, "unchanged_count": 0, "stale_chunks_removed": 0, "embedding_model": "text-embedding-3-small", "policy_version": "3.0", "effective_date": "2025-10-01", "event": "document_ingested", "level": "info", "timestamp": "..."}
```

Expected chunk counts for the synthetic corpus:

| Document | Chunks |
|---|---|
| POL-HL-V1, POL-HL-V2 | 31 each |
| POL-HL-V3 | 32 |
| CIR-2026-07 | 8 |
| POL-PL-V1 | 18 |
| POL-LAP-V1 | 19 |
| POL-AL-V1 | 17 |
| POL-OPS-001 | 21 |
| **Total** | **177** |

Each count is the document's numbered clauses plus one front-matter chunk. The parent collection ends up with 80 sections.

Running it again is safe and cheap. A second run logs `"embedded_count": 0` for every document, because every stored checksum matches and no embedding call is made.

**Upgrading a collection that Session 1 filled:** the chunk IDs changed from `POL-HL-V3_c007` to `POL-HL-V3_3.4_c01`. The first Session 2 run embeds all 177 new chunks and reports the 128 old ones under `stale_chunks_removed` as it deletes them. No manual cleanup is needed.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ValidationError ... Field required` naming `mongodb_db`, `openai_api_key`, `institution`, etc. | A required variable is missing from `.env`, or `.env` was not found | Run from the project root and compare `.env` with `.env.example` |
| `command not found: uv` | `uv` is not installed | Install it from https://docs.astral.sh/uv/ |
| `AuthenticationError` from OpenAI | Wrong or placeholder `OPENAI_API_KEY` | Set a real key (`sk-...`) |
| `ServerSelectionTimeoutError` from PyMongo | Bad `MONGODB_URI`, or your IP is not on the Atlas network access list | Check the URI and add your IP in Atlas |
| Corpus count differs from the expected 177 chunks | Wrong collection/scope, changed source/chunk settings, or older data | Compare the register and configured institution; rerun ingestion for changed documents and use explicit retirement for withdrawn ones. Do not clear unrelated corpus data. |
| Vector search later returns nothing | The Atlas index does not exist, is still building, or has a different name or dimension | Check index status; `VECTOR_INDEX_NAME` and `EMBEDDING_DIMENSIONS` must match |
| `libmagic is unavailable` warning | Optional file-type helper not installed | Safe to ignore |
| Tests or commands cannot find `app` | Not run from the project root | `cd` to the folder containing `pyproject.toml` |

### Check the result in Atlas

For the eight registered PDFs and documented chunk settings, expect 177 child
chunks and 80 parent sections in the configured corpus. Each child has a
1536-number embedding with the default model/dimensions and the metadata from
[Section 5](#5-data-model-the-chunk-record). Different source/configuration choices
can change those counts; inspect the configured institution rather than treating
an entire shared collection as one corpus.

### Use the pieces from your own code

```python
import asyncio
from pathlib import Path

from app.core.config import get_settings
from app.services.ingestion import ingest, requests_from_register

settings = get_settings()
requests = requests_from_register(Path("nexa_synthetic_data/document_register.json"), settings)

# Ingest only the circular
circular = [r for r in requests if r.doc_id == "CIR-2026-07"]
responses = asyncio.run(ingest(circular))
print(responses[0].chunk_count)  # 8
```

To ingest a document that is not in the register, build an `IngestRequest` yourself and pass it to `ingest`. All fields except `product_id`, `effective_to` and `supersedes` are required.

### Walk through it as a lesson: the notebook

There is one notebook per session: [phase1_session1_ingestion.ipynb](notebooks/phase1_session1_ingestion.ipynb) (architecture and the Session 1 baseline) and [phase1_session2_chunking.ipynb](notebooks/phase1_session2_chunking.ipynb) (tokens, embeddings, clause-aware chunking, parent documents, checksums, scaling arithmetic). Each is a teaching version of this same pipeline. It does not import `app/`, and it maps each section to the file it mirrors. Open it with the project's environment as the kernel and run it from the project root.

If `MONGODB_URI` and `OPENAI_API_KEY` are not set, it switches to **offline mode** (in-memory store, fake embeddings). Every stage still runs, but the similarity ranking in the last section is arbitrary. With real credentials, that section shows several policy versions competing for the same question, which is the reason version metadata exists.

### Adapting it

| To change | Where |
|---|---|
| Chunk size and overlap | `CHUNK_MAX_CHARACTERS`, `CHUNK_OVERLAP_CHARACTERS` in `.env`, no code change |
| What counts as a clause or section | The two regular expressions at the top of [app/chains/chunking.py](app/chains/chunking.py) |
| Embedding model or dimension | `.env`, then create a new Atlas index version and re-embed |
| The corpus | Add the file to the register, then re-run the command |
| What is stored per chunk | `enrich` inside [app/chains/ingestion.py](app/chains/ingestion.py) |
| Parsing rules | [app/chains/parsing.py](app/chains/parsing.py) |

---

### Next session: from retrieved evidence to an answer

The earlier R1–R4 questions are executable examples in
`app/evaluation/rag_failure_examples.py`. The next session adds answer generation
on top of the same retrieval implementation:

```text
POST /answer (same request schema as /retrieve)
  -> app/api/answer.py                 HTTP validation and safe service errors
  -> app/services/answer.py            Wires the existing retrieval service and chat model
  -> app/chains/answer.py              Retrieval, context assembly, generation, citation checks
     -> app/services/retrieval.py     Existing Atlas retrieval and counters
     -> app/chains/prompts/answer.py  Evidence-only instructions and applicability rules
     -> app/chains/llm.py             ChatOpenAI with the Responses API
  -> app/models/answer.py              Structured answer, quotes, sources and timings
```

`CHAT_MODEL=gpt-6-luna` is the configured default. It uses the existing
`OPENAI_API_KEY`; the existing embedding model and Atlas index are unchanged.
The chat model receives the question and retrieved policy context. Optional
LangChain tracing also records chain inputs and outputs when enabled.

Start the API from the repository root:

```bash
uv run --no-sync --env-file .env ddtrace-run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal, start with a straightforward fact:

```bash
curl -sS -X POST http://127.0.0.1:8000/answer \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is the maximum loan amount for a NexaHome Loan?","as_of":"2026-10-01","product_id":"NEXA-HL","top_k":5}'
```

Then reuse the Session 5 circular experiment:

```bash
curl -sS -X POST http://127.0.0.1:8000/answer \
  -H 'Content-Type: application/json' \
  -d '{"query":"What processing fee does Circular CIR-2026-07 prescribe in revised clause 6.2, and which loans sanctioned from 1 July 2026 does it cover?","as_of":"2026-10-01","product_id":"NEXA-HL","top_k":20}'
```

Compare it with the generic current-fee question at top 5. Retrieval now loads
complete, effective circulars linked to retrieved policy IDs, including their
sanction-date conditions. These appear separately in `amendments`; `top_k` still
limits the similarity hits in `results`. Exact generated wording and the quality
of applicability reasoning still require live evaluation.

Finish with an unsupported question such as "What is the current NRLR?". Its
current value comes from the catalog, which the PDF ingestion path does not
index. The expected behavior is `insufficient_evidence`, rather than guessing a
rate from policy examples. The ten teaching cases, including the historical
boundary and abstention cases, are in `evaluation/datasets/answer.jsonl`. They
are evaluation inputs and expectations, not a claim that generation has passed.

#### Reading the answer response

- `status`: `answered`, `needs_clarification`, or `insufficient_evidence`.
- `answer`: generated text with `[1]`-style evidence markers, or a fixed
  insufficient-evidence message. Product/date conflicts and ambiguous rate
  questions can instead return a fixed, uncited scope question; model-written
  policy claims are not forwarded as clarifications. Clients with
  a fixed status enum must accept the new `needs_clarification` value.
- `citations`: each has `evidence_id`, an exact supporting `quote`, and the
  `matched_child` citation from retrieval. The quote can come from another
  clause in the child's parent section; `matched_child.clause` is not a claim
  that the quote belongs to that clause. A complete linked circular has
  `matched_child.clause: null` and is cited at document level.
- `retrieval_timings`: search, context-fetch (`parent_ms`, including linked
  amendments), and total timings.
- `generation_ms`: prompt/model duration, excluding retrieval; zero for an
  empty retrieval where no generation call is made.

Repeated parent text is deduplicated within a document/version before prompting.
The first matching child's citation is retained for that context. Source IDs
are local to each response and are not MongoDB document IDs.

The application rejects answered drafts with missing/unknown citation IDs,
unmatched inline markers, or quotes absent from the referenced context. It allows
one repair attempt against the same evidence; another invalid draft becomes
`insufficient_evidence`. The reported generation time includes both attempts.
Empty retrieval also skips generation;
upstream provider/database/parser failures return a generic HTTP 503.

These are mechanical citation checks, not a semantic correctness guarantee.
Linked amendment retrieval is deterministic and uses the same institution,
date, product, jurisdiction and confidentiality filters as the initial search.
It follows circular-to-circular links until no new instrument remains and
rejects inconsistent source metadata instead of returning a partial or mixed
instrument. This checks the stored chunks, not whether ingestion omitted a
source section entirely.
The model interprets applicability and precedence from that evidence; there is
no hardcoded fee or general policy-precedence engine. If a complete linked
amendment cannot fit the context budget, generation fails closed rather than
answering from the older clause alone. Missing links or stale source records
still need correction. Inspect the evidence before relying on an answer.

The NexaFlex foreclosure reference uses the source wording **plus GST**.
Its foreclosure clause does not specify a percentage; the processing-fee
clause's 18% must not be assumed to apply to foreclosure.

Official integration references: [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)
and [LangChain structured output](https://docs.langchain.com/oss/python/integrations/chat/openai#structured-output).

### Session 8: evaluate answers and investigate failures

The evaluation CLI reuses `build_answer_chain` and evaluates the exact evidence
used for generation. The ten cases in `evaluation/datasets/answer.jsonl` cover
current policies, amendments, historical boundaries and expected abstentions.

Install the existing UI and evaluation extras together, then begin with one case:

```bash
uv sync --locked --extra ui --extra evaluation
uv run --no-sync --env-file .env ddtrace-run python -m app.evaluation.run --limit 1 --output app/evaluation/results/one_case.json
```

This calls Atlas, the configured generation model, the existing judge and RAGAS.
The runner uses `CHAT_MODEL` for generation and `EVAL_JUDGE_MODEL` for judging and
RAGAS, falling back to `CHAT_MODEL` when the judge setting is unset or blank.
Its report records the actual judge model and explains the limits of automated
judging. Batch and live RAGAS share the Responses API structured-output adapter
and the configured embedding model and dimensions.
The evaluation extra pins `langchain-community==0.3.31` because RAGAS 0.4.3 imports
a wrapper removed in community 0.4.

To run all ten cases, omit `--limit` and choose a separate report path:

```bash
uv run --no-sync --env-file .env ddtrace-run python -m app.evaluation.run --output app/evaluation/results/all_cases.json
```

Reports include generated answers, actual retrieved and reference contexts,
retrieval/generation timings, compact LangChain traces, judge rationales and
four RAGAS scores. Expected abstentions skip RAGAS. An answerable case with no
retrieved context receives zero RAGAS scores; non-finite metrics fail the run.
Out-of-range scores also fail; only floating-point roundoff at zero and one is
normalized. Each external RAGAS metric has a 120-second timeout.

With LLM Observability enabled, each answer has a `policy_answer_evaluation`
workflow span. Judge correctness, groundedness, status matching and the applicable
RAGAS scores are attached to that span. Local reports retain its Datadog IDs.
See [Datadog web traces and evaluations](DATADOG_README.md#view-traces-and-evaluations-on-the-web)
for the application link, filters and export verification.

A completed run without `--min-score` reports `gate.status=not_configured`.
To apply an explicitly chosen teaching threshold:

```bash
uv run --no-sync --env-file .env ddtrace-run python -m app.evaluation.run --min-score 0.8 --output app/evaluation/results/gated_cases.json
```

The gate checks the RAGAS metric means, each case's judge scores and matching
answer/abstention status. A failed gate or pipeline error exits 1. The example
0.8 is a teaching choice, not a calibrated production threshold. A subset result
does not validate the full dataset. Keep separate output paths when preserving
runs: this runner writes to the requested path and can replace an existing file.
Reports contain synthetic policy text and model responses.

For controlled offline checks without provider calls, run
`uv run --no-sync pytest tests/test_answer.py tests/test_evaluation.py`.
Constructed fixtures test wiring and citation behavior; they do not measure
semantic model quality. RAGAS scores and user feedback are submitted to Datadog LLM Observability when enabled.

## 8. Testing and quality checks

The saved 21 October verification passed **121 application tests**, **22 separate
mock API tests**, Ruff and mypy (56 application source files). It also records
twelve targeted live policy cases, browser feedback/input checks, and desktop
and mobile Lighthouse accessibility snapshots scoring 100. The snapshots are
not full accessibility certification. See the
[fix report](app/evaluation/results/fixes_20261021/FIX_REPORT.md) for outcomes and limits.

```bash
RAGAS_DO_NOT_TRACK=true \
uv run --locked --no-sync pytest -q tests/
uv run --locked --no-sync ruff check app tests
uv run --locked --no-sync ruff format --check app tests
uv run --locked --no-sync mypy app
```

The mock banking API suite is separate from the application's default test
discovery. Use the dependencies and command in
[the synthetic kit guide](nexa_synthetic_data/README.md); its 22 tests do not
establish that banking customer tools are integrated with the policy UI.

| Regression area | Code covered |
|---|---|
| Scoped retrieval and complete circulars | `tests/test_retrieval.py`, `tests/test_amendments.py`, `tests/test_hybrid_rerank.py` |
| Context budgets, exact citations and one repair attempt | `tests/test_answer.py` |
| Live/batch scoring, reference handling and unavailable metrics | `tests/test_evaluation.py`, `tests/test_live_evaluation.py` |
| Reviewer authorization, retry IDs and review conflicts | `tests/test_feedback.py` |
| Chat state, hidden score cards, references and immediate feedback submission | `tests/test_ui.py` |
| Ingestion, failure diagnosis and instrumentation | `tests/test_ingestion.py`, `tests/test_rag_failure_modes.py`, `tests/test_telemetry.py` |

Install the Git commit hook once per clone after syncing dependencies:

```bash
uv sync --locked --extra ui --extra evaluation
uv run --locked --no-sync pre-commit install --install-hooks
uv run --locked --no-sync pre-commit run --all-files
```

The hook checks staged files for YAML/TOML validity, merge-conflict markers,
private keys, Ruff lint errors and Python formatting. Ruff uses the version in
`uv.lock` and the exclusions in `pyproject.toml`. Checks report errors without
rewriting files; fix formatting with `uv run --no-sync ruff format app tests`,
then stage the changes again. Keep `uv` on your PATH when committing. Hook setup
requires network access initially; pytest and mypy remain separate checks.
Private-key detection does not detect every API token: keep `.env` untracked.
See the [pre-commit documentation](https://pre-commit.com/).

Controlled tests use the real parser/chains with local stores, fake embeddings
or model responses. The following table focuses on the ingestion teaching cases:

| Test | What it proves |
|---|---|
| `test_register_maps_to_requests` | The register yields 8 requests with correct dates, types and a null product for the operations policy |
| `test_every_document_parses_and_chunks` | All 8 PDFs ingest and every chunk has the nine metadata fields plus `clause`, `parent_id` and `checksum` |
| `test_version_metadata_is_stamped_on_every_chunk` | Product, version, effective date and `supersedes` appear on every chunk |
| `test_tables_are_kept_whole` | The FOIR table is one chunk with all three rows intact, together with the sentence of clause 3.4 that introduces it |
| `test_running_headers_and_footers_are_dropped` | Page headers and footers do not leak into chunk text |
| `test_reingesting_is_idempotent` | Ingesting the same document twice leaves the chunk count unchanged |
| `test_every_registered_clause_is_exactly_one_chunk` | For all 8 documents, the clause chunks match the register's clause list one to one, numbers and headings |
| `test_every_golden_citation_resolves_to_a_chunk` | Every `{doc_id, clause}` cited by the 97 golden Q&As exists as a chunk |
| `test_chunk_carries_identity_header_and_clause_id` | Clause 6.2 has the ID `POL-HL-V3_6.2_c01`, starts with the header line, and holds no other clause |
| `test_oversized_clause_splits_narrative_with_overlap_but_never_the_table` | Above the size limit, narrative splits at sentences with overlap and the table stays whole |
| `test_parent_section_holds_every_clause_of_the_section` | The parent of clause 6.2 contains clauses 6.1 to 6.5 and nothing from section 7 |
| `test_reingesting_replaces_parent_sections` | A second ingest leaves the same parent keys, with no duplicate-key error |
| `test_only_new_or_changed_chunks_are_embedded` | A re-run sends nothing to the embedding model; one stale checksum re-embeds exactly one chunk |
| `test_changing_the_embedding_model_re_embeds_everything` | A different `embedding_model` changes every checksum |
| `test_stale_chunks_are_deleted_and_current_ones_kept` | Cleanup removes only obsolete IDs in the configured corpus and preserves foreign/unscoped records |

Controlled tests do not establish real embedding quality, Atlas ranking or
live ingestion correctness. They use local/mocked stores; selected live API
and browser observations are recorded separately in the validation report.

---

## 9. Design decisions

- **The register drives ingestion, not a folder listing.** `document_register.json` records which documents are authoritative, their effective windows and what supersedes what. Customer uploads in `nexa_synthetic_data/uploads/` are deliberately not indexed as policy.
- **Version metadata on every chunk.** Similarity search cannot tell a current clause from a superseded one; metadata filters can. This is the main finance-specific design point of the deck.
- **Tables are kept whole and stored as HTML.** A fee or threshold table flattened to text loses which value belongs to which band.
- **Deterministic chunk IDs keyed by clause.** `{doc_id}_{clause}_c{nn}` makes writes idempotent, and a new clause does not shift the IDs of the clauses after it.
- **Structure first, size second.** The clause is the unit the policies are written in and the unit the golden Q&A set cites, so it is the chunk. Size rules only apply to a clause over the limit, and never to a table.
- **Identity header in the embedded text.** Every chunk starts with document, version and section, so a piece of a split clause, or a table, is never anonymous.
- **Embed the child, store the parent.** Clauses are embedded for precise matching; whole sections sit in a separate, unembedded collection keyed by `parent_id`.
- **One checksum per chunk, not a timestamp per file.** The hash covers text, metadata and embedding model. A re-saved file with no text change embeds nothing; a model change re-embeds everything.
- **Retired, not left behind.** After each document, chunks whose IDs were not produced are deleted, so a withdrawn clause cannot keep answering.
- **`pdfplumber` for PDFs.** Unstructured's fast mode split table cells into stray headings, and its table-aware mode needs poppler, tesseract and large model downloads. `pdfplumber` is lighter and works on born-digital PDFs. Scanned PDFs would need a different parser.
- **Config in the environment.** Model names and chunk size can change without a redeploy.
- **Manual Atlas index.** The index definition is version-controlled in `indexes.md` rather than created by code.

### Known limitations

- The clause and section patterns fit this corpus ("6. Fees and charges", "6.2 Processing fee. ..."). A document numbered differently would fall into one front-matter chunk per section until the patterns in `chunking.py` are extended.
- With the default `CHUNK_MAX_CHARACTERS`, no clause in the corpus is over the limit, so the size rule is exercised by a test and the notebook, not by a normal run.
- The header line is added on top of the size limit, so a chunk can exceed `CHUNK_MAX_CHARACTERS` by the header length.
- Parent sections are rewritten on every run. They are not embedded, so this costs no model calls.
- The write is not transactional: a crash between the chunk upsert, parent upsert and stale-data cleanup can leave a mixed state until a successful rerun. Embedding failures leave the previous parent evidence intact. Run one ingestion job at a time.
- `/retrieve` falls back to the child passage and logs a warning if a parent is missing or its source scope does not match.
- `section_type` is only `text` or `table`.
- The Atlas index must include institution, product, jurisdiction, confidentiality and both effective-date filter fields. Missing metadata needs re-ingestion; adding a mapping to already stored metadata does not require new embeddings.
- Linked circular lookup depends on consistent stored metadata and `supersedes` links. It follows effective links and checks all indexed parts, but cannot detect a source section that ingestion omitted entirely without an ingestion manifest.
- Citation checks verify exact quote/marker membership, not semantic entailment. One repair draft is allowed; two invalid drafts still abstain. Date-intent classification can return abstention rather than the desired clarification.
- Reviewer access uses a shared secret. There are no individual user accounts or tenant roles, and multi-institution write isolation is not established.
- Feedback retry protection needs stable submitter/submission IDs. Datadog feedback has no durable delivery queue; SDK `queued` is not proof of cloud receipt.
- Reloading a previously open sidebar can still produce CLS 0.24. Automated accessibility snapshots passed, but framework label/ID issues and full accessibility testing remain open.
- The installed ddtrace integration can log a non-LLM prompt-template warning. Complete cloud prompt capture and sustained load were not verified in the latest run.
- The saved live checks cover selected synthetic cases, not every golden case or a cloud deployment. Recruited-user usability, disaster recovery and production capacity remain unverified.
- Scaling beyond a loop (queue, worker pool, sharded index) is covered as design in Session 2, not as code.

---

## 10. Roadmap

```mermaid
flowchart LR
    S1["S1: Architecture +<br/>ingestion"]:::done --> S2["S2: Clause-aware chunking,<br/>parent docs, checksums"]:::done
    S2 --> S3["S3: Embeddings and<br/>vector index"]:::done
    S3 --> S4["S4: Retrieval API<br/>and scoped amendments"]:::done
    S4 --> S5["S5: Failure diagnosis;<br/>alerting still open"]:::partial
    S5 --> W2["Week 2: answers, UI,<br/>evaluation and feedback built"]:::partial
    W2 --> P2["Phases 2 to 4: agents,<br/>MCP, production"]:::planned

    classDef done fill:#d1fae5,stroke:#047857,color:#064e3b
    classDef partial fill:#fef3c7,stroke:#b45309,color:#78350f
    classDef planned fill:#f3f4f6,stroke:#6b7280,color:#374151
```

**Legend:** 🟩 implemented with selected live evidence · 🟨 implemented with remaining quality/operational checks · ⬜ planned

| Session | Adds |
|---|---|
| 2 (done) | Clause-aware chunking with a `clause` field, parent documents, checksum-based incremental re-ingest |
| 3 (implemented; selected live checks) | Python 3.14 environment, shared embeddings and a queryable Atlas index including institution filtering |
| 4 (implemented; selected live checks) | Scoped `/retrieve`, parent validation, complete linked circulars, citations and timings |
| 5 (implemented; operations work remains) | Failure diagnosis, health, counters and latency percentiles; alert thresholds and statistical drift monitoring remain open |
| Week 2 (implemented; quality work remains) | Cited answers, Streamlit, live/batch RAGAS and judge workflows, feedback and reviewer-token access. Full-dataset quality, hybrid/reranker comparisons and the instructor walkthrough remain open. |

Phases 2 to 4 (agents, MCP, production) wrap this service without rewriting it, so the ingestion contract and the chunk record shape should stay stable.

---

## Streamlit and endpoint coverage

For the owner/reviewer handoff, approval errors, and prerequisites for every
customer and request type, see the [Service requests operator guide](C4-Documentation/SERVICE_REQUESTS_GUIDE.md).

For the **Service requests** workspace, all four services must be up and running
together. Use `make run` from the repository root and keep its terminal open;
see [Run the services with Make](#run-the-services-with-make) for prerequisites.
Policy chat uses the RAG API; service requests also
need the Agent API and banking API.

| Service | Port | Check or open |
|---|---|---|
| RAG FastAPI | 8000 | [Health](http://127.0.0.1:8000/health) |
| Banking mock API | 8080 | [API docs](http://127.0.0.1:8080/docs) |
| Agent FastAPI | 8001 | [Health](http://127.0.0.1:8001/health) |
| Streamlit UI | 8501 | [NexaAssist](http://127.0.0.1:8501) |

Complete [Getting started](#6-getting-started) first, including Atlas setup and
policy ingestion. In `.env`, configure `OPENAI_API_KEY`, MongoDB settings, a
nonempty `BANKING_API_TOKEN`, and distinct named owner/reviewer credentials in
`AGENT_PRINCIPALS` with the required customer access. The legacy
`AGENT_VALIDATION_TOKEN` cannot own or approve durable runs. See the
[workflow credential example](app/agents/README.md#integrated-flow--27-october-2026).

The local service URLs are:

```dotenv
API_BASE_URL=http://127.0.0.1:8000
RAG_BASE_URL=http://127.0.0.1:8000/
AGENT_API_BASE_URL=http://127.0.0.1:8001
BANKING_BASE_URL=http://127.0.0.1:8080/
```

Install dependencies once:

```bash
make setup
```

The Make targets load `.env` before `ddtrace-run` and set a separate Datadog
service name per process. Start all four together:

```bash
make run
```

Alternatively, start each service in its own terminal:

```bash
# Terminal 1: RAG FastAPI
make run-rag

# Terminal 2: synthetic banking API (run from the repository root)
make run-bank

# Terminal 3: Agent FastAPI
make run-agent

# Terminal 4: Streamlit
make run-ui
```

Open Streamlit, select **Service requests**, and enter the configured owner
credential to prepare a proposal. A different authorized reviewer must load the
saved run and approve its exact proposal before submission.

Use **Customer ID `NXC-0010`** for the demo and include it in both the owner's
and reviewer's `AGENT_PRINCIPALS[].customer_ids`. For an `INTEREST_CERTIFICATE`
request, use its loan account **`NXL-HL-0010`**. A customer outside the configured
credential's scope is rejected with HTTP 403, even when it exists in the banking
mock.

If **Prepare for approval** fails, check that the banking API on port 8080 is
still running as well as both FastAPI services. The `/health` endpoints report
liveness only; they do not verify MongoDB, model or banking connectivity. For an
uncertain bank submission, load and retry the same saved run rather than starting
a new request. An "address already in use" startup error means the selected port
is occupied; check the existing service before starting another instance.

To find question traces, open [Datadog Trace Explorer](https://app.datadoghq.com/apm/traces?query=service%3Anexaassist-api%20env%3Adev),
select **Live Search** / **Past 15 minutes**, and send a new question. Start with
`service:nexaassist-api env:dev`; then select `fastapi.request` and resource
`POST /answer/evaluated` with quality scoring enabled, `POST /answer` with it disabled,
or `POST /retrieve` in Evidence mode. A service page filtered to
`pymongo.cmd` / client spans shows database calls rather than the API request entry.
Clear those filters and refresh any saved absolute time window before diagnosing
missing traces. See [DATADOG_README.md](DATADOG_README.md) for Agent diagnostics.

The UI uses `API_BASE_URL` (default `http://127.0.0.1:8000`) and `UI_TIMEZONE`
(default `Asia/Kolkata`). Streamlit 1.66+ loads the app's dark theme from
`app/.streamlit/config.toml`. The chat interface includes a bottom message composer,
suggested policy questions, a **New chat** action, and recent chats in the sidebar.
Chats are stored only in the current Streamlit session, without database persistence.
Each question is sent independently to the existing API; previous messages are displayed
for reference and are not sent as model context. Include relevant details in each question.

Use **Policy scope** in the sidebar to set the policy date, an optional product ID,
the source limit, and **Answer** or **Evidence** mode. Each message retains its submitted
scope even if the sidebar changes. Failed requests offer **Try again** with that original scope.
Source clauses and per-answer feedback appear below each response. Linked
amendments can add evidence beyond the selected similarity-source limit.
Select **Review feedback** in the sidebar and enter the reviewer token before
loading or reviewing saved feedback. Set `FEEDBACK_REVIEWER_TOKEN` in `.env` to
a random secret of at least 32 characters, then restart FastAPI:

```bash
uv run python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Keep that generated value in `.env`, outside version control. Review routes
return 503 when the token is not configured, or 401 for missing/incorrect
credentials. API reviewers send `Authorization: Bearer <reviewer token>`.
This is a shared reviewer credential for the local training app; it does not
provide individual user accounts or roles. Use TLS and proper identity/role
management before opening this application to shared remote access.
With Datadog LLM Observability enabled, **Send feedback** also queues a `thumbs`
rating and optional correction on the matching API answer span. An anonymous
browser-session UUID identifies the submitter. MongoDB saving remains available
if Datadog fails or an older answer has no span reference; see the
[Datadog feedback guide](DATADOG_README.md#end-user-feedback-from-streamlit).
The UI sends a stable `submission_id` for each answer and retains the anonymous
`submitter_id` within its browser session. Retrying an identical
submission returns its saved record without another Datadog submission; reusing
the same IDs with changed contents returns 409. Direct API clients must retain
**both** UUIDs on retries; omitting either creates a fresh identity for that
request. Datadog delivery remains best effort rather than a durable
delivery queue.

**Answer quality → Evaluate with RAGAS** is enabled for new questions by default.
RAGAS metrics appear in **Datadog only**; Streamlit shows the answer, citations
and a **View evaluation in Datadog** link. Add an expected answer in **Reference answer for the next question**
to measure all four metrics. Exact golden-case matches can supply that reference
automatically; missing references or scorer failures remain explicit in the API
result and are not exported as numeric scores. The reference applies to one
question and is then cleared. Scoring uses
additional model calls and reuses the generated answer's exact retrieved evidence.
Successful scores are also queued against the `policy_answer_live_evaluation`
workflow in Datadog; use **View evaluation in Datadog** below the answer.
See the [live RAGAS guide](DATADOG_README.md#ragas-scores-in-datadog-for-streamlit-questions).

The UI covers six of nine application operations:

| Method and endpoint | Streamlit control |
| --- | --- |
| `POST /answer/evaluated` | Answer mode with Evaluate with RAGAS enabled |
| `POST /answer` | Answer mode with Evaluate with RAGAS disabled |
| `POST /retrieve` | Policy chat → Evidence → send a message |
| `POST /feedback` | Send feedback below an answer |
| `GET /feedback` | Review feedback → reviewer token → Load feedback for review |
| `POST /feedback/{feedback_id}/review` | Reviewer token, reviewer name, decision and note → Save review |
| `GET /health` | No existing UI control; tested directly |
| `GET /metrics` | No existing UI control; tested directly |
| `GET /metrics/latency` | No existing UI control; tested directly |

Liveness does not verify MongoDB or model readiness; counters and latency samples
belong to one API process. The three operational endpoints have no controls in
the UI.

`tests/test_ui.py` connects Streamlit AppTest to the real ASGI routes, discovers
the application endpoints and reports the three operations not reached by UI actions. It
uses controlled retrieval/answer responses and an in-memory feedback store;
these tests do not measure Atlas ranking or real model quality.

The UI tests also verify chat switching, retained history, policy filters, suggestions,
request retries, and feedback isolation between answers. Feedback inputs no longer carry
over from one answer to the next.

```bash
RAGAS_DO_NOT_TRACK=true uv run --extra ui --extra evaluation pytest tests/
```

## Local Datadog tracing

See [Datadog for NexaAssist and LLM applications](DATADOG_README.md) for local
macOS and cloud setup, API/UI launch commands, a traced RAG example, dashboards,
troubleshooting and the distinction between telemetry and AI evaluations. The
guide documents the existing Agent-based setup on `datadoghq.com`, including
configuration loading before `ddtrace-run` and the current PyMongo compatibility
constraint.

## Further reading

- [PROMPT_HISTORY_AND_GUIDE.md](PROMPT_HISTORY_AND_GUIDE.md): an empty-folder starting guide, your prompts in corrected English, improved prompts, issue resolutions and reusable templates
- [DEVELOPMENT_TOOLING_GUIDE.md](DEVELOPMENT_TOOLING_GUIDE.md): development skills, Git and assistant hooks, MCP status, and usage examples
- [CLAUDE.md](CLAUDE.md): coding rules and layer boundaries
- [nexa_synthetic_data/README.md](nexa_synthetic_data/README.md): the corpus, golden Q&A, customer data and mock API
- [app/database/indexes.md](app/database/indexes.md): the Atlas index definition

## Phase 2: Orchestrator and HTTP Retriever — 23 October 2026

This extension implements the Orchestrator/Retriever stage from syllabus pages
4–5. Earlier dated Phase 1 status and diagrams above remain historical snapshots.
One **Agent Service** hosts the graph roles; Validator and Action execution are
later stages. The Phase 1 application and its routes are unchanged, including
`POST /retrieve`, `/answer` and `/answer/evaluated` (the existing evaluated path).

```mermaid
flowchart LR
    S[START] --> O[Orchestrator: structured intent]
    O -->|policy question| R[Retriever: HTTP POST /retrieve]
    R --> A[Existing grounded answer chain]
    A --> E[END]
    O -->|missing or conflicting information| C[Clarification]
    O -->|forbidden| F[Stop]
    O -->|customer context| U[Tools unavailable in this stage]
    O -->|supported action| V[Validator and Action unavailable]
    C --> E
    F --> E
    U --> E
    V --> E
```

The classifier and answer generator both use `get_chat_model(settings)` and the
existing `CHAT_MODEL`. No routing model, policy fee, rate or eligibility rule is
introduced. Routing scope is sourced from `POL-OPS-001` clauses 2, 4–6, the product
catalog's names/IDs, and the supplied mock banking OpenAPI operations. The graph
never calls those customer/action APIs in this stage.

For this routing-only example, start the two FastAPI services from the repository
root in separate terminals. For the complete service-request UI, follow the
[four-service startup instructions](#streamlit-and-endpoint-coverage), including
the banking API and Streamlit:

```bash
uv sync --locked --all-extras
# Terminal 1: existing Phase 1 RAG
uv run --no-sync --env-file .env ddtrace-run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# Terminal 2: one Agent Service
uv run --no-sync --env-file .env ddtrace-run python -m uvicorn app.agent_main:app --host 127.0.0.1 --port 8001
```

`RAG_BASE_URL` defaults to `http://127.0.0.1:8000/` and must identify the Phase 1
service, not the Agent Service. An optional path prefix is preserved. HTTP calls
have a 60-second timeout; a graph run has a 180-second deadline and an eight-step
recursion bound. Configure these timeouts in `.env.example`'s agent settings.
Model timeout/retry behavior stays in the existing chat-model factory. No banking
write retries or action execution are added.

Compare the two consumers of the same retrieval endpoint:

```bash
curl -sS http://127.0.0.1:8000/retrieve \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is the maximum loan amount for a NexaHome Loan?","as_of":"2026-10-01","product_id":"NEXA-HL"}'
curl -sS http://127.0.0.1:8001/agent \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is the maximum loan amount for a NexaHome Loan?","as_of":"2026-10-01","product_id":"NEXA-HL"}'
```

`POST /agent` accepts `query`, optional `as_of`, `product_id`, `customer_id`,
`action`, `jurisdiction`, and `top_k` (1–20; default 5). Missing scope is accepted
at this boundary so the graph can return clarification. Malformed dates, customer
IDs, action names and unknown fields return HTTP 422. Explicit, unambiguous scope
in the query can be extracted; conflicts with supplied fields require clarification.
A sanction date is not silently substituted for the policy lookup date.

The response has `intent`, `status`, `message`, `missing_fields`, and `answer`.
`answer` contains the existing complete `AnswerResponse` for the policy route,
including verified quotes, matched sources and timings. The HTTP retriever returns
results **and complete amendments** to the existing answer chain; it retrieves once
and replays that evidence, retaining budgeting, abstention and one citation-repair
attempt. Upstream HTTP/model/schema failures return a generic HTTP 503.

| Route | Response behavior |
| --- | --- |
| `policy_question` | `answered`, `needs_clarification` or `insufficient_evidence` from the existing answer chain |
| `missing_information` | `needs_clarification` with fixed, uncited text and missing/conflicting field labels |
| `forbidden` | `forbidden`; stops without retrieval, generation or action execution |
| `customer_context` | Missing customer identity clarifies; otherwise `unavailable` until customer tools are integrated |
| `supported_action` | Missing customer/action/details clarifies; otherwise `unavailable` until Validator/Action integration |

All these domain outcomes use HTTP 200. Requests are independent: resubmit the
original query and clarified fields together. There is no checkpoint, implicit
conversation memory, customer access, banking write, approval gate or claim of
completed Phase 2 exit requirements. Classification uses an LLM; the offline
route tests do not prove semantic classification accuracy or injection resistance.

`GET /health` is liveness only. `GET /metrics` exposes process-local agent request
and completed-run counters; completed includes clarification, refusal and unavailable
outcomes. It does not report Phase 1 retrieval counters or in-flight failures.
The shared startup initializes logging/Datadog; orchestration has a Datadog workflow
span and versioned prompt annotation. No raw customer data is loaded by this stage.

```bash
RAGAS_DO_NOT_TRACK=true DD_TRACE_ENABLED=false DD_LLMOBS_ENABLED=false \
  uv run --locked --no-sync pytest tests/ -q
uv run --locked --no-sync ruff check app tests
uv run --locked --no-sync ruff format --check app tests
uv run --locked --no-sync mypy app
```

`tests/test_agent.py` covers all graph routes, required/conflicting scope, ordered
execution, the actual Phase 1 route through HTTPX's ASGI transport, amendment/citation
preservation, failed quote validation, downstream errors, deadlines and API separation.
Ten routing golden cases are in `evaluation/datasets/agent.jsonl`; they include
source/contract notes and expected outcomes, not measured quality scores.

Official references verified for this implementation:
[LangGraph structured routing](https://docs.langchain.com/oss/python/langgraph/workflows-agents#routing),
[Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api),
[ChatOpenAI structured output](https://docs.langchain.com/oss/python/integrations/chat/openai#structured-output),
and [HTTPX async clients](https://www.python-httpx.org/async/).

The requested history analysis is saved in [codebase-recon-report.md](codebase-recon-report.md).

Verification for this extension: **159 application tests passed**, including 34
new agent cases; Ruff checks, formatting (85 files), mypy (64 source files),
`uv lock --check --offline` and `git diff --check` passed. A separate Agent uvicorn
process and the already-running Phase 1 service passed six synthetic cases over
real loopback TCP using the configured `gpt-6-luna`: cited policy answer, missing
date, forbidden credit decision, unavailable customer context, unavailable callback
action, and missing service-request details. The temporary Agent process was stopped.
See [the live smoke report](app/evaluation/results/agent_stage_20261023.json).
Datadog export was disabled for this smoke run; these six cases are not a
full-dataset quality or production-load claim.

A before/after preservation audit found 425 of 430 existing modified/untracked
files byte-identical. The other five were intentional integration edits: README,
component guide and `.env.example` retained their prior content verbatim with
appended sections; config gained AgentSettings and an import; compatibility
requirements gained LangGraph. Historical prompts and synthetic source data
were untouched. No commit, stash, reset or existing-service restart was performed.

## Phase 2: service-request Validator

Explicit submission is available through `POST /agent/submit`, using the same
body as `/agent` with complete customer/date/action details and an
`Idempotency-Key` header (16–64 letters, digits, underscores or hyphens). The
existing validator bearer token and customer allowlist apply. Successful validation
posts the exact proposal to the banking API and returns `status: "submitted"`,
the bank's receipt and policy evidence. `/agent` remains validation-only.
See [submission behavior and retry limits](app/agents/README.md#explicit-service-request-submission).
On an unconfirmed submission, retry with the same key and unchanged body; a timeout
can follow a committed write. A received service request does not establish KYC
completion, certificate issuance or an effective prepayment opt-in.

The selected Phase 2 Sessions 1–3 decks now carry forward into the Validator
lesson (syllabus Session 6). See the [session walkthrough and code map](app/agents/README.md)
for the source-backed eligibility rule, setup, request example, teaching sequence,
deck corrections and deferred Action/approval stages.

`POST /agent` additionally accepts typed `action_details` for all five service
request types in the supplied OpenAPI contract. With configured caller authorization and a
server-controlled customer allowlist, this route retrieves policy evidence once,
reads the customer's loan through the banking mock API, and returns a deterministic
validation decision plus an eligible service-request proposal. Successful proposals
use outer status `validated`; `validation.requires_approval` remains true.
No banking write is executed and no opt-in is claimed to have occurred.

| Request type | Validation performed before proposing |
| --- | --- |
| `OPT_IN_CIR_2026_07_PART_PREPAY` | Existing circular/version/sanction/opt-in checks; requires `NEXA-HL` and selected loan |
| `INTEREST_CERTIFICATE` | Operations-policy permission and ownership of a required selected loan; supplied product must match |
| `STATEMENT_COPY` | Operations-policy permission and authorized customer; any supplied loan must belong to that customer |
| `CALLBACK` | Operations-policy permission and authorized customer; any supplied loan must belong to that customer |
| `RE_KYC_REMINDER` | Operations-policy permission and authorized customer; this does not complete KYC or assert that re-KYC is due |

All proposals require `as_of`, customer, action and request type. The Orchestrator
clarifies missing details or a required loan before retrieval. General requests
use one fixed bank-wide `POL-OPS-001` query with no product filter; an explicitly
selected product stays in `AgentRequest` and is checked against the selected loan.
The prepayment path keeps its existing product-scoped circular query. Both paths
retain full source quotes/citations and read customer records only through HTTP.
`createCase`, `addCaseNote` and `escalateCase` remain unavailable integrations.

Configuration is disabled by default. The new variables are
`AGENT_VALIDATION_TOKEN`, `AGENT_ALLOWED_CUSTOMER_IDS`, `BANKING_BASE_URL` and
`BANKING_API_TOKEN`; `.env.example` contains placeholders only. The scoped token
is a training authentication mechanism, not production identity management.
Existing RAG endpoints and policy-only answers retain their behavior.

Run `uv run --locked --no-sync pytest tests/test_validation.py tests/test_agent.py -q`
for the original ten prepayment golden cases, ten additional service-request
goldens, authorization and failure tests, and
existing orchestrator/retriever regression coverage. The session guide distinguishes
these mocked tests from live model classification and actual provider connectivity.

The blocking quality contract is [CONSTRAINTS.md](CONSTRAINTS.md). Run
`make check-fast`, `make check-task`, or `make check-full`; full security checks
use external Semgrep source/secret rules and a fingerprinted pre-existing baseline.
