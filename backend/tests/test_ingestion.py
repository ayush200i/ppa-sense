from pathlib import Path

import pymupdf
import pytest
from pydantic import ValidationError

from app.ingestion import PDFIngestionError, chunk_document, extract_pdf
from app.ingestion.chunking import estimate_tokens, is_heading
from app.schemas.ingestion import ChunkingConfig, ExtractedDocument, PageText


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample_ppa.pdf"
    with pymupdf.open() as pdf:
        for text in [
            "1. Contract Price\nThe tariff is USD 45 per MWh.\nPayment is due within 30 days.",
            "The tariff increases by two percent annually.\n\n"
            "2. Termination Rights\nEither party must give 60 days notice.",
            "",
        ]:
            page = pdf.new_page()
            assert page.insert_textbox(pymupdf.Rect(50, 50, 550, 750), text, fontsize=11) >= 0
        pdf.save(path)
    return path


def test_pdf_sections_and_cross_page_metadata(sample_pdf: Path) -> None:
    document = extract_pdf(sample_pdf)
    assert [p.page_number for p in document.pages] == [1, 2, 3]
    assert "USD 45 per MWh" in document.pages[0].text
    assert document.pages[2].text == ""
    assert "Page 3" in document.warnings[0]
    chunks = chunk_document(document)
    assert len(chunks) == 2
    assert chunks[0].section_title == "1. Contract Price"
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 2)
    assert "two percent" in chunks[0].text
    assert chunks[1].section_title == "2. Termination Rights"
    assert (chunks[1].page_start, chunks[1].page_end) == (2, 2)
    assert "tariff" not in chunks[1].text
    assert chunks == chunk_document(extract_pdf(sample_pdf))


def test_long_pdf_fallback_overlap_and_page_provenance(tmp_path: Path) -> None:
    path = tmp_path / "long_contract.pdf"
    words_by_page = [[f"p{p}word{i}" for i in range(250)] for p in range(1, 6)]
    with pymupdf.open() as pdf:
        for words in words_by_page:
            page = pdf.new_page()
            assert page.insert_textbox(
                pymupdf.Rect(40, 40, 555, 800), " ".join(words), fontsize=10
            ) >= 0
        pdf.save(path)
    document = extract_pdf(path)
    chunks = chunk_document(document)
    assert len(chunks) > 2
    expected = {word for words in words_by_page for word in words}
    assert {word for chunk in chunks for word in chunk.text.split()} == expected
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    for chunk in chunks:
        pages = [int(word[1]) for word in chunk.text.split()]
        assert (chunk.page_start, chunk.page_end) == (min(pages), max(pages))
        assert estimate_tokens(chunk.text) <= 800
        assert chunk.section_title is None
    for left, right in zip(chunks, chunks[1:]):
        assert set(left.text.split()) & set(right.text.split())


def test_sentence_boundary_and_no_overlap_between_sections() -> None:
    first = " ".join(["payment"] * 23) + "."
    document = ExtractedDocument(doc_id="test", pages=[PageText(
        page_number=1, text=f"1. Tariff\n{first}\n\n{first}\n2. Termination\nNotice required."
    )])
    chunks = chunk_document(document, ChunkingConfig(target_tokens=64, overlap_tokens=8))
    assert chunks[0].text.endswith(".")
    assert "payment" not in chunks[-1].text
    assert all(estimate_tokens(c.text) <= 64 for c in chunks)


@pytest.mark.parametrize(
    "heading", ["ARTICLE IV PAYMENT", "4.2 Payment Terms", "FORCE MAJEURE"]
)
def test_heading_patterns(heading: str) -> None:
    assert is_heading(heading)


@pytest.mark.parametrize(
    "body", ["1. The buyer shall pay within thirty days.", "Tariff .... 12", "45"]
)
def test_body_is_not_heading(body: str) -> None:
    assert not is_heading(body)


def test_empty_and_oversized_word() -> None:
    assert chunk_document(ExtractedDocument(doc_id="empty", pages=[])) == []
    document = ExtractedDocument(doc_id="long", pages=[PageText(page_number=4, text="x" * 1000)])
    chunks = chunk_document(document, ChunkingConfig(target_tokens=32, overlap_tokens=0))
    assert "".join(c.text for c in chunks) == "x" * 1000
    assert all(c.page_start == c.page_end == 4 for c in chunks)


def test_reject_invalid_settings_and_page_order() -> None:
    with pytest.raises(ValidationError):
        ChunkingConfig(target_tokens=100, overlap_tokens=50)
    with pytest.raises(ValueError, match="increasing"):
        chunk_document(ExtractedDocument(doc_id="bad", pages=[
            PageText(page_number=2, text="body"), PageText(page_number=1, text="body")
        ]))


def test_bad_and_encrypted_pdf(tmp_path: Path, sample_pdf: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(PDFIngestionError):
        extract_pdf(bad)
    encrypted = tmp_path / "locked.pdf"
    with pymupdf.open(sample_pdf) as pdf:
        pdf.save(encrypted, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                 owner_pw="owner", user_pw="reader")
    with pytest.raises(PDFIngestionError, match="unencrypted"):
        extract_pdf(encrypted)
