from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Document(BaseModel):
    doc_id: str
    filename: str
    size_bytes: int
    status: Literal["queued", "processing", "ready", "failed"]
    created_at: datetime
    chunks_stored: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class DocumentList(BaseModel):
    documents: list[Document]
    limit: int
    offset: int


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)
    rerank: bool = False


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top_k: int = Field(default=4, ge=1, le=10)
    max_retries: int = Field(default=1, ge=0, le=2)
    confidence_threshold: float = Field(default=0.75, ge=0.5, le=0.95)


class ErrorResponse(BaseModel):
    detail: str
