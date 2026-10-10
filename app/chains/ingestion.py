from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any

from langchain_core.documents import Document
from langchain_core.runnables import Runnable, RunnableLambda, RunnablePassthrough
from langchain_core.stores import BaseStore
from langchain_core.vectorstores import VectorStore
from unstructured.documents.elements import Element

from app.chains.chunking import Chunk, chunk_by_clause
from app.chains.parsing import parse_document
from app.core.config import Settings
from app.models.ingestion import IngestRequest, IngestResponse


def parent_key_prefix(doc_id: str) -> str:
    return f"{doc_id}_s"


def chunk_checksum(text: str, metadata: dict[str, Any]) -> str:
    """Fingerprint of everything that should force a re-embed: text, metadata, model.

    `metadata` carries `embedding_model`, so switching models changes every checksum.
    """
    payload = json.dumps({"text": text, "metadata": metadata}, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def build_ingestion_chain(
    vector_store: VectorStore,
    parent_store: BaseStore[str, Document],
    settings: Settings,
) -> Runnable[dict[str, Any], IngestResponse]:
    """Write path: parse -> chunk -> enrich + hash -> compare -> embed + upsert.

    Children are clause chunks (embedded, searched). Parents are whole sections, kept in
    `parent_store` under `parent_id` so the read path can return the section around a match.
    Only children whose checksum differs from the stored one are embedded.
    Input: {"request": IngestRequest}. Run with `ainvoke`.
    """

    def parse(state: dict[str, Any]) -> list[Element]:
        return parse_document(state["request"].source_path)

    def chunk(state: dict[str, Any]) -> list[Chunk]:
        return chunk_by_clause(
            state["elements"], settings.chunk_max_characters, settings.chunk_overlap_characters
        )

    def enrich(state: dict[str, Any]) -> dict[str, list[Document]]:
        request: IngestRequest = state["request"]
        doc_metadata = request.model_dump(mode="json", exclude={"source_path"})

        def header(section: str) -> str:
            # Identity line: a chunk read on its own still says which document it is from.
            return f"[{request.doc_id} | {request.title} v{request.policy_version} | {section}]"

        children: list[Document] = []
        by_section: dict[str, list[Chunk]] = defaultdict(list)
        positions: Counter[str] = Counter()
        for item in state["chunks"]:
            # Keyed by clause, not by position: inserting a clause does not renumber the rest.
            key = item.clause or str(item.section_number)
            positions[key] += 1
            chunk_id = f"{request.doc_id}_{key}_c{positions[key]:02d}"
            parent_id = f"{parent_key_prefix(request.doc_id)}{item.section_number:02d}"
            text = f"{header(item.section)}\n{item.text}"
            metadata = {
                **doc_metadata,
                "chunk_id": chunk_id,
                "parent_id": parent_id,
                "section": item.section,
                "clause": item.clause,
                "clause_heading": item.clause_heading,
                "section_type": "table" if item.has_table else "text",
                "embedding_model": settings.embedding_model,
            }
            metadata["checksum"] = chunk_checksum(text, metadata)
            children.append(Document(id=chunk_id, page_content=text, metadata=metadata))
            by_section[parent_id].append(item)

        parents = [
            Document(
                id=parent_id,
                page_content="\n".join([header(items[0].section), *(i.text for i in items)]),
                metadata={**doc_metadata, "parent_id": parent_id, "section": items[0].section},
            )
            for parent_id, items in by_section.items()
        ]
        return {"children": children, "parents": parents}

    async def compare(state: dict[str, Any]) -> list[Document]:
        children: list[Document] = state["enriched"]["children"]
        stored = {
            document.id: document.metadata.get("checksum")
            for document in await vector_store.aget_by_ids(
                [child.id for child in children if child.id]
            )
        }
        return [child for child in children if stored.get(child.id) != child.metadata["checksum"]]

    async def embed_and_upsert(state: dict[str, Any]) -> IngestResponse:
        request: IngestRequest = state["request"]
        children: list[Document] = state["enriched"]["children"]
        parents: list[Document] = state["enriched"]["parents"]
        changed: list[Document] = state["changed"]

        # Parents are not embedded, so rewriting them is cheap. The doc store's insert
        # does not overwrite, so this document's sections are cleared first.
        prefix = parent_key_prefix(request.doc_id)
        stale = [key async for key in parent_store.ayield_keys(prefix=prefix)]
        if stale:
            await parent_store.amdelete(stale)
        await parent_store.amset([(parent.id, parent) for parent in parents if parent.id])

        if changed:
            # Deterministic ids make the write idempotent: a re-run replaces, never duplicates.
            await vector_store.aadd_documents(
                changed, ids=[child.metadata["chunk_id"] for child in changed]
            )
        return IngestResponse(
            doc_id=request.doc_id,
            chunk_count=len(children),
            chunk_ids=[child.metadata["chunk_id"] for child in children],
            embedded_count=len(changed),
            unchanged_count=len(children) - len(changed),
            embedding_model=settings.embedding_model,
        )

    return (
        RunnablePassthrough.assign(elements=RunnableLambda(parse).with_config(run_name="parse"))
        | RunnablePassthrough.assign(chunks=RunnableLambda(chunk).with_config(run_name="chunk"))
        | RunnablePassthrough.assign(enriched=RunnableLambda(enrich).with_config(run_name="enrich"))
        | RunnablePassthrough.assign(
            changed=RunnableLambda(compare).with_config(run_name="compare")
        )
        | RunnableLambda(embed_and_upsert).with_config(run_name="embed_and_upsert")
    )
