"""Load and validate application, UI and agent settings from the environment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.validation import AgentPrincipal


class Settings(BaseSettings):
    """Validated RAG, persistence, model and telemetry configuration.

    Attributes:
        mongodb_uri: Secret MongoDB connection string used by storage integrations.
        mongodb_db: Configured database containing the scoped application collections.
        mongodb_collection: Collection storing searchable embedded policy chunks.
        vector_index_name: Atlas Vector Search index configured for policy chunks.
        mongodb_parent_collection: Collection storing complete parent policy sections.
        mongodb_feedback_collection: Collection storing user feedback and review decisions.
        mongodb_agent_runs_collection: Collection storing owner-scoped run lifecycle records.
        mongodb_agent_checkpoints_collection: Collection storing durable graph checkpoints.
        mongodb_agent_writes_collection: Collection storing pending durable checkpoint writes.
        mongodb_agent_retention_seconds: Run/checkpoint retention duration, longer than the safe retry window.
        feedback_reviewer_token: Secret credential required to load and decide feedback reviews.
        openai_api_key: Secret provider credential used by chat and embedding integrations.
        embedding_model: Configured embedding model identity used in chunk compatibility checks.
        embedding_dimensions: Vector dimensions shared by ingestion, retrieval and Atlas indexing.
        embedding_batch_size: Maximum embedding inputs sent per configured batch.
        chat_model: Configured provider model identifier for generation and classification.
        chat_max_output_tokens: Optional output-token ceiling for the configured chat model.
        eval_judge_model: Optional model override for quality judgments; otherwise the chat model.
        retrieval_mode: Dense or hybrid Atlas candidate-search strategy.
        text_index_name: Atlas lexical search index used by hybrid retrieval.
        retrieval_candidates: Number of candidates considered before optional reranking.
        rerank_enabled: Whether retrieved candidates pass through the configured reranker.
        context_max_tokens: Maximum tokens in the serialized retained evidence payload.
        rerank_model: Configured local relevance-scoring model identifier.
        rerank_api: Configured local reranker protocol: Ollama, OpenAI-compatible or llama.cpp.
        rerank_base_url: HTTP endpoint hosting the optional local reranker.
        rerank_api_key: Secret credential for the configured local reranker endpoint.
        rerank_max_tokens: Scoring-token limit for the selected reranker protocol.
        token_encoding: Tokenizer identity used for context and model-work estimates.
        chunk_max_characters: Target maximum characters in a packed clause chunk.
        chunk_overlap_characters: Narrative overlap allowed when splitting an oversized clause.
        institution: Institution boundary used by corpus queries and run persistence.
        jurisdiction: Jurisdiction boundary retained in the policy lookup scope.
        confidentiality_level: Corpus confidentiality scope applied to ingestion and retrieval.
        log_level: Structured logging threshold used at application startup.
        dd_trace_enabled: Whether startup patches supported Datadog integrations.
        dd_service: Service identity assigned to application telemetry.
        dd_site: Validated Datadog site domain used for workflow links.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongodb_uri: SecretStr
    mongodb_db: str
    mongodb_collection: str
    vector_index_name: str = "policy_chunks_v1"
    mongodb_parent_collection: str = "policy_sections"
    mongodb_feedback_collection: str = "policy_feedback"
    mongodb_agent_runs_collection: str = "agent_runs"
    mongodb_agent_checkpoints_collection: str = "agent_checkpoints"
    mongodb_agent_writes_collection: str = "agent_checkpoint_writes"
    mongodb_agent_retention_seconds: int = Field(default=604800, ge=172800, le=31536000)
    feedback_reviewer_token: SecretStr | None = Field(default=None, min_length=32)

    @field_validator("feedback_reviewer_token", mode="before")
    @classmethod
    def empty_reviewer_token(cls, value: object) -> object:
        """Normalize an empty feedback reviewer token to an unconfigured value."""
        return None if value == "" else value

    openai_api_key: SecretStr
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, gt=0)
    embedding_batch_size: int = Field(default=100, ge=1, le=100)
    chat_model: str = "gpt-6-luna"
    chat_max_output_tokens: int | None = Field(default=None, ge=256, le=32768)
    eval_judge_model: str | None = None
    retrieval_mode: Literal["dense", "hybrid"] = "dense"
    text_index_name: str = "policy_text_v1"
    retrieval_candidates: int = Field(default=20, ge=20, le=50)
    rerank_enabled: bool = False
    context_max_tokens: int = Field(default=6000, ge=256)
    rerank_model: str = "nexaassist-reranker:0.6b-mlx"
    rerank_api: Literal["ollama", "openai", "llamacpp"] = "ollama"
    rerank_base_url: str = "http://127.0.0.1:11434"
    rerank_api_key: SecretStr = SecretStr("ollama")
    rerank_max_tokens: int = Field(default=4000, ge=256)
    token_encoding: str = "o200k_base"

    chunk_max_characters: int = Field(default=1200, gt=0)
    chunk_overlap_characters: int = Field(default=150, ge=0)

    # Corpus-wide metadata that the document register does not carry.
    institution: str
    jurisdiction: str
    confidentiality_level: str

    log_level: str = "INFO"
    dd_trace_enabled: bool = False
    dd_service: str = "nexaassist-api"
    dd_site: str = Field(default="datadoghq.com", pattern=r"^[a-z0-9.-]+$")

    @model_validator(mode="after")
    def valid_overlap(self) -> Settings:
        """Reject chunk overlap that would consume the entire chunk window.

        Raises:
            ValueError: CHUNK_OVERLAP_CHARACTERS must be smaller than CHUNK_MAX_CHARACTERS.
        """
        if self.chunk_overlap_characters >= self.chunk_max_characters:
            raise ValueError("CHUNK_OVERLAP_CHARACTERS must be smaller than CHUNK_MAX_CHARACTERS")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached, validated RAG and storage configuration.

    Returns:
        Cached application settings validated from the configured environment.

    Raises:
        ValidationError: Required environment values are missing or invalid.
    """
    return Settings()


class UISettings(BaseSettings):
    """Validated UI endpoint and local timezone configuration.

    Attributes:
        api_base_url: RAG FastAPI endpoint used for policy chat and feedback.
        agent_api_base_url: Agent FastAPI endpoint used by the Streamlit service workspace.
        ui_timezone: Timezone used when initializing the UI policy date.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_base_url: str = "http://127.0.0.1:8000"
    agent_api_base_url: str = "http://127.0.0.1:8001"
    ui_timezone: str = "Asia/Kolkata"


@lru_cache
def get_ui_settings() -> UISettings:
    """Return the cached UI endpoint and timezone configuration.

    Returns:
        Cached endpoint and timezone settings for the UI.

    Raises:
        ValidationError: UI environment values do not satisfy the settings contract.
    """
    return UISettings()


class AgentSettings(BaseSettings):
    """Validated named identities, banking endpoints and bounded workflow settings.

    Attributes:
        rag_base_url: RAG HTTP endpoint used by the agent retrieval adapter.
        agent_retrieval_transport: Explicit HTTP or local MCP retrieval integration.
        agent_http_timeout_seconds: Deadline for each RAG or banking HTTP request.
        agent_run_timeout_seconds: Deadline for one graph start or resume operation.
        banking_transport: MCP by default; HTTP is explicit compatibility.
        banking_mcp_transport: Stdio or authenticated HTTP between the Agent and banking MCP.
        banking_mcp_url: HTTP MCP endpoint; defaults to http://127.0.0.1:9002/mcp.
        banking_mcp_port: Local HTTP listener port, from 1024 to 65535; defaults to 9002.
        banking_mcp_token: Private HTTP connection token, at least 32 characters.
        banking_mcp_signing_key: Private request-signing key, at least 32 characters.
        banking_mcp_customer_ids: Customer scope for token-only Inspector sessions.
        banking_mcp_write_grant: Private exact-operation binding supplied by the trusted launcher.
        banking_base_url: Banking API destination bound to durable operation recovery.
        banking_api_token: Secret upstream banking caller credential used for operation identity.
        agent_validation_token: Optional legacy training credential, excluded from durable-run ownership.
        agent_allowed_customer_ids: Customer scope for the legacy shared training credential.
        agent_principals: Named identities with distinct secrets, customer scopes and reviewer rights.
        agent_allow_direct_submit: Whether the legacy direct submission endpoint is explicitly enabled.
        agent_approval_ttl_seconds: Seconds from run creation until the proposal approval expires.
        agent_model_max_output_tokens: Output-token ceiling passed to the agent chat model.
        agent_model_max_calls: Maximum chat calls reserved within one graph execution.
        agent_model_token_budget: Conservative token-work reservation limit for one execution.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    rag_base_url: HttpUrl = HttpUrl("http://127.0.0.1:8000/")
    agent_retrieval_transport: Literal["http", "mcp"] = "http"
    agent_http_timeout_seconds: float = Field(default=60, gt=0, le=300)
    agent_run_timeout_seconds: float = Field(default=180, gt=0, le=600)
    banking_transport: Literal["http", "mcp"] = "mcp"
    banking_mcp_transport: Literal["stdio", "http"] = "stdio"
    banking_mcp_auth_mode: Literal["token", "oauth"] = "token"
    banking_mcp_url: HttpUrl = HttpUrl("http://127.0.0.1:9002/mcp")
    banking_mcp_port: int = Field(default=9002, ge=1024, le=65535)
    banking_mcp_token: SecretStr | None = Field(default=None, min_length=32)
    banking_mcp_signing_key: SecretStr | None = Field(default=None, min_length=32)
    banking_mcp_customer_ids: frozenset[str] = Field(default_factory=frozenset)
    banking_mcp_write_grant: SecretStr | None = Field(default=None, exclude=True)
    banking_mcp_oauth_directory: Path = Path.home() / ".local/share/nexa-banking-mcp/oauth"
    banking_mcp_oauth_storage_key: SecretStr | None = None
    banking_base_url: HttpUrl = HttpUrl("http://127.0.0.1:8080/")
    banking_api_token: SecretStr | None = Field(default=None, min_length=1)
    agent_validation_token: SecretStr | None = Field(default=None, min_length=32)
    agent_allowed_customer_ids: frozenset[str] = Field(default_factory=frozenset)
    agent_principals: list[AgentPrincipal] = Field(default_factory=list)
    agent_allow_direct_submit: bool = False
    agent_approval_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    agent_model_max_output_tokens: int = Field(default=2048, ge=256, le=32768)
    agent_model_max_calls: int = Field(default=3, ge=1, le=3)
    agent_model_token_budget: int = Field(default=64000, ge=1024, le=256000)

    @model_validator(mode="after")
    def unique_principals(self) -> AgentSettings:
        """Reject duplicate identities, duplicate tokens and reserved training identities.

        Raises:
            ValueError: Agent identity credentials must differ from the legacy training token.
        """
        names = [principal.principal_id for principal in self.agent_principals]
        tokens = [principal.token.get_secret_value() for principal in self.agent_principals]
        if len(set(names)) != len(names) or "training" in names or len(set(tokens)) != len(tokens):
            raise ValueError(
                "Agent identities and credentials must be unique; training is reserved"
            )
        if (
            self.agent_validation_token is not None
            and self.agent_validation_token.get_secret_value() in tokens
        ):
            raise ValueError(
                "Agent identity credentials must differ from the legacy training token"
            )
        return self

    @field_validator(
        "banking_api_token",
        "agent_validation_token",
        "banking_mcp_write_grant",
        "banking_mcp_token",
        "banking_mcp_signing_key",
        "banking_mcp_oauth_storage_key",
        mode="before",
    )
    @classmethod
    def empty_validation_token(cls, value: object) -> object:
        """Normalize an empty banking or legacy agent token to an unconfigured value."""
        return None if value == "" else value


@lru_cache
def get_agent_settings() -> AgentSettings:
    """Return the cached agent identities, limits and banking configuration.

    Returns:
        Cached agent capabilities, limits and named principal configuration.

    Raises:
        ValidationError: Agent settings contain invalid or ambiguous identities and scopes.
    """
    return AgentSettings()
