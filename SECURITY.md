# Security and data handling

PPA-Sense is a local learning prototype. It has no user authentication or tenant
authorization. Do not expose it directly to the public internet. Docker Compose binds
published ports to the local machine.

Uploaded PDFs, extracted text, vectors, and saved results are kept under local data
directories or Docker volumes. Answering and extraction send selected contract text
to the configured model provider. Use public or authorized documents only.

Keep API keys in a local `.env` or deployment secret store. Never place credentials
in `VITE_` variables; those values become browser code. Do not commit uploads, model
caches, Chroma databases, or logs. Deleting a Git-tracked secret is insufficient;
revoke it at its provider and remove it from history if one is accidentally published.

For a potential vulnerability, use this repository's private vulnerability reporting
feature when available. Otherwise contact the maintainer through a contact method on
their GitHub profile. Do not post secrets or exploitable private data in public issues.

No production security support or response-time guarantee is provided.
