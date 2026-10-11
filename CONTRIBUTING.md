# Contributing

Use Python 3.12 and Node 22.12 or newer. Follow the setup in [README.md](README.md).
Create a feature branch and keep each change focused. Use typed Pydantic models at
backend boundaries and keep parsing, retrieval, model calls, and HTTP handling separate.

Before opening a pull request, run:

```sh
python -m pip install -e './backend[dev]'
python -m ruff check backend
python -m pytest backend/tests -q
cd frontend
npm ci
npm run format:check
npm run build
npx playwright install chromium
npm test
```

Activate your virtual environment first. On Windows, installed Edge can be used with
`$env:PLAYWRIGHT_CHANNEL='msedge'` before `npm test`. The default backend suite uses
test doubles and needs no provider credentials; the real embedding test is opt-in.

Use synthetic contracts in tests. Never attach private PDFs, credentials, database
files, or logs containing contract text. Describe the behavior changed and the checks
run. Update documentation for user-visible behavior or configuration changes.

Evaluation changes must retain source provenance, physical page numbers, and honest
denominators. Do not overwrite the baseline report or treat missing model results as
successful evaluations. Label new results with their configuration and limitations.
