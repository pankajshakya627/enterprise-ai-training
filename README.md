# NexaAssist

A policy, product and lending advisor for the staff of NexaBank (a fictional bank), built as the hands-on codebase for the **Enterprise Agentic RAG** training program. Everything is synthetic.

This repository currently contains **Phase 1, Session 1**: the architecture and the **ingestion (write) path**. It turns the bank's policy PDFs into versioned, embedded chunks in MongoDB Atlas. The retrieval (read) path arrives in later sessions.

| | |
|---|---|
| Language | Python 3.11 or 3.12 |
| Orchestration | LangChain (LCEL runnables) |
| Vector store | MongoDB Atlas Vector Search via `langchain-mongodb` |
| Embeddings | OpenAI `text-embedding-3-small`, 1536 dimensions |
| PDF parsing | `pdfplumber` (tables and headings), `unstructured` (chunking) |
| Config and logging | `pydantic-settings`, `structlog` (JSON), optional `ddtrace` |
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

---

## 1. Architecture

The system has two paths that share a store, not a service. Ingestion is bursty and throughput-bound; retrieval is latency-bound. Keeping them separate means a slow re-index cannot slow a loan officer.

```mermaid
flowchart LR
    subgraph WRITE["Write path: built in Session 1"]
        direction LR
        SRC["Policy PDFs and circulars"] --> LOAD["Load via document register"]
        LOAD --> PARSE["Parse: tables, headings"]
        PARSE --> CHUNK["Chunk by section"]
        CHUNK --> ENRICH["Enrich: 9 metadata fields"]
        ENRICH --> EMBED["Embed with OpenAI"]
    end

    EMBED --> ATLAS[("MongoDB Atlas<br/>chunks + vector index")]

    subgraph READ["Read path: Sessions 4 to 7"]
        direction LR
        UI["Staff UI"] --> RET["Retrieval Service"]
        RET --> QEMB["Embed query<br/>same model"]
        QEMB --> SEARCH["Vector search + filters"]
        SEARCH --> CITED["Cited chunks"]
    end

    ATLAS -.-> SEARCH
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
    class L1 built
    class L2,L3,L4,L5,L6 todo
```

Green is implemented. Everything else is future work.

---

## 2. Project layout

```text
.
├── README.md
├── CLAUDE.md                       Rules for how the code is written
├── pyproject.toml                  Dependencies and tool config
├── .env.example                    Template for required environment variables
├── phase1_session1_ingestion.ipynb Standalone teaching notebook (does not import app/)
│
├── app/
│   ├── core/                       Cross-cutting infrastructure
│   │   ├── config.py               Settings: every env var, typed
│   │   ├── logging.py              JSON logging setup
│   │   └── telemetry.py            Datadog APM switch
│   ├── models/
│   │   └── ingestion.py            IngestRequest, IngestResponse (the service contract)
│   ├── chains/                     Pure LangChain: no web or UI imports
│   │   ├── parsing.py              PDF to structured elements
│   │   ├── embeddings.py           OpenAI embeddings client
│   │   └── ingestion.py            The write-path chain
│   ├── database/
│   │   ├── mongo.py                MongoDB Atlas vector store
│   │   └── indexes.md              The Atlas Vector Search index JSON (created by hand)
│   └── services/
│       └── ingestion.py            Entry point: register to requests, run the chain
│
├── tests/
│   └── test_ingestion.py           6 tests, no network needed
│
└── nexa_synthetic_data/            The corpus (see its own README.md)
    ├── document_register.json      Controlled library: versions, windows, supersession
    ├── policies/                   8 PDFs
    ├── golden_qa/                  97 Q&As for evaluation (used from Week 2)
    ├── catalog/  customers/  uploads/  api/  generator/
    └── README.md
```

### Layer rules

- `chains/` has no FastAPI or Streamlit imports.
- `database/` is the only place that creates a MongoDB client.
- `services/` bridges chains to whatever calls them (today, the command line; later, FastAPI).
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
    participant CLI as services.ingestion.main
    participant REG as document_register.json
    participant CH as Ingestion chain
    participant PDF as parse_document
    participant UNS as chunk_by_title
    participant OAI as OpenAI Embeddings
    participant DB as MongoDB Atlas

    CLI->>REG: read entries
    REG-->>CLI: 8 authoritative documents
    CLI->>CLI: requests_from_register builds IngestRequest per document
    loop for each IngestRequest
        CLI->>CH: ainvoke(request)
        CH->>PDF: parse
        PDF-->>CH: Title, NarrativeText, Table elements
        CH->>UNS: chunk
        UNS-->>CH: chunks (tables kept whole)
        CH->>CH: enrich: attach metadata and chunk_id
        CH->>OAI: embed in batches of 100
        OAI-->>CH: vectors
        CH->>DB: bulk upsert by chunk_id
        CH-->>CLI: IngestResponse
        CLI->>CLI: log document_ingested
    end
```

### What each stage does

| # | Stage | Function | Result |
|---|---|---|---|
| 1 | Load | `requests_from_register` | One `IngestRequest` per authoritative document in the register. Files not in the register are never indexed. |
| 2 | Parse | `parse_document` | Ordered `Title`, `NarrativeText` and `Table` elements. Tables are extracted whole as HTML; page headers and footers are dropped. |
| 3 | Chunk | `chunk_by_title` | A new chunk at every heading. A table is never merged with neighbouring text. Size limit from `CHUNK_MAX_CHARACTERS`. |
| 4 | Enrich | `enrich` (inside the chain) | A LangChain `Document` per chunk with a deterministic `chunk_id` and the metadata fields. |
| 5 | Embed + upsert | `embed_and_upsert` (inside the chain) | Vectors from OpenAI, written to Atlas by `chunk_id`, so re-running replaces instead of duplicating. |

---

## 4. Classes and functions

### Class diagram

```mermaid
classDiagram
    class Settings {
        +SecretStr mongodb_uri
        +str mongodb_db
        +str mongodb_collection
        +str vector_index_name
        +SecretStr openai_api_key
        +str embedding_model
        +int embedding_dimensions
        +int embedding_batch_size
        +int chunk_max_characters
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
        +str embedding_model
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
| `Settings` | class, extends `BaseSettings` | Typed view of every environment variable. Reads `.env`. Secrets are `SecretStr`, so they never print in logs. |
| `get_settings` | `() -> Settings` | Cached accessor; the app builds `Settings` once. |
| `configure_logging` | `(level: str) -> None` | Configures `structlog` to write JSON lines to stdout, ready for Datadog. |
| `init_telemetry` | `(enabled: bool) -> None` | Calls `ddtrace.patch_all()` when enabled so PyMongo is traced. Does nothing when disabled. |

### `app/models/ingestion.py`

| Item | Purpose |
|---|---|
| `IngestRequest` | The Ingestion Service contract: one source document plus its document-level metadata. |
| `IngestResponse` | What an ingest returns: the document, how many chunks, their IDs, and the embedding model used. |

### `app/chains/`

| Item | Signature | Purpose |
|---|---|---|
| `parse_document` | `(path: str) -> list[Element]` | Entry point for parsing. PDFs go to `_parse_pdf`; other formats go to `unstructured.partition`. |
| `_parse_pdf` | `(path: str) -> list[Element]` | Uses `pdfplumber`. Tables come from ruling lines, headings from font size larger than the body text, and header and footer lines are cropped by page margin. Output is in reading order. |
| `_table_element` | `(rows) -> Table` | Builds a `Table` element with an HTML rendering, so each value stays attached to its column. |
| `get_embeddings` | `(settings) -> OpenAIEmbeddings` | Pins model, dimension and batch size from settings. The read path must use the same values. |
| `build_ingestion_chain` | `(vector_store, settings) -> Runnable` | Composes the write path as one LCEL chain: `parse`, `chunk`, `enrich`, `embed_and_upsert`. Input is `{"request": IngestRequest}`; run it with `ainvoke`. Each stage has a `run_name`, so a LangSmith trace shows per-stage timing. |

### `app/database/mongo.py`

| Item | Signature | Purpose |
|---|---|---|
| `get_vector_store` | `(settings, embedding) -> MongoDBAtlasVectorSearch` | Creates the client and returns the store with collection, embedding and index name passed explicitly. It never creates the index; that is manual (see [Section 6](#6-getting-started)). |

### `app/services/ingestion.py`

| Item | Signature | Purpose |
|---|---|---|
| `DOC_TYPES` | `dict[str, str]` | Maps a `doc_id` prefix to a document type: `POL` is `policy`, `CIR` is `circular`. |
| `requests_from_register` | `(register_path, settings) -> list[IngestRequest]` | Reads `document_register.json`, keeps authoritative entries, and fills `institution`, `jurisdiction` and `confidentiality_level` from settings. |
| `ingest` | `async (requests) -> list[IngestResponse]` | Builds the store and chain once, then ingests each request in order, logging one `document_ingested` event per document. |
| `main` | `() -> None` | Command-line entry point. Takes the register path as its only argument. |

---

## 5. Data model: the chunk record

Each chunk is stored in MongoDB as one document. Metadata sits at the top level beside the text and vector.

```json
{
  "_id": "POL-HL-V3_c007",
  "text": "<table><tr><td>Net monthly income</td><td>Maximum FOIR</td></tr><tr><td>Below ₹1,00,000</td><td>50%</td></tr> ...</table>",
  "embedding": [0.012, -0.044, "... 1536 floats"],

  "doc_id": "POL-HL-V3",
  "title": "NexaHome Loan Policy",
  "chunk_id": "POL-HL-V3_c007",
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
  "embedding_model": "text-embedding-3-small"
}
```

The nine metadata fields from the training deck are `institution`, `product_id`, `policy_version`, `effective_date`, `supersedes`, `jurisdiction`, `doc_type`, `section_type` and `confidentiality_level`. Eight come from the request; `section_type` (`text` or `table`) is set per chunk by the pipeline. `doc_id`, `title`, `effective_to`, `chunk_id` and `embedding_model` are extra fields this build adds.

### Where the metadata comes from

```mermaid
flowchart LR
    REG["document_register.json"] -->|"doc_id, title, product_id,<br/>version, effective window,<br/>supersedes"| REQ["IngestRequest"]
    ENV[".env"] -->|"institution, jurisdiction,<br/>confidentiality_level"| REQ
    PFX["doc_id prefix"] -->|"doc_type"| REQ
    REQ -->|"model_dump"| META["chunk metadata"]
    PIPE["pipeline"] -->|"chunk_id, section_type,<br/>embedding_model"| META
```

---

## 6. Getting started

### Prerequisites

- [`uv`](https://docs.astral.sh/uv/) installed. It will fetch a compatible Python (3.11 or 3.12) if you do not have one.
- An **OpenAI API key**.
- A **MongoDB Atlas** cluster on a tier that supports Vector Search.

### Step 1: install

```bash
uv sync
```

> **Heads-up:** `uv sync` creates `.venv` inside the project folder. If the folder is synced by Google Drive, point it elsewhere first:
> `export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/nexaassist"`

### Step 2: configure

```bash
cp .env.example .env
```

Edit `.env`. These are required:

| Variable | Example | Meaning |
|---|---|---|
| `MONGODB_URI` | `mongodb+srv://user:pass@cluster.mongodb.net` | Atlas connection string |
| `MONGODB_DB` | `nexaassist` | Database name |
| `MONGODB_COLLECTION` | `policy_chunks` | Collection that holds chunks |
| `OPENAI_API_KEY` | `sk-...` | Used for embeddings only |
| `INSTITUTION` | `NexaBank` | Stamped on every chunk |
| `JURISDICTION` | `IN` | Stamped on every chunk |
| `CONFIDENTIALITY_LEVEL` | `internal` | Stamped on every chunk |

These have defaults and are optional:

| Variable | Default | Meaning |
|---|---|---|
| `VECTOR_INDEX_NAME` | `policy_chunks_v1` | Must match the index you create in Atlas |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Pinned; never mix models in one index |
| `EMBEDDING_DIMENSIONS` | `1536` | Must match the Atlas index |
| `EMBEDDING_BATCH_SIZE` | `100` | Chunks per embedding request |
| `CHUNK_MAX_CHARACTERS` | `1200` | Upper bound for one chunk |
| `LOG_LEVEL` | `INFO` | `structlog` level |
| `DD_TRACE_ENABLED` | `false` | Turn on only with a Datadog Agent running |

`.env.example` also lists `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY` and `LANGCHAIN_PROJECT`. LangChain reads these itself; set `LANGCHAIN_TRACING_V2=true` to see per-stage traces in LangSmith.

### Step 3: create the Atlas Vector Search index (once, by hand)

In the Atlas UI go to **Atlas Search, Create Search Index, Atlas Vector Search, JSON editor**. Choose your database and collection, name the index `policy_chunks_v1`, and paste:

```json
{
  "fields": [
    { "type": "vector", "path": "embedding", "numDimensions": 1536, "similarity": "cosine" },
    { "type": "filter", "path": "product_id" },
    { "type": "filter", "path": "jurisdiction" },
    { "type": "filter", "path": "effective_date" },
    { "type": "filter", "path": "confidentiality_level" }
  ]
}
```

The same definition lives in [app/database/indexes.md](app/database/indexes.md). If you change the model or dimension, create a new index version (`policy_chunks_v2`) and re-embed everything.

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
uv sync                                    # once
uv run --env-file .env python -m app.services.ingestion \
    nexa_synthetic_data/document_register.json
```

What happens, in order: settings load from `.env`, logging is configured, the register is read, and each of the eight documents is parsed, chunked, embedded and upserted. It finishes after eight `document_ingested` log lines (see below). To ingest a different register or a copy of it, pass its path as the argument.

#### Option B: run the tests (no credentials)

```bash
uv run pytest -q
```

Expect `6 passed`.

#### Option C: offline dry run (no credentials)

Save this as `dry_run.py` in the project root and run `uv run python dry_run.py`. It uses the real parser and chain, but an in-memory store and fake embeddings, so nothing leaves your machine.

```python
import asyncio
from pathlib import Path

from langchain_core.embeddings import DeterministicFakeEmbedding
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
chain = build_ingestion_chain(store, settings)


async def main() -> None:
    register = Path("nexa_synthetic_data/document_register.json")
    for request in requests_from_register(register, settings):
        response = await chain.ainvoke({"request": request})
        print(f"{response.doc_id:<13}{response.chunk_count:>4} chunks")
    print("total:", len(store.store))


asyncio.run(main())
```

Expected output:

```text
POL-HL-V1      21 chunks
POL-HL-V2      21 chunks
POL-HL-V3      21 chunks
CIR-2026-07    10 chunks
POL-PL-V1      13 chunks
POL-LAP-V1     14 chunks
POL-AL-V1      13 chunks
POL-OPS-001    15 chunks
total: 128
```

The notebook [phase1_session1_ingestion.ipynb](phase1_session1_ingestion.ipynb) does the same thing step by step and falls back to this offline mode automatically.

#### What a successful full run looks like

One JSON log line per document, eight in total:

```json
{"doc_id": "POL-HL-V3", "chunk_count": 21, "embedding_model": "text-embedding-3-small", "policy_version": "3.0", "effective_date": "2025-10-01", "event": "document_ingested", "level": "info", "timestamp": "..."}
```

Expected chunk counts for the synthetic corpus:

| Document | Chunks |
|---|---|
| POL-HL-V1, POL-HL-V2, POL-HL-V3 | 21 each |
| CIR-2026-07 | 10 |
| POL-PL-V1 | 13 |
| POL-LAP-V1 | 14 |
| POL-AL-V1 | 13 |
| POL-OPS-001 | 15 |
| **Total** | **128** |

Running it again is safe. Chunk IDs are deterministic, so existing chunks are replaced rather than duplicated.

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ValidationError ... Field required` naming `mongodb_db`, `openai_api_key`, `institution`, etc. | A required variable is missing from `.env`, or `.env` was not found | Run from the project root and compare `.env` with `.env.example` |
| `command not found: uv` | `uv` is not installed | Install it from https://docs.astral.sh/uv/ |
| `AuthenticationError` from OpenAI | Wrong or placeholder `OPENAI_API_KEY` | Set a real key (`sk-...`) |
| `ServerSelectionTimeoutError` from PyMongo | Bad `MONGODB_URI`, or your IP is not on the Atlas network access list | Check the URI and add your IP in Atlas |
| Ingestion succeeds but the collection holds fewer or more documents than 128 | A different `MONGODB_COLLECTION` than you are inspecting, or older data in it | Check the collection name; clear old documents if you changed the chunking |
| Vector search later returns nothing | The Atlas index does not exist, is still building, or has a different name or dimension | Check index status; `VECTOR_INDEX_NAME` and `EMBEDDING_DIMENSIONS` must match |
| `libmagic is unavailable` warning | Optional file-type helper not installed | Safe to ignore |
| Tests or commands cannot find `app` | Not run from the project root | `cd` to the folder containing `pyproject.toml` |

### Check the result in Atlas

In the Atlas data explorer, the collection should hold 128 documents, each with an `embedding` array of 1536 numbers and the metadata fields from [Section 5](#5-data-model-the-chunk-record).

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
print(responses[0].chunk_count)  # 10
```

To ingest a document that is not in the register, build an `IngestRequest` yourself and pass it to `ingest`. All fields except `product_id`, `effective_to` and `supersedes` are required.

### Walk through it as a lesson: the notebook

[phase1_session1_ingestion.ipynb](phase1_session1_ingestion.ipynb) is the teaching version of this same pipeline. It does not import `app/`, and it maps each section to the file it mirrors. Open it with the project's environment as the kernel and run it from the project root.

If `MONGODB_URI` and `OPENAI_API_KEY` are not set, it switches to **offline mode** (in-memory store, fake embeddings). Every stage still runs, but the similarity ranking in the last section is arbitrary. With real credentials, that section shows several policy versions competing for the same question, which is the reason version metadata exists.

### Adapting it

| To change | Where |
|---|---|
| Chunk size | `CHUNK_MAX_CHARACTERS` in `.env`, no code change |
| Embedding model or dimension | `.env`, then create a new Atlas index version and re-embed |
| The corpus | Add the file to the register, then re-run the command |
| What is stored per chunk | `enrich` inside [app/chains/ingestion.py](app/chains/ingestion.py) |
| Parsing rules | [app/chains/parsing.py](app/chains/parsing.py) |

---

## 8. Testing and quality checks

```bash
uv run pytest -q              # 6 tests, no network or credentials needed
uv run ruff check app tests   # lint
uv run mypy app               # type check
```

The tests run the real parser and chain over your synthetic PDFs, with an in-memory vector store and fake embeddings in place of Atlas and OpenAI.

| Test | What it proves |
|---|---|
| `test_register_maps_to_requests` | The register yields 8 requests with correct dates, types and a null product for the operations policy |
| `test_every_document_parses_and_chunks` | All 8 PDFs ingest and every chunk has the nine metadata fields |
| `test_version_metadata_is_stamped_on_every_chunk` | Product, version, effective date and `supersedes` appear on every chunk |
| `test_tables_are_kept_whole` | The FOIR table is one chunk with all three rows intact |
| `test_running_headers_and_footers_are_dropped` | Page headers and footers do not leak into chunk text |
| `test_reingesting_is_idempotent` | Ingesting the same document twice leaves the chunk count unchanged |

What the tests do **not** cover: real embedding quality, Atlas index behaviour, or search results. Those need credentials and are verified by running the ingestion command and checking Atlas.

---

## 9. Design decisions

- **The register drives ingestion, not a folder listing.** `document_register.json` records which documents are authoritative, their effective windows and what supersedes what. Customer uploads in `nexa_synthetic_data/uploads/` are deliberately not indexed as policy.
- **Version metadata on every chunk.** Similarity search cannot tell a current clause from a superseded one; metadata filters can. This is the main finance-specific design point of the deck.
- **Tables are kept whole and stored as HTML.** A fee or threshold table flattened to text loses which value belongs to which band.
- **Deterministic chunk IDs.** `{doc_id}_c{nnn}` makes writes idempotent.
- **`pdfplumber` for PDFs.** Unstructured's fast mode split table cells into stray headings, and its table-aware mode needs poppler, tesseract and large model downloads. `pdfplumber` is lighter and works on born-digital PDFs. Scanned PDFs would need a different parser.
- **Config in the environment.** Model names and chunk size can change without a redeploy.
- **Manual Atlas index.** The index definition is version-controlled in `indexes.md` rather than created by code.

### Known limitations

- Tables land in their own chunk, separate from the sentence that introduces them (for example "3.4 FOIR limit" is in the chunk before its table). Session 2 fixes this with clause-aware chunking.
- Re-ingesting a document that became shorter leaves its old trailing chunks behind, because nothing deletes IDs that no longer exist.
- There is no checksum step, so every run re-embeds every chunk.
- `section_type` is only `text` or `table`.
- The Atlas filter fields cover product, jurisdiction, effective date and confidentiality. As-of queries will also need `effective_to` and probably `doc_id`.
- The pipeline has been tested end to end with fake embeddings and an in-memory store, not against a live Atlas cluster.

---

## 10. Roadmap

```mermaid
flowchart LR
    S1["S1: Architecture +<br/>ingestion"]:::done --> S2["S2: Clause-aware chunking,<br/>parent docs, checksums"]
    S2 --> S3["S3: Embeddings and<br/>vector index"]
    S3 --> S4["S4: Retrieval API<br/>/retrieve"]
    S4 --> S5["S5: Failure modes,<br/>Week 1 checkpoint"]
    S5 --> W2["Week 2: hybrid retrieval,<br/>rerank, evaluation,<br/>observability"]

    classDef done fill:#d1fae5,stroke:#047857,color:#064e3b
```

| Session | Adds |
|---|---|
| 2 | Clause-aware chunking with a `clause` field, parent-document retrieval, checksum-based incremental re-ingest |
| 3 | Embedding model and dimension decisions, the Atlas index in depth |
| 4 | `/retrieve` endpoint (FastAPI) with filters including `as_of`, citations and timings |
| 5 | Failure modes and alerts, Week 1 checkpoint |
| Week 2 | BM25 and reciprocal rank fusion, reranking, context assembly with a chat model, RAGAS and LLM-as-judge evaluation against the golden Q&A set, tracing |

Phases 2 to 4 (agents, MCP, production) wrap this service without rewriting it, so the ingestion contract and the chunk record shape should stay stable.

---

## Further reading

- [CLAUDE.md](CLAUDE.md): coding rules and layer boundaries
- [nexa_synthetic_data/README.md](nexa_synthetic_data/README.md): the corpus, golden Q&A, customer data and mock API
- [app/database/indexes.md](app/database/indexes.md): the Atlas index definition
