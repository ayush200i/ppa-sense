from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.answer import AnswerResponse
from app.schemas.terms import FIELD_TYPES, ExtractionResponse, FieldName


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Question(StrictModel):
    id: str
    question: str
    answer: str
    pages: list[int] = Field(min_length=1)
    section: str
    answerable: bool = True
    text_available: bool = True
    notes: str = ""


class FieldGold(StrictModel):
    mode: Literal["exact", "manual"]
    expected: Any = None
    pages: list[int] = Field(min_length=1)
    rubric: str


class Dataset(StrictModel):
    version: str
    title: str
    source_url: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_page_count: int = Field(ge=100)
    page_start: int = Field(ge=1)
    page_end: int
    annotation_status: str
    notes: list[str]
    questions: list[Question] = Field(min_length=15, max_length=15)
    fields: dict[FieldName, FieldGold]

    @model_validator(mode="after")
    def valid_scope(self) -> Self:
        if not self.page_start + 99 <= self.page_end <= self.source_page_count:
            raise ValueError("Evaluation scope must contain at least 100 original PDF pages")
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("Question IDs must be unique")
        if set(self.fields) != set(FIELD_TYPES):
            raise ValueError("Provide a rubric for every extraction field")
        for item in [*self.questions, *self.fields.values()]:
            if any(not self.page_start <= p <= self.page_end for p in item.pages):
                raise ValueError("Gold pages must be within the PPA scope")
        return self


class QuestionRun(StrictModel):
    id: str
    retrieved_pages: list[int]
    retrieved_chunk_ids: list[str]
    hit: bool | None
    answer: AnswerResponse | None = None
    error: str | None = None


class Run(StrictModel):
    dataset_sha256: str
    source_sha256: str
    created_at: str
    settings: dict[str, Any]
    questions: list[QuestionRun]
    extraction: ExtractionResponse | None = None
    extraction_error: str | None = None


class Review(StrictModel):
    run_sha256: str
    reviewer: str = ""
    answers: dict[str, bool | None]
    citation_support: dict[str, list[bool | None]]
    fields: dict[FieldName, bool | None]


class Metric(StrictModel):
    name: str
    correct: int = 0
    total: int = 0
    note: str = ""

    @property
    def score(self) -> str:
        return (
            f"{100 * self.correct / self.total:.1f}% ({self.correct}/{self.total})"
            if self.total
            else "N/A"
        )
