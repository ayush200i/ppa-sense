from datetime import date
from typing import Annotated, Any, Generic, Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.answer import Citation, EvidenceReference

T = TypeVar("T")
ContractType = Literal["PPA", "EPC"]
Text = Annotated[str, Field(min_length=1, max_length=6000)]
PositiveYears = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
FieldName = Literal[
    "contract_type",
    "parties",
    "effective_date",
    "term_years",
    "COD",
    "price_tariff",
    "escalation",
    "delivery_point",
    "guaranteed_output",
    "termination_rights",
    "liquidated_damages",
    "change_in_law",
    "force_majeure",
    "payment_terms",
]


class TermsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Party(TermsModel):
    name: Text
    role: Text


class CODValue(TermsModel):
    scheduled_date: date | None
    actual_date: date | None
    condition: Text


class PriceValue(TermsModel):
    kind: Literal["tariff", "contract_price", "formula", "other"]
    amount: Annotated[float, Field(ge=0)] | None
    currency: Text | None
    unit: Text | None
    basis: Text


class FieldDraft(TermsModel, Generic[T]):
    value: T | None
    evidence: list[EvidenceReference] = Field(max_length=8)
    confidence: Confidence
    reason: str = Field(max_length=1000)

    @model_validator(mode="after")
    def consistent_evidence(self) -> Self:
        if self.value is None:
            if self.evidence or self.confidence != 0:
                raise ValueError("Missing values require empty evidence and confidence 0")
        elif not self.evidence:
            raise ValueError("A value requires supporting evidence")
        return self


class SourcedField(TermsModel, Generic[T]):
    value: T | None
    sources: list[Citation]
    confidence: Confidence
    status: Literal["validated", "low_confidence", "not_found", "invalid_evidence", "error"]
    reason: str
    attempts: int = Field(ge=1)

    @model_validator(mode="after")
    def consistent_result(self) -> Self:
        if self.value is None:
            if self.sources or self.confidence != 0 or self.status == "validated":
                raise ValueError("Missing values cannot have validated evidence or confidence")
        elif not self.sources or self.status not in {"validated", "low_confidence"}:
            raise ValueError("Only evidence-backed results can contain a value")
        return self


class CommercialTerms(TermsModel):
    contract_type: SourcedField[ContractType]
    parties: SourcedField[Annotated[list[Party], Field(min_length=1)]]
    effective_date: SourcedField[date]
    term_years: SourcedField[PositiveYears]
    COD: SourcedField[CODValue]
    price_tariff: SourcedField[PriceValue]
    escalation: SourcedField[Text]
    delivery_point: SourcedField[Text]
    guaranteed_output: SourcedField[Text]
    termination_rights: SourcedField[Text]
    liquidated_damages: SourcedField[Text]
    change_in_law: SourcedField[Text]
    force_majeure: SourcedField[Text]
    payment_terms: SourcedField[Text]


FIELD_TYPES: dict[FieldName, Any] = {
    "contract_type": ContractType,
    "parties": Annotated[list[Party], Field(min_length=1)],
    "effective_date": date,
    "term_years": PositiveYears,
    "COD": CODValue,
    "price_tariff": PriceValue,
    **{
        name: Text
        for name in (
            "escalation",
            "delivery_point",
            "guaranteed_output",
            "termination_rights",
            "liquidated_damages",
            "change_in_law",
            "force_majeure",
            "payment_terms",
        )
    },
}


class ExtractionRequest(TermsModel):
    doc_id: str = Field(min_length=1, max_length=200)
    top_k: int = Field(default=4, ge=1, le=10)
    max_retries: int = Field(default=1, ge=0, le=2)
    confidence_threshold: float = Field(default=0.75, ge=0.5, le=0.95)


class ExtractionResponse(TermsModel):
    doc_id: str
    terms: CommercialTerms
    needs_review: list[FieldName]
    warnings: list[str]
