# Step 2: page-aware ingestion

```python
from app.ingestion import extract_pdf, chunk_document
from app.schemas.ingestion import ChunkingConfig

document = extract_pdf("contract.pdf")
chunks = chunk_document(document, ChunkingConfig(target_tokens=800, overlap_tokens=100))
print(document.warnings)
print(chunks[0].model_dump())
```

`extract_pdf` uses PyMuPDF text blocks sorted in approximate reading order. Every page,
including empty pages, has its original one-based physical PDF number. These numbers
are not printed labels such as roman-numeral prefaces. Document IDs hash the PDF bytes.
Empty pages produce warnings: they may be blank or may need OCR. OCR is not implemented.
Unreadable and password-protected PDFs are rejected. Missing paths raise FileNotFoundError.

The chunker recognizes short uppercase, title-case, and numbered headings using
conservative heuristics. It preserves the heading in the section text and carries its
title into each resulting chunk. Text before the first heading has a null section title.
Short sections stay whole; different sections are never merged just to fill a budget.
Repeated headings, running headers, tables of contents and unusual typography can fool
the heuristic. Text is not silently removed to compensate. There is no hierarchy parser yet.

Long sections and unheaded content are packed up to approximately 800 tokens. Within
that limit, sentence endings and blank-line paragraph boundaries in the latter half
of the window are preferred. Otherwise, splitting occurs at a word boundary; an
exceptionally long unbroken word can be divided as a last resort. Overlap copies up
to 100 estimated tokens from the previous chunk and never crosses section boundaries.
The estimate sums ceil(word length / 4), with a minimum of one per word. It is not a
provider tokenizer and is intended for English contracts. Real model token counts vary.

Source offsets map every piece of chunk text, including overlap, back to its page.
page_start/page_end enclose all contributing physical pages. A blank page inside that
range can contribute no text. IDs are deterministic for the same PDF and chunking output,
and include a chunker version so future algorithm changes can be deliberately reindexed.
Page ranges identify candidate evidence; they do not establish that an answer is supported.

Why it matters: a price may be qualified by an exception in the following sentence.
Cutting at arbitrary positions can separate the rule from its exception. Larger chunks
retain context but mix topics and consume more model input. Smaller chunks improve
focus but can lose definitions and cross-references. Overlap protects local boundaries
at the cost of duplicated storage, repeated evidence, and additional embedding work.
Cross-section definitions still require retrieval of multiple chunks later.

Scale limits: extraction and chunks currently stay in memory; hashing and extraction
read the file twice. Use immutable input files. For high throughput, use background
jobs and bounded memory batches. Coordinate-sorted text can misorder multiple columns
and flatten tables. Mixed scanned/text pages may need OCR even when some text exists.
The future embedding model's maximum input length must be checked: some local models
truncate well before 800 tokens, so rechunk or choose an appropriate model before indexing.

Tests generate synthetic PDF files in pytest's temporary directory, including a
five-page unheaded contract. They check page spans, section boundaries, overlap, text
coverage, deterministic IDs, blank pages, malformed input and encrypted input. These
are regression fixtures, not evidence of accuracy on real contract layouts.

Interview questions:

1. Why keep page metadata during splitting instead of finding it afterward?
   Repeated language makes text matching ambiguous. Mapping source offsets during
   splitting preserves provenance even when overlapping chunks span page boundaries.
2. Why heading-aware splitting with a fallback?
   Headings provide useful commercial boundaries but PDFs lack reliable semantic
   structure. A bounded fallback handles long sections and missing headings.
3. How would you tune size and overlap?
   Measure evidence recall, answer correctness, citation accuracy, latency and token
   cost on representative contracts. Respect embedding input limits and compare
   several chunk/overlap sizes rather than assuming 800/100 is universally best.
