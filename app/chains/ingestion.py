from __future__ import annotations

from typing import Any

from langchain_core.documents import Document
from langchain_core.runnables import Runnable, RunnableLambda, RunnablePassthrough
from langchain_core.vectorstores import VectorStore
from unstructured.chunking.title import chunk_by_title
from unstructured.documents.elements import Element, Table

from app.chains.parsing import parse_document
from app.core.config import Settings
from app.models.ingestion import IngestRequest, IngestResponse


def build_ingestion_chain(
    vector_store: VectorStore, settings: Settings
) -> Runnable[dict[str, Any], IngestResponse]:
    """Write path: load + parse -> chunk -> enrich -> embed + upsert.

    Input: {"request": IngestRequest}. Run with `ainvoke`.
    """

    def parse(state: dict[str, Any]) -> list[Element]:
        return parse_document(state["request"].source_path)

    def chunk(state: dict[str, Any]) -> list[Element]:
        # Section-aware: a new chunk starts at every heading, and a table is never
        # merged into (or split across) neighbouring text unless it exceeds the limit.
        return chunk_by_title(
            state["elements"],
            max_characters=settings.chunk_max_characters,
            combine_text_under_n_chars=0,
        )

    def enrich(state: dict[str, Any]) -> list[Document]:
        request: IngestRequest = state["request"]
        doc_metadata = request.model_dump(mode="json", exclude={"source_path"})

        documents = []
        for position, element in enumerate(state["chunks"], start=1):
            is_table = isinstance(element, Table)
            chunk_id = f"{request.doc_id}_c{position:03d}"
            documents.append(
                Document(
                    id=chunk_id,
                    # HTML keeps each fee tied to its balance band; flat text does not.
                    page_content=(element.metadata.text_as_html or element.text)
                    if is_table
                    else element.text,
                    metadata={
                        **doc_metadata,
                        "chunk_id": chunk_id,
                        "section_type": "table" if is_table else "text",
                        "embedding_model": settings.embedding_model,
                    },
                )
            )
        return documents

    async def embed_and_upsert(state: dict[str, Any]) -> IngestResponse:
        documents: list[Document] = state["documents"]
        chunk_ids = [document.metadata["chunk_id"] for document in documents]
        # Deterministic ids make the write idempotent: a re-run replaces, never duplicates.
        await vector_store.aadd_documents(documents, ids=chunk_ids)
        return IngestResponse(
            doc_id=state["request"].doc_id,
            chunk_count=len(documents),
            chunk_ids=chunk_ids,
            embedding_model=settings.embedding_model,
        )

    return (
        RunnablePassthrough.assign(elements=RunnableLambda(parse).with_config(run_name="parse"))
        | RunnablePassthrough.assign(chunks=RunnableLambda(chunk).with_config(run_name="chunk"))
        | RunnablePassthrough.assign(documents=RunnableLambda(enrich).with_config(run_name="enrich"))
        | RunnableLambda(embed_and_upsert).with_config(run_name="embed_and_upsert")
    )
