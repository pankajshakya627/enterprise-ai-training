from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2000)
    as_of: date
    top_k: int = Field(default=5, ge=1, le=20)
    product_id: str | None = Field(default=None, min_length=1)
    jurisdiction: str | None = Field(default=None, min_length=1)


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    title: str
    policy_version: str
    clause: str | None
    effective_date: date


class RetrieveHit(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    rank: int
    score: float
    text: str
    parent_text: str
    citation: Citation


class RetrieveTimings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    search_ms: float
    parent_ms: float
    total_ms: float


class RetrieveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date
    results: list[RetrieveHit]
    timings: RetrieveTimings
    amendments: list[RetrieveHit] = Field(default_factory=list)

    @property
    def evidence(self) -> list[RetrieveHit]:
        complete = {(hit.citation.doc_id, hit.citation.policy_version) for hit in self.amendments}
        return self.amendments + [
            hit
            for hit in self.results
            if (hit.citation.doc_id, hit.citation.policy_version) not in complete
        ]
