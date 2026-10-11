from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

NOT_FOUND = "not found in the contract"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AnswerRequest(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    doc_id: str | None = Field(default=None, min_length=1, max_length=200)
    top_k: int = Field(default=5, ge=1, le=10)
    rerank: bool = False


class Evidence(StrictModel):
    source_id: str
    doc_id: str
    page: int = Field(ge=1)
    section: str
    text: str


class EvidenceReference(StrictModel):
    source_id: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=2000)


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    evidence: list[EvidenceReference] = Field(min_length=1, max_length=5)


class DraftAnswer(StrictModel):
    found: bool
    claims: list[Claim] = Field(max_length=10)

    @model_validator(mode="after")
    def consistent_found(self) -> Self:
        if self.found != bool(self.claims):
            raise ValueError("Found answers need claims; missing answers must have none")
        return self


class Citation(StrictModel):
    doc_id: str
    page: int = Field(ge=1)
    section: str
    quote: str


class AnswerResponse(StrictModel):
    answer: str
    citations: list[Citation]
