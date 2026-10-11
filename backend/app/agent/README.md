# Step 4: answer questions with checked sources

This step uses Anthropic as requested in Step 4, replacing the earlier planned Gemini
provider. Embeddings still run locally. A live answer needs an Anthropic API key and
an account-accessible Claude model that supports tool use. No model name is hard-coded.

## Setup and use

Install the updated dependencies from the project root:

```powershell
.\.venv\Scripts\python.exe -m pip install -e "./backend[dev]"
```

Set these entries in your root `.env` (copy `.env.example` only if you do not already
have a `.env`). Keep the key out of source control and never put it in frontend code:

```dotenv
LLM_PROVIDER=anthropic
LLM_MODEL=your_account_accessible_claude_model
ANTHROPIC_API_KEY=your_key
LLM_MAX_TOKENS=2000
LLM_TIMEOUT_SECONDS=60
```

Re-ingest PDFs indexed before Step 4. The chunk IDs remain stable; the new page-offset
metadata is updated without duplicates. Old single-page chunks remain usable. Old
multi-page chunks are rejected because their exact quote page cannot be established.

```powershell
.\.venv\Scripts\python.exe -m app.retrieval ingest "C:\path\contract.pdf"
.\.venv\Scripts\python.exe -m app.agent "What is the tariff?" --doc-id "ID_FROM_INGEST" --top-k 5
```

Add `--rerank` to retrieve three times as many candidates, then keep the best top-k
after a simple rerank combining cosine similarity with keyword overlap. This is a
cheap local heuristic, not a trained reranker, and is disabled by default. Evaluate
it before assuming it improves answers. Omit doc_id only for intentional cross-document search.

HTTP is available through `POST /api/answer` after starting Uvicorn as described in
the root README. The interactive API page at `/docs` accepts this request:

```json
{"question": "What is the tariff?", "doc_id": "ID_FROM_INGEST", "top_k": 5, "rerank": false}
```

Illustrative response (not a live API result):

```json
{
  "answer": "The tariff is USD 45 per MWh. [contract-A, page 4, section 1. Tariff]",
  "citations": [{
    "doc_id": "contract-A",
    "page": 4,
    "section": "1. Tariff",
    "quote": "The tariff is USD 45 per MWh."
  }]
}
```

The requested citation fields are present. doc_id is also returned so citations
remain unambiguous when searching multiple documents. Missing evidence returns:

```json
{"answer": "not found in the contract", "citations": []}
```

## What the pipeline does

LangGraph connects these steps:

1. Retrieve top-k chunks, with an optional document filter and rerank.
2. Split the retrieved text into page-specific evidence records with short source IDs.
3. Send the question and evidence to Anthropic. Require structured claims and quotes
   using a forced `submit_answer` tool. The tool is an output format, not code execution.
4. Validate the response with Pydantic. Check every source ID and quote.
5. Build the answer and citation labels from stored metadata.

No retrieved chunks means no model call. Evidence is capped at 40,000 characters
without slicing an individual page excerpt in half. This is a character budget, not
an exact provider token count. The model response is separately capped by LLM_MAX_TOKENS.
No conversation memory or contract-processing tools are exposed to the answer model.

Files are separated by job: `pipeline.py` controls the sequence; `anthropic_client.py`
owns the prompt and API request; `evidence.py` checks sources and formats citations;
Pydantic models live in `schemas/answer.py`; the HTTP wrapper lives in `api/answer.py`.

## How we reduce invented answers

The prompt says: use only supplied passages, keep units and exceptions, report
conflicting clauses, and do not guess. It treats instructions inside a contract as
document text, not commands. If the answer is missing, the model must abstain.

The model chooses source IDs and exact quotes, but cannot choose the returned page
number or document label. Our code verifies that the quoted text occurs in the
selected page excerpt (allowing whitespace differences only), then attaches that
excerpt's stored page, section, and document ID. Each claim needs at least one quote.
Invalid citations reject the whole answer; unsupported text is not partly released.

A prompt cannot guarantee truth. A real quote can still be misunderstood, omit an
exception, or fail to support the claim. This version checks source existence and
quote/page matching, not full logical support. Human-reviewed contract questions,
claim-support evaluation, and retrieval-recall tests are needed to measure quality.
The phrase "not found in the contract" means not found in the retrieved evidence;
it is not proof that the clause is absent everywhere in a 100-page document.

## Errors are different from missing evidence

HTTP 422: invalid question/settings in the request.
HTTP 503: answer service is not configured or cannot initialize.
HTTP 409: evidence cannot be used safely, such as old multi-page chunks needing re-ingestion.
HTTP 502: provider failure, truncated/refused response, malformed output, or invalid citations.
These failures never become a false "not found" answer. Provider exception bodies and
keys are not returned to the HTTP client. The SDK has a timeout and at most one retry.
The health route still works without a key. The answer service initializes on its first request.

## Why not paste the whole PDF?

Retrieval sends fewer passages, reducing repeated input and distracting text. It also
makes source checks manageable. Full-document input can be useful when global context
matters and the model can fit it, but repeats much more text for every question.
Retrieval can miss definitions, exceptions, or referenced schedules. The next quality
improvement should measure those misses and retrieve linked clauses when necessary.

## Tests and scale

Run `python -m pytest backend/tests -q` using the project virtual environment.
Tests cover the LangGraph path, empty evidence without a model call, abstention,
document isolation, reranking, page-offset persistence, forged source IDs, wrong-page
quotes, fabricated quotes, malformed/truncated responses, and HTTP behavior. Model
responses are controlled in these tests: they do not measure live Anthropic quality.

At scale, synchronous CPU search and provider calls occupy request workers. Add a
request queue, concurrency limits, caching, per-user document access checks, and
provider usage monitoring before exposing the service widely. Retrieval still has
the single-machine limits documented in Step 3. Page offsets increase metadata size.
Source offsets refer to extracted physical PDF pages, not printed page labels, and
OCR/layout mistakes can still corrupt the extracted evidence.

## Interview answers

1. Does the prompt prevent hallucination?
   No. It reduces guessing by limiting answers to supplied evidence and allowing
   abstention. Code checks quotes and source metadata, but meaning still needs evaluation.
2. How do you know a citation is real?
   Resolve its source ID to retrieved text, match the quote on that exact page, and
   build the citation label from stored metadata rather than model-generated labels.
3. Why retrieval instead of the whole contract?
   It uses less input per question and focuses the model on likely evidence. The
   tradeoff is that retrieval can miss important clauses, so evidence recall must be tested.

References:
- [Anthropic tool use](https://platform.claude.com/docs/claude/docs/tool-use)
- [Anthropic Python SDK](https://platform.claude.com/docs/en/api/sdks/python)
