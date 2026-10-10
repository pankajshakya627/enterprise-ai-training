from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongodb_uri: SecretStr
    mongodb_db: str
    mongodb_collection: str
    vector_index_name: str = "policy_chunks_v1"
    mongodb_parent_collection: str = "policy_sections"

    openai_api_key: SecretStr
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    embedding_batch_size: int = 100

    chunk_max_characters: int = 1200
    chunk_overlap_characters: int = 150

    # Corpus-wide metadata that the document register does not carry.
    institution: str
    jurisdiction: str
    confidentiality_level: str

    log_level: str = "INFO"
    dd_trace_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
