from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import structlog

from app.chains.embeddings import get_embeddings
from app.chains.ingestion import build_ingestion_chain
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.telemetry import init_telemetry
from app.database.mongo import get_vector_store
from app.models.ingestion import IngestRequest, IngestResponse

logger = structlog.get_logger()

DOC_TYPES = {"POL": "policy", "CIR": "circular"}


def requests_from_register(register_path: Path, settings: Settings) -> list[IngestRequest]:
    """One IngestRequest per authoritative document in document_register.json."""
    return [
        IngestRequest(
            source_path=str(register_path.parent / entry["file"]),
            doc_id=entry["doc_id"],
            title=entry["title"],
            institution=settings.institution,
            product_id=entry["product_id"],
            policy_version=entry["version"],
            effective_date=entry["effective_from"],
            effective_to=entry["effective_to"],
            supersedes=entry["supersedes"],
            jurisdiction=settings.jurisdiction,
            doc_type=DOC_TYPES[entry["doc_id"].split("-")[0]],
            confidentiality_level=settings.confidentiality_level,
        )
        for entry in json.loads(register_path.read_text())
        if entry["authoritative"]
    ]


async def ingest(requests: list[IngestRequest]) -> list[IngestResponse]:
    settings = get_settings()
    vector_store = get_vector_store(settings, get_embeddings(settings))
    chain = build_ingestion_chain(vector_store, settings)
    responses = []
    for request in requests:
        response = await chain.ainvoke({"request": request})
        logger.info(
            "document_ingested",
            doc_id=response.doc_id,
            chunk_count=response.chunk_count,
            embedding_model=response.embedding_model,
            policy_version=request.policy_version,
            effective_date=request.effective_date.isoformat(),
        )
        responses.append(response)
    return responses


def main() -> None:
    """Usage: python -m app.services.ingestion <path/to/document_register.json>"""
    if len(sys.argv) != 2:
        sys.exit(main.__doc__)
    settings = get_settings()
    configure_logging(settings.log_level)
    init_telemetry(settings.dd_trace_enabled)
    asyncio.run(ingest(requests_from_register(Path(sys.argv[1]), settings)))


if __name__ == "__main__":
    main()
