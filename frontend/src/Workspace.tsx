import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, errorMessage } from "./api";
import { AnswerText, CitationDialog, Sources, TermsTable } from "./components";
import type { Answer, Citation, ContractDocument, Extraction } from "./types";

export default function Workspace({
  document,
}: {
  document: ContractDocument;
}) {
  const [terms, setTerms] = useState<Extraction | null>(null);
  const [loadingTerms, setLoadingTerms] = useState(true);
  const [extracting, setExtracting] = useState(false);
  const [termsError, setTermsError] = useState("");
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [chatError, setChatError] = useState("");
  const [messages, setMessages] = useState<
    { question: string; answer: Answer }[]
  >([]);
  const [source, setSource] = useState<Citation | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api
      .terms(document.doc_id, controller.signal)
      .then(setTerms)
      .catch((error) => {
        if (
          !controller.signal.aborted &&
          !(error instanceof ApiError && error.status === 404)
        ) {
          setTermsError(errorMessage(error));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingTerms(false);
      });
    return () => controller.abort();
  }, [document.doc_id]);

  async function extract() {
    setExtracting(true);
    setTermsError("");
    try {
      setTerms(await api.extract(document.doc_id));
    } catch (error) {
      setTermsError(errorMessage(error));
    } finally {
      setExtracting(false);
    }
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    const submitted = question.trim();
    if (!submitted || asking) return;
    setAsking(true);
    setChatError("");
    try {
      const answer = await api.ask(document.doc_id, submitted);
      setMessages((previous) => [...previous, { question: submitted, answer }]);
      setQuestion("");
    } catch (error) {
      setChatError(errorMessage(error));
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="workspace-grid">
      <section className="panel terms-panel" aria-labelledby="terms-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">01 / Understand</p>
            <h2 id="terms-title">Commercial terms</h2>
          </div>
          <button disabled={loadingTerms || extracting} onClick={extract}>
            {extracting
              ? "Extracting…"
              : terms
                ? "Extract again"
                : "Extract terms"}
          </button>
        </div>
        <p className="muted">
          Key obligations, with the contract passages behind them.
        </p>
        {termsError && (
          <p className="error" role="alert">
            {termsError}
          </p>
        )}
        <div role="status">
          {extracting && (
            <p className="notice">
              Reading clauses and checking evidence. This can take a few
              minutes.
            </p>
          )}
        </div>
        {loadingTerms ? (
          <p role="status">Loading saved terms…</p>
        ) : terms ? (
          <TermsTable result={terms} onSelect={setSource} />
        ) : (
          <div className="empty">
            <span className="empty-symbol" aria-hidden="true">
              ≡
            </span>
            <h3>Your contract, at a glance</h3>
            <p>
              Extract 14 commercial terms, then open any page citation to read
              its source quote.
            </p>
          </div>
        )}
      </section>

      <section className="panel chat-panel" aria-labelledby="chat-title">
        <p className="eyebrow">02 / Ask</p>
        <h2 id="chat-title">Ask the contract</h2>
        <p className="muted">
          Answers use this document. Ask each question in full.
        </p>
        <div
          className="messages"
          role="log"
          aria-label="Contract questions and answers"
          aria-live="polite"
        >
          {messages.length === 0 && (
            <div className="chat-empty">
              <h3>Start with a clause.</h3>
              <p>
                What is the tariff?
                <br />
                When can either party terminate?
                <br />
                What happens if COD is delayed?
              </p>
            </div>
          )}
          {messages.map((message, index) => (
            <article className="exchange" key={index}>
              <p className="question">
                <span className="speaker">You</span>
                {message.question}
              </p>
              <div className="answer">
                <span className="speaker">PPA-Sense</span>
                <AnswerText answer={message.answer} onSelect={setSource} />
                <Sources
                  sources={message.answer.citations}
                  onSelect={setSource}
                />
              </div>
            </article>
          ))}
        </div>
        {asking && (
          <p role="status" className="notice">
            Finding supporting passages…
          </p>
        )}
        {chatError && (
          <p role="alert" className="error">
            {chatError}
          </p>
        )}
        <form onSubmit={ask} className="question-form">
          <label htmlFor="question">Your question</label>
          <textarea
            id="question"
            placeholder="What are the payment terms?"
            rows={3}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            disabled={asking}
            maxLength={2000}
            required
          />
          <div className="form-footer">
            <small>{question.length}/2000</small>
            <button disabled={asking || !question.trim()} type="submit">
              {asking ? "Asking…" : "Ask question"}{" "}
              <span aria-hidden="true">↑</span>
            </button>
          </div>
        </form>
      </section>
      {source && (
        <CitationDialog
          source={source}
          filename={document.filename}
          onClose={() => setSource(null)}
        />
      )}
    </div>
  );
}
