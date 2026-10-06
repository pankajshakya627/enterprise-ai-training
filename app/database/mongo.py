from __future__ import annotations

from langchain_core.embeddings import Embeddings
from langchain_mongodb import MongoDBAtlasVectorSearch
from pymongo import MongoClient

from app.core.config import Settings


def get_vector_store(settings: Settings, embedding: Embeddings) -> MongoDBAtlasVectorSearch:
    """Chunk store shared by the write path and the read path.

    The Atlas Vector Search index is created manually; see indexes.md.
    """
    client: MongoClient = MongoClient(settings.mongodb_uri.get_secret_value())
    collection = client[settings.mongodb_db][settings.mongodb_collection]
    return MongoDBAtlasVectorSearch(
        collection=collection,
        embedding=embedding,
        index_name=settings.vector_index_name,
        text_key="text",
        embedding_key="embedding",
        relevance_score_fn="cosine",
        auto_create_index=False,
    )
