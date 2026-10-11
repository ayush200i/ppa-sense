import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, errorMessage } from "./api";
import type { ContractDocument } from "./types";
import Workspace from "./Workspace";

export default function App() {
  const [documents, setDocuments] = useState<ContractDocument[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [listing, setListing] = useState(true);
  const [offset, setOffset] = useState(0);
  const [more, setMore] = useState(false);
  const [listError, setListError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [statusError, setStatusError] = useState("");
  const [statusRetry, setStatusRetry] = useState(0);
  const selected = documents.find((document) => document.doc_id === selectedId);

  const mergeDocuments = useCallback((incoming: ContractDocument[]) => {
    setDocuments((previous) => {
      const merged = new Map(
        previous.map((document) => [document.doc_id, document]),
      );
      incoming.forEach((document) => merged.set(document.doc_id, document));
      return [...merged.values()];
    });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    api
      .list(0, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        mergeDocuments(result.documents);
        setOffset(result.documents.length);
        setMore(result.documents.length === result.limit);
        setSelectedId(
          (current) => current || result.documents[0]?.doc_id || "",
        );
      })
      .catch((error) => {
        if (!controller.signal.aborted) setListError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setListing(false);
      });
    return () => controller.abort();
  }, [mergeDocuments]);

  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setStatusError("");
    async function poll() {
      try {
        const document = await api.document(selectedId, controller.signal);
        if (controller.signal.aborted) return;
        mergeDocuments([document]);
        if (document.status === "queued" || document.status === "processing") {
          timer = setTimeout(poll, 2000);
        }
      } catch (error) {
        if (!controller.signal.aborted) setStatusError(errorMessage(error));
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [selectedId, statusRetry, mergeDocuments]);

  async function loadMore() {
    setListing(true);
    setListError("");
    try {
      const result = await api.list(offset);
      mergeDocuments(result.documents);
      setOffset(offset + result.documents.length);
      setMore(result.documents.length === result.limit);
      setSelectedId((current) => current || result.documents[0]?.doc_id || "");
    } catch (error) {
      setListError(errorMessage(error));
    } finally {
      setListing(false);
    }
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || uploading) return;
    const form = event.currentTarget;
    setUploading(true);
    setUploadError("");
    try {
      const document = await api.upload(file);
      mergeDocuments([document]);
      setSelectedId(document.doc_id);
      setStatusRetry((value) => value + 1);
      setFile(null);
      form.reset();
    } catch (error) {
      setUploadError(errorMessage(error));
    } finally {
      setUploading(false);
    }
  }

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            P
          </span>
          <span>
            PPA<span className="brand-light">-Sense</span>
          </span>
        </div>
        <span className="topbar-note">Contract intelligence</span>
      </header>
      <main>
        <div className="intro">
          <p className="eyebrow">Renewable energy / Contract workspace</p>
          <h1>Clarity in every clause.</h1>
          <p>
            Explore your PPA or EPC contract. Find the terms. Follow the
            evidence.
          </p>
        </div>
        <section
          className="panel document-panel"
          aria-labelledby="documents-title"
        >
          <div>
            <p className="eyebrow">Your documents</p>
            <h2 id="documents-title">Start with a contract</h2>
            <p className="muted">Upload a PDF with selectable text.</p>
          </div>
          <form className="upload-form" onSubmit={upload}>
            <label htmlFor="contract-file">Contract PDF</label>
            <div className="upload-controls">
              <input
                id="contract-file"
                type="file"
                accept=".pdf,application/pdf"
                disabled={uploading || listing}
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                required
              />
              <button type="submit" disabled={!file || uploading || listing}>
                {uploading ? "Uploading…" : "Upload contract"}
              </button>
            </div>
            {uploadError && (
              <p role="alert" className="error">
                {uploadError}
              </p>
            )}
          </form>
          <div className="document-picker">
            <label htmlFor="document-select">Active contract</label>
            <select
              id="document-select"
              value={selectedId}
              onChange={(event) => setSelectedId(event.target.value)}
              disabled={!documents.length}
            >
              {!documents.length && (
                <option value="">
                  {listing ? "Loading documents…" : "No contracts uploaded yet"}
                </option>
              )}
              {documents.map((document) => (
                <option key={document.doc_id} value={document.doc_id}>
                  {document.filename} · {document.status} ·{" "}
                  {document.doc_id.slice(0, 8)}
                </option>
              ))}
            </select>
            {(more || listError) && (
              <button
                className="secondary"
                disabled={listing}
                onClick={loadMore}
              >
                {listing
                  ? "Loading…"
                  : listError
                    ? "Retry document list"
                    : "Load more documents"}
              </button>
            )}
            {listError && (
              <p role="alert" className="error">
                {listError}
              </p>
            )}
          </div>
        </section>
        {selected ? (
          <>
            <div className="document-heading">
              <div>
                <h2 className="filename">{selected.filename}</h2>
                <p className="muted">
                  {(selected.size_bytes / 1024 / 1024).toFixed(2)} MB ·{" "}
                  {selected.chunks_stored} indexed passages
                </p>
              </div>
              <span className={`badge ${selected.status}`} role="status">
                {selected.status}
              </span>
            </div>
            {statusError && (
              <div className="error" role="alert">
                {statusError}{" "}
                <button
                  className="secondary"
                  onClick={() => setStatusRetry((value) => value + 1)}
                >
                  Retry status check
                </button>
              </div>
            )}
            {selected.warnings.map((warning, index) => (
              <p className="notice" key={index}>
                {warning}
              </p>
            ))}
            {selected.status === "ready" ? (
              <Workspace key={selected.doc_id} document={selected} />
            ) : (
              <section className="panel empty" role="status">
                <h2>
                  {selected.status === "failed"
                    ? "This contract could not be processed"
                    : "Preparing your contract…"}
                </h2>
                <p>
                  {selected.status === "failed"
                    ? selected.error
                    : "Extracting text and building the search index. This page updates automatically."}
                </p>
                {selected.status === "failed" && (
                  <p>
                    Upload the PDF again to retry. Scanned PDFs need OCR before
                    uploading.
                  </p>
                )}
              </section>
            )}
          </>
        ) : (
          <section className="panel empty">
            <span className="empty-symbol" aria-hidden="true">
              ↥
            </span>
            <h2>One contract. A clearer picture.</h2>
            <p>
              Upload a document to explore its commercial terms and ask
              source-cited questions.
            </p>
          </section>
        )}
        <footer>PPA-Sense · Every answer starts with the contract.</footer>
      </main>
    </>
  );
}
