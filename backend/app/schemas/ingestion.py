from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PageText(BaseModel):
    model_config = ConfigDict(frozen=True)

    page_number: int = Field(ge=1)
    text: str


class ExtractedDocument(BaseModel):
    doc_id: str = Field(min_length=1)
    pages: list[PageText]
    warnings: list[str] = Field(default_factory=list)


class ChunkPageSpan(BaseModel):
    page: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(ge=1)


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    section_title: str | None = None
    text: str = Field(min_length=1)
    page_spans: list[ChunkPageSpan] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered_pages(self) -> Self:
        if self.page_end < self.page_start:
            raise ValueError("page_end must be >= page_start")
        previous_end = 0
        for span in self.page_spans:
            if not (
                self.page_start <= span.page <= self.page_end
                and previous_end <= span.start < span.end <= len(self.text)
            ):
                raise ValueError("Invalid page span")
            previous_end = span.end
        return self


class ChunkingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_tokens: int = Field(default=800, ge=16)
    overlap_tokens: int = Field(default=100, ge=0)

    @model_validator(mode="after")
    def bounded_overlap(self) -> Self:
        if self.overlap_tokens >= self.target_tokens // 2:
            raise ValueError("overlap_tokens must be less than half target_tokens")
        return self
