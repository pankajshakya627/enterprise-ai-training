from __future__ import annotations

import asyncio
import json
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any

import mongomock
import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.stores import InMemoryStore
from langchain_core.vectorstores import InMemoryVectorStore
from unstructured.documents.elements import (
    Element,
    ElementMetadata,
    ListItem,
    NarrativeText,
    Table,
    Title,
)

from app.chains.chunking import chunk_by_clause
from app.chains.ingestion import build_ingestion_chain
from app.core.config import Settings
from app.database.mongo import delete_stale_chunks
from app.models.ingestion import IngestRequest, IngestResponse
from app.services.ingestion import requests_from_register

REGISTER = Path("nexa_synthetic_data/document_register.json")
GOLDEN_QA = Path("nexa_synthetic_data/golden_qa/golden_qa.jsonl")
METADATA_FIELDS = {
    "institution",
    "product_id",
    "policy_version",
    "effective_date",
    "supersedes",
    "jurisdiction",
    "doc_type",
    "section_type",
    "confidentiality_level",
    "clause",
    "parent_id",
    "checksum",
}


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings(
        _env_file=None,
        mongodb_uri="mongodb://unused",
        mongodb_db="unused",
        mongodb_collection="unused",
        openai_api_key="unused",
        institution="NexaBank",
        jurisdiction="IN",
        confidentiality_level="internal",
    )


@pytest.fixture(scope="module")
def requests(settings: Settings) -> dict[str, IngestRequest]:
    return {request.doc_id: request for request in requests_from_register(REGISTER, settings)}


class CountingEmbedding(DeterministicFakeEmbedding):
    """Fake embeddings that record how many texts were sent to the model."""

    embedded: int = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.embedded += len(texts)
        return super().embed_documents(texts)


def run_chain(
    store: InMemoryVectorStore,
    settings: Settings,
    request: IngestRequest,
    parents: InMemoryStore | None = None,
) -> IngestResponse:
    chain = build_ingestion_chain(store, parents or InMemoryStore(), settings)
    response: IngestResponse = asyncio.run(chain.ainvoke({"request": request}))
    assert response.chunk_count == len(response.chunk_ids) > 0
    assert response.embedded_count + response.unchanged_count == response.chunk_count
    return response


def ingest(
    store: InMemoryVectorStore,
    settings: Settings,
    request: IngestRequest,
    parents: InMemoryStore | None = None,
) -> list[Document]:
    return store.get_by_ids(run_chain(store, settings, request, parents).chunk_ids)


@pytest.fixture(scope="module")
def home_loan_v3(settings: Settings, requests: dict[str, IngestRequest]) -> list[Document]:
    store = InMemoryVectorStore(DeterministicFakeEmbedding(size=8))
    return ingest(store, settings, requests["POL-HL-V3"])


def test_register_maps_to_requests(requests: dict[str, IngestRequest]) -> None:
    assert len(requests) == 8
    circular = requests["CIR-2026-07"]
    assert circular.doc_type == "circular"
    assert circular.effective_date.isoformat() == "2026-07-01"
    assert requests["POL-HL-V2"].effective_to == date(2025, 9, 30)
    assert requests["POL-OPS-001"].product_id is None


def test_every_document_parses_and_chunks(
    settings: Settings, requests: dict[str, IngestRequest]
) -> None:
    store = InMemoryVectorStore(DeterministicFakeEmbedding(size=8))
    for request in requests.values():
        for document in ingest(store, settings, request):
            assert METADATA_FIELDS <= document.metadata.keys()
            assert document.metadata["doc_id"] == request.doc_id
            assert document.id == document.metadata["chunk_id"]
            assert document.metadata["chunk_id"].startswith(f"{request.doc_id}_")


def test_version_metadata_is_stamped_on_every_chunk(home_loan_v3: list[Document]) -> None:
    for document in home_loan_v3:
        assert document.metadata["product_id"] == "NEXA-HL"
        assert document.metadata["policy_version"] == "3.0"
        assert document.metadata["effective_date"] == "2025-10-01"
        assert document.metadata["supersedes"] == "POL-HL-V2"


def test_tables_are_kept_whole(home_loan_v3: list[Document]) -> None:
    tables = [d.page_content for d in home_loan_v3 if d.metadata["section_type"] == "table"]
    foir = next(table for table in tables if "Maximum FOIR" in table)
    # The table stays with the clause that introduces it.
    assert "3.4 FOIR limit. The maximum FOIR depends on net monthly income" in foir
    for row in (
        "<tr><td>Below ₹1,00,000</td><td>50%</td></tr>",
        "<tr><td>₹1,00,000 to ₹2,49,999</td><td>55%</td></tr>",
        "<tr><td>₹2,50,000 and above</td><td>60%</td></tr>",
    ):
        assert row in foir


def test_running_headers_and_footers_are_dropped(home_loan_v3: list[Document]) -> None:
    text = "\n".join(document.page_content for document in home_loan_v3)
    assert "SYNTHETIC DOCUMENT" not in text
    assert "Not a real bank" not in text
    assert "6.2 Processing fee. Processing fee is 0.35%" in text


def test_reingesting_is_idempotent(settings: Settings, requests: dict[str, IngestRequest]) -> None:
    store = InMemoryVectorStore(DeterministicFakeEmbedding(size=8))
    first = ingest(store, settings, requests["CIR-2026-07"])
    second = ingest(store, settings, requests["CIR-2026-07"])

    assert [d.id for d in first] == [d.id for d in second]
    assert len(store.store) == len(first)


def test_every_registered_clause_is_exactly_one_chunk(
    settings: Settings, requests: dict[str, IngestRequest]
) -> None:
    store = InMemoryVectorStore(DeterministicFakeEmbedding(size=8))
    for entry in json.loads(REGISTER.read_text()):
        chunks = ingest(store, settings, requests[entry["doc_id"]])
        headings = [(d.metadata["clause"], d.metadata["clause_heading"]) for d in chunks]
        registered = [(clause["clause"], clause["heading"]) for clause in entry["clauses"]]
        assert sorted(h for h in headings if h[0]) == sorted(registered)


def test_every_golden_citation_resolves_to_a_chunk(
    settings: Settings, requests: dict[str, IngestRequest]
) -> None:
    store = InMemoryVectorStore(DeterministicFakeEmbedding(size=8))
    indexed = {
        (d.metadata["doc_id"], d.metadata["clause"])
        for request in requests.values()
        for d in ingest(store, settings, request)
    }
    cited = {
        (source["doc_id"], source["clause"])
        for line in GOLDEN_QA.read_text().splitlines()
        for source in json.loads(line)["expected_sources"]
        if source["doc_id"] in requests  # the rate card (CATALOG) is not a policy document
    }
    assert cited and cited <= indexed


def test_chunk_carries_identity_header_and_clause_id(home_loan_v3: list[Document]) -> None:
    fee = next(d for d in home_loan_v3 if d.metadata["clause"] == "6.2")
    assert fee.id == "POL-HL-V3_6.2_c01"
    assert fee.metadata["clause_heading"] == "Processing fee"
    assert fee.metadata["section"] == "6. Fees and charges"
    assert fee.page_content.startswith(
        "[POL-HL-V3 | NexaHome Loan Policy v3.0 | 6. Fees and charges]\n6.2 Processing fee."
    )
    assert "6.3" not in fee.page_content


def test_oversized_clause_splits_narrative_with_overlap_but_never_the_table() -> None:
    table = Table(
        text="Band Fee A 1% B 2%",
        metadata=ElementMetadata(
            text_as_html="<table>" + "<tr><td>Band</td></tr>" * 20 + "</table>"
        ),
    )
    sentences = [f"Sentence number {n} of the clause." for n in range(1, 9)]
    elements: list[Element] = [
        Title("1. Fees"),
        NarrativeText("1.1 Long clause. " + " ".join(sentences)),
        table,
        NarrativeText("1.2 Short clause. Fits in one chunk."),
    ]
    chunks = chunk_by_clause(elements, max_characters=120, overlap=40)

    long_clause = [c for c in chunks if c.clause == "1.1"]
    tables = [c for c in long_clause if c.has_table]
    narrative = [c for c in long_clause if not c.has_table]
    assert [c.text for c in tables] == [table.metadata.text_as_html]  # whole, though over the limit
    assert len(narrative) > 1 and all(len(c.text) <= 120 for c in narrative)
    # Overlap: each piece starts with the sentence that ended the previous one.
    for previous, current in pairwise(narrative):
        assert previous.text.rstrip().endswith(current.text.split(". ")[0].rstrip(".") + ".")
    assert [c.text for c in chunks if c.clause == "1.2"] == ["1.2 Short clause. Fits in one chunk."]


def test_oversized_list_clause_is_packed_not_split_per_item() -> None:
    items = [f"Item {n}: required document number {n} for the loan." for n in range(1, 8)]
    elements: list[Element] = [
        Title("1. Documents"),
        NarrativeText("1.1 Documents required. Submit the following documents."),
        *(ListItem(text) for text in items),
    ]
    chunks = [
        c for c in chunk_by_clause(elements, max_characters=200, overlap=60) if c.clause == "1.1"
    ]

    assert len(chunks) < len(items)  # packed, not one chunk per element
    assert all(len(c.text) <= 200 for c in chunks)
    assert chunks[0].text.startswith("1.1 Documents required.")
    joined = "\n".join(c.text for c in chunks)
    assert all(item in joined for item in items)  # no item is lost


def test_parent_section_holds_every_clause_of_the_section(
    settings: Settings, requests: dict[str, IngestRequest]
) -> None:
    store = InMemoryVectorStore(DeterministicFakeEmbedding(size=8))
    parents: InMemoryStore = InMemoryStore()
    chunks = ingest(store, settings, requests["POL-HL-V3"], parents)

    fee = next(d for d in chunks if d.metadata["clause"] == "6.2")
    assert fee.metadata["parent_id"] == "POL-HL-V3_s06"
    parent = parents.mget([fee.metadata["parent_id"]])[0]
    assert parent is not None
    assert parent.metadata["policy_version"] == "3.0"
    for clause in (
        "6. Fees and charges",
        "6.1 General.",
        "6.2 Processing fee.",
        "6.5 Other costs.",
    ):
        assert clause in parent.page_content
    assert "7.1 Security." not in parent.page_content


def test_reingesting_replaces_parent_sections(
    settings: Settings, requests: dict[str, IngestRequest]
) -> None:
    store = InMemoryVectorStore(DeterministicFakeEmbedding(size=8))
    parents: InMemoryStore = InMemoryStore()
    ingest(store, settings, requests["CIR-2026-07"], parents)
    keys = sorted(parents.yield_keys())
    ingest(store, settings, requests["CIR-2026-07"], parents)
    assert sorted(parents.yield_keys()) == keys


def test_only_new_or_changed_chunks_are_embedded(
    settings: Settings, requests: dict[str, IngestRequest]
) -> None:
    embedding = CountingEmbedding(size=8)
    store = InMemoryVectorStore(embedding)
    request = requests["POL-HL-V3"]

    first = run_chain(store, settings, request)
    assert first.embedded_count == first.chunk_count == embedding.embedded

    second = run_chain(store, settings, request)
    assert (second.embedded_count, second.unchanged_count) == (0, first.chunk_count)
    assert embedding.embedded == first.chunk_count  # no call to the model at all

    # One clause's stored checksum no longer matches: only that chunk is embedded again.
    store.store["POL-HL-V3_6.2_c01"]["metadata"]["checksum"] = "sha256:stale"
    third = run_chain(store, settings, request)
    assert third.embedded_count == 1
    assert store.get_by_ids(["POL-HL-V3_6.2_c01"])[0].metadata["checksum"] != "sha256:stale"


def test_changing_the_embedding_model_re_embeds_everything(
    settings: Settings, requests: dict[str, IngestRequest]
) -> None:
    store = InMemoryVectorStore(DeterministicFakeEmbedding(size=8))
    first = run_chain(store, settings, requests["CIR-2026-07"])
    other_model = settings.model_copy(update={"embedding_model": "another-model"})
    second = run_chain(store, other_model, requests["CIR-2026-07"])
    assert second.embedded_count == first.chunk_count


def test_stale_chunks_are_deleted_and_current_ones_kept() -> None:
    collection: Any = mongomock.MongoClient().db.chunks
    collection.insert_many(
        [
            {"_id": "POL-X_1.1_c01", "doc_id": "POL-X"},
            {"_id": "POL-X_c002", "doc_id": "POL-X"},
            {"_id": "POL-Y_1.1_c01", "doc_id": "POL-Y"},
        ]
    )
    assert delete_stale_chunks(collection, "POL-X", ["POL-X_1.1_c01"]) == 1
    assert sorted(row["_id"] for row in collection.find()) == ["POL-X_1.1_c01", "POL-Y_1.1_c01"]
