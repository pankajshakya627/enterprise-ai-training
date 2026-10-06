from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.vectorstores import InMemoryVectorStore

from app.chains.ingestion import build_ingestion_chain
from app.core.config import Settings
from app.models.ingestion import IngestRequest
from app.services.ingestion import requests_from_register

REGISTER = Path("nexa_synthetic_data/document_register.json")
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


def ingest(store: InMemoryVectorStore, settings: Settings, request: IngestRequest) -> list[Document]:
    chain = build_ingestion_chain(store, settings)
    response = asyncio.run(chain.ainvoke({"request": request}))
    assert response.chunk_count == len(response.chunk_ids) > 0
    return store.get_by_ids(response.chunk_ids)


@pytest.fixture(scope="module")
def home_loan_v3(settings: Settings, requests: dict[str, IngestRequest]) -> list[Document]:
    store = InMemoryVectorStore(DeterministicFakeEmbedding(size=8))
    return ingest(store, settings, requests["POL-HL-V3"])


def test_register_maps_to_requests(requests: dict[str, IngestRequest]) -> None:
    assert len(requests) == 8
    circular = requests["CIR-2026-07"]
    assert circular.doc_type == "circular"
    assert circular.effective_date.isoformat() == "2026-07-01"
    assert requests["POL-HL-V2"].effective_to.isoformat() == "2025-09-30"
    assert requests["POL-OPS-001"].product_id is None


def test_every_document_parses_and_chunks(
    settings: Settings, requests: dict[str, IngestRequest]
) -> None:
    store = InMemoryVectorStore(DeterministicFakeEmbedding(size=8))
    for request in requests.values():
        for document in ingest(store, settings, request):
            assert METADATA_FIELDS <= document.metadata.keys()
            assert document.metadata["doc_id"] == request.doc_id
            assert document.id.startswith(f"{request.doc_id}_c")


def test_version_metadata_is_stamped_on_every_chunk(home_loan_v3: list[Document]) -> None:
    for document in home_loan_v3:
        assert document.metadata["product_id"] == "NEXA-HL"
        assert document.metadata["policy_version"] == "3.0"
        assert document.metadata["effective_date"] == "2025-10-01"
        assert document.metadata["supersedes"] == "POL-HL-V2"


def test_tables_are_kept_whole(home_loan_v3: list[Document]) -> None:
    tables = [d.page_content for d in home_loan_v3 if d.metadata["section_type"] == "table"]
    foir = next(table for table in tables if "Maximum FOIR" in table)
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
