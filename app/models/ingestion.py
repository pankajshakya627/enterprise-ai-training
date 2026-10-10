from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class IngestRequest(BaseModel):
    """Ingestion Service contract: one source document plus its document-level metadata.

    The ninth metadata field, section_type, is set per chunk by the pipeline, as are
    clause, parent_id and checksum.
    """

    model_config = ConfigDict(extra="forbid")

    source_path: str
    doc_id: str
    title: str
    institution: str
    product_id: str | None = None
    policy_version: str
    effective_date: date
    effective_to: date | None = None
    supersedes: str | None = None
    jurisdiction: str
    doc_type: str
    confidentiality_level: str


class IngestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    chunk_count: int
    chunk_ids: list[str]
    embedded_count: int
    unchanged_count: int
    embedding_model: str
