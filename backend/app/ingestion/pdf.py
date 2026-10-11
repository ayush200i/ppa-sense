from hashlib import sha256
from pathlib import Path

import pymupdf

from app.schemas.ingestion import ExtractedDocument, PageText


class PDFIngestionError(ValueError):
    """The input cannot be read as an unencrypted PDF."""


def extract_pdf(path: str | Path) -> ExtractedDocument:
    source = Path(path)
    with source.open("rb") as stream:
        digest = sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)

    try:
        with pymupdf.open(source) as pdf:
            if not pdf.is_pdf or pdf.needs_pass:
                raise PDFIngestionError("Expected an unencrypted PDF")
            pages: list[PageText] = []
            warnings: list[str] = []
            for number, page in enumerate(pdf, start=1):
                blocks = page.get_text("blocks", sort=True)
                text = "\n\n".join(b[4].strip() for b in blocks if b[6] == 0 and b[4].strip())
                pages.append(PageText(page_number=number, text=text))
                if not text:
                    warnings.append(
                        f"Page {number} has no extractable text; inspect for OCR needs."
                    )
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise PDFIngestionError("Could not read PDF") from exc

    return ExtractedDocument(doc_id=digest.hexdigest(), pages=pages, warnings=warnings)
