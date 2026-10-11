import re
from dataclasses import dataclass
from hashlib import sha256
from math import ceil

from app.schemas.ingestion import Chunk, ChunkingConfig, ChunkPageSpan, ExtractedDocument

_NUMBERED = re.compile(
    r"^(?:(?:ARTICLE|SECTION|SCHEDULE|ANNEX)\s+[\w.-]+[.:]?|\d+(?:\.\d+)*[.)]?)\s+(.+)$",
    re.I,
)


def is_heading(line: str) -> bool:
    text = line.strip()
    if not text or len(text) > 120 or len(text.split()) > 14:
        return False
    if re.search(r"\.{3,}\s*\d+$", text) or text.endswith((".", ";", ",")):
        return False
    match = _NUMBERED.match(text)
    title = match.group(1) if match else text
    words = [word for word in title.split() if any(c.isalpha() for c in word)]
    if not words:
        return False
    # Numbering alone also occurs in body clauses; require title-like capitalization.
    title_case = all(
        w[0].isupper() or w.lower() in {"and", "of", "the", "in", "for", "to"} for w in words
    )
    return title.isupper() or (title_case and (match is not None or len(words) <= 8))


def estimate_tokens(text: str) -> int:
    """Conservative word/character estimate, not a Gemini tokenizer."""
    return sum(max(1, ceil(len(word) / 4)) for word in text.split())


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    page: int


def chunk_document(
    document: ExtractedDocument, config: ChunkingConfig | None = None
) -> list[Chunk]:
    settings = config or ChunkingConfig()
    numbers = [page.page_number for page in document.pages]
    if numbers != sorted(set(numbers)):
        raise ValueError("Pages must have unique, increasing page numbers")
    chunks: list[Chunk] = []
    title: str | None = None
    lines: list[tuple[str, int]] = []

    def flush() -> None:
        if lines:
            chunks.extend(_split_section(document.doc_id, title, lines, settings, len(chunks)))
            lines.clear()

    for page in document.pages:
        for line in page.text.splitlines():
            if is_heading(line):
                flush()
                title = line.strip()
            lines.append((line, page.page_number))
    flush()
    return chunks


def _split_section(
    doc_id: str,
    title: str | None,
    lines: list[tuple[str, int]],
    config: ChunkingConfig,
    offset: int,
) -> list[Chunk]:
    text = ""
    spans: list[_Span] = []
    for line, page in lines:
        start = len(text)
        text += line + "\n"
        if line.strip():
            spans.append(_Span(start, start + len(line), page))

    # Keep source offsets through splitting so overlap retains its original pages.
    atoms: list[tuple[int, int, int]] = []
    for match in re.finditer(r"\S+", text):
        for start in range(match.start(), match.end(), config.target_tokens * 4):
            end = min(match.end(), start + config.target_tokens * 4)
            atoms.append((start, end, ceil((end - start) / 4)))
    result: list[Chunk] = []
    start_index = 0
    while start_index < len(atoms):
        end_index = start_index
        budget = 0
        preferred_end: int | None = None
        while end_index < len(atoms) and budget + atoms[end_index][2] <= config.target_tokens:
            budget += atoms[end_index][2]
            end_index += 1
            end = atoms[end_index - 1][1]
            next_start = atoms[end_index][0] if end_index < len(atoms) else len(text)
            if budget >= config.target_tokens // 2 and (
                text[end - 1] in ".;!?" or "\n\n" in text[end:next_start]
            ):
                preferred_end = end_index
        if end_index < len(atoms) and preferred_end is not None:
            end_index = preferred_end
        start, end = atoms[start_index][0], atoms[end_index - 1][1]
        content = text[start:end]
        pages = [span.page for span in spans if span.start < end and span.end > start]
        identity = f"v1|{doc_id}|{offset + len(result)}|{pages[0]}|{pages[-1]}|{title}|{content}"
        result.append(
            Chunk(
                chunk_id=sha256(identity.encode()).hexdigest(),
                doc_id=doc_id,
                page_start=pages[0],
                page_end=pages[-1],
                section_title=title,
                text=content,
                page_spans=[
                    ChunkPageSpan(
                        page=span.page,
                        start=max(span.start, start) - start,
                        end=min(span.end, end) - start,
                    )
                    for span in spans
                    if span.start < end and span.end > start
                ],
            )
        )
        if end_index == len(atoms):
            break
        next_index = end_index
        overlap = 0
        while next_index > start_index + 1:
            cost = atoms[next_index - 1][2]
            if overlap + cost > config.overlap_tokens:
                break
            overlap += cost
            next_index -= 1
        start_index = next_index
    return result
