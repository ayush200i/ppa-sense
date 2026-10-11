# PPA-Sense frontend

Minimal React + TypeScript UI, built with Vite. Use Node 22.12+.

## Run locally

Start FastAPI in a separate terminal from the project root:

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Then from `frontend`:

```powershell
npm ci
npm run dev
```

Open http://127.0.0.1:5173. The default API address is http://127.0.0.1:8000.
To change it, copy `.env.example` to `.env`, edit `VITE_API_BASE_URL`, and restart Vite.
The backend's `CORS_ORIGINS` must include the exact frontend origin (scheme, host, port).
Never put provider API keys in a `VITE_` variable: those values are public browser code.

Alternatively, run `docker compose up --build` from the project root. The frontend
container serves the production build on port 5173 and proxies `/backend/` to FastAPI.
The browser then uses the same origin for both the UI and API. Its proxy has a 51 MiB
request-body limit to accommodate the backend's default 50 MiB file limit plus multipart
overhead; adjust both if increasing upload limits. Extraction requests time out at the
proxy after 10 minutes; the server may still finish and save results. Reload to check.

## Use

1. Choose a PDF and upload it. Its ingestion status updates every two seconds while pending.
2. Once ready, select **Extract terms**. Previous saved terms load automatically.
3. Click a page citation in the table or an answer to open its source quote and PDF page.
4. Ask a complete question in the chat box. Requests use only the selected document.
5. Switch contracts with the dropdown. Load more when the catalog exceeds 20 documents.

The citation dialog is keyboard accessible; Escape or Close dismisses it and restores
focus. It shows the supplied quote, not a PDF viewer. Page numbers are physical PDF page
positions, which may differ from printed numbering. Contract text is rendered as plain
React text; raw HTML and model-provided links are not executed.

## Files and data flow

- `src/api.ts`: fetch requests, multipart upload, JSON errors, configurable backend URL.
- `src/types.ts`: TypeScript representations of backend response models.
- `src/App.tsx`: document list, uploads, active document, and status polling.
- `src/Workspace.tsx`: extraction, saved terms, and question/answer state.
- `src/components.tsx`: terms table, clickable citations, and source dialog.
- `src/styles.css`: responsive layout without a component library.

Upload uses `FormData` with `POST /documents`. After 202, polling uses
`GET /documents/{id}` until ready or failed. We stop polling on network errors and
offer retry. The UI then loads `GET /documents/{id}/terms` (404 means no saved terms).
Extraction sends `{}` to `POST /documents/{id}/extract`; questions send `{question}`
to `POST /documents/{id}/ask`. The resulting JSON updates React state and the screen.
Status and saved-term fetches are cancelled on document change. Each document workspace
has a different React key, preventing old chat and extraction state from appearing under
a new document. Switching away does not cancel backend extraction work.

This chat is a visible list of independent questions; conversation history is not sent
to the model. Chat is cleared when switching documents or reloading. Saved extraction
results and uploaded documents persist on the backend. Missing evidence and low-confidence
fields remain visible. Model confidence is not a calibrated probability of correctness.

## Verify

```powershell
npm run build
npx playwright install chromium
npm test
```

You can use an installed Edge browser instead of downloading Chromium:
`$env:PLAYWRIGHT_CHANNEL='msedge'; npm test` in PowerShell.
Run `npm run format:check` to check code formatting.

Browser tests mock API responses and exercise upload/polling, extraction, citations,
Q&A, saved results, errors, switching documents, and mobile overflow. They do not call
a paid model. Backend integration tests are separate. Screenshots are written into
the ignored `test-results` folder.

## Limits

The UI relies on backend Pydantic validation; TypeScript types are compile-time checks,
not runtime JSON validation. Generate these types from OpenAPI if the API grows.
At scale, replace frequent polling with backoff or server notifications, paginate chat
history, and use a shared query cache. Long extraction requests should use background
jobs with progress. Authentication and per-user document authorization are still needed
for multiple users. The model provider remains the existing backend integration.

React documents why effect cleanup matters for [preventing stale responses](https://react.dev/reference/react/useEffect#fetching-data-with-effects).
