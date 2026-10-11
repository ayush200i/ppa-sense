import re
from collections import defaultdict

from app.schemas.answer import NOT_FOUND, AnswerResponse, Citation, DraftAnswer, Evidence
from app.schemas.retrieval import SearchHit


class CitationValidationError(ValueError):
    pass


class EvidenceUnavailableError(ValueError):
    pass


def normalize(text: str) -> str:
    return " ".join(text.split())


def prepare_evidence(hits: list[SearchHit], max_chars: int = 40000) -> list[Evidence]:
    sources: list[Evidence] = []
    seen: set[tuple[str, int, str, str]] = set()
    used = 0
    for hit in hits:
        chunk = hit.chunk
        pages: dict[int, list[str]] = defaultdict(list)
        if chunk.page_spans:
            for span in chunk.page_spans:
                pages[span.page].append(chunk.text[span.start : span.end])
        elif chunk.page_start == chunk.page_end:
            pages[chunk.page_start].append(chunk.text)
        else:
            raise EvidenceUnavailableError(
                "Re-ingest this PDF to add exact page offsets for citation checking"
            )
        for page, pieces in pages.items():
            text = "\n".join(pieces)
            key = (chunk.doc_id, page, chunk.section_title or "Unlabelled section", text)
            if key in seen:
                continue
            if used + len(text) > max_chars:
                continue
            seen.add(key)
            used += len(text)
            sources.append(
                Evidence(
                    source_id=f"S{len(sources) + 1}",
                    doc_id=key[0],
                    page=page,
                    section=key[2],
                    text=text,
                )
            )
    if hits and not sources:
        raise EvidenceUnavailableError("Retrieved text exceeds the evidence budget")
    return sources


def verify_and_render(draft: DraftAnswer, sources: list[Evidence]) -> AnswerResponse:
    if not draft.found:
        return AnswerResponse(answer=NOT_FOUND, citations=[])
    lookup = {source.source_id: source for source in sources}
    citations: list[Citation] = []
    paragraphs: list[str] = []
    for claim in draft.claims:
        # The server owns citation formatting; model-written labels are not trusted.
        if "[" in claim.text or "]" in claim.text:
            raise CitationValidationError("Model supplied its own citation labels")
        labels: list[str] = []
        for reference in claim.evidence:
            source = lookup.get(reference.source_id)
            if source is None:
                raise CitationValidationError("Unknown source ID")
            quote = normalize(reference.quote)
            if not quote or quote not in normalize(source.text):
                raise CitationValidationError("Quote is not present on the cited page")
            citation = Citation(
                doc_id=source.doc_id,
                page=source.page,
                section=source.section,
                quote=reference.quote,
            )
            if citation not in citations:
                citations.append(citation)
            section = re.sub(r"[\[\]\r\n]", " ", source.section)
            label = f"[{source.doc_id}, page {source.page}, section {section}]"
            if label not in labels:
                labels.append(label)
        paragraphs.append(f"{claim.text} {' '.join(labels)}")
    return AnswerResponse(answer="\n\n".join(paragraphs), citations=citations)
