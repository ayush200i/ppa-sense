from typing import Any

from pydantic import ValidationError

from app.agent.evidence import normalize
from app.schemas.answer import Citation, Evidence
from app.schemas.terms import FIELD_TYPES, FieldDraft, FieldName, SourcedField


def failed_field(status: str, reason: str, attempts: int) -> SourcedField[Any]:
    return SourcedField[Any](
        value=None, sources=[], confidence=0, status=status, reason=reason, attempts=attempts
    )


def validate_field(
    field: FieldName,
    payload: dict[str, Any],
    sources: list[Evidence],
    threshold: float,
    attempts: int,
) -> SourcedField[Any]:
    try:
        draft = FieldDraft[FIELD_TYPES[field]].model_validate(payload)
    except ValidationError:
        return failed_field("error", "Structured value does not match the field schema", attempts)
    if draft.value is None:
        return failed_field(
            "not_found", draft.reason or "Not found in retrieved evidence", attempts
        )
    lookup = {source.source_id: source for source in sources}
    citations: list[Citation] = []
    for reference in draft.evidence:
        source = lookup.get(reference.source_id)
        if source is None or normalize(reference.quote) not in normalize(source.text):
            return failed_field(
                "invalid_evidence", "Quote or source ID failed exact-page checking", attempts
            )
        citation = Citation(
            doc_id=source.doc_id, page=source.page, section=source.section, quote=reference.quote
        )
        if citation not in citations:
            citations.append(citation)
    status = "validated" if draft.confidence >= threshold else "low_confidence"
    return SourcedField[Any](
        value=draft.value,
        sources=citations,
        confidence=draft.confidence,
        status=status,
        reason=draft.reason,
        attempts=attempts,
    )
