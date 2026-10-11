import { useEffect, useRef, type ReactNode } from "react";
import {
  fieldLabels,
  type Answer,
  type Citation,
  type Extraction,
  type FieldName,
  type SourcedField,
} from "./types";

type CitationAction = (citation: Citation) => void;

export function Sources({
  sources,
  onSelect,
}: {
  sources: Citation[];
  onSelect: CitationAction;
}) {
  return (
    <div className="sources">
      {sources.map((source, index) => (
        <button
          className="citation"
          key={`${source.doc_id}-${source.page}-${index}`}
          onClick={() => onSelect(source)}
          title={`View quote · ${source.section}`}
        >
          Page {source.page} <span aria-hidden="true">↗</span>
        </button>
      ))}
    </div>
  );
}

function formatValue(value: SourcedField["value"]): ReactNode {
  if (value === null) return "No supported value";
  if (typeof value !== "object") return String(value);
  if (Array.isArray(value))
    return (
      <ul className="value-list">
        {value.map((item, index) => (
          <li key={index}>{formatValue(item)}</li>
        ))}
      </ul>
    );
  return (
    <dl className="value-details">
      {Object.entries(value).map(([key, item]) => (
        <div key={key}>
          <dt>{key.replaceAll("_", " ")}</dt>
          <dd>{formatValue(item)}</dd>
        </div>
      ))}
    </dl>
  );
}

const statusLabels = {
  validated: "Evidence checked",
  low_confidence: "Review needed",
  not_found: "Not found",
  invalid_evidence: "Evidence rejected",
  error: "Extraction error",
};

export function TermsTable({
  result,
  onSelect,
}: {
  result: Extraction;
  onSelect: CitationAction;
}) {
  return (
    <>
      {result.needs_review.length > 0 && (
        <p className="notice">
          {result.needs_review.length} of 14 terms need review. A checked quote
          does not guarantee the interpretation is correct.
        </p>
      )}
      {result.warnings.map((warning, index) => (
        <p className="notice" key={index}>
          {warning}
        </p>
      ))}
      <div className="table-scroll">
        <table>
          <caption className="sr-only">
            Extracted commercial terms and their source pages
          </caption>
          <thead>
            <tr>
              <th scope="col">Commercial term</th>
              <th scope="col">Value</th>
              <th scope="col">Evidence</th>
            </tr>
          </thead>
          <tbody>
            {(Object.keys(fieldLabels) as FieldName[]).map((name) => {
              const field = result.terms[name];
              return (
                <tr key={name}>
                  <th scope="row">{fieldLabels[name]}</th>
                  <td>{formatValue(field.value)}</td>
                  <td>
                    <span
                      className={`field-status ${field.status === "validated" ? "checked" : ""}`}
                    >
                      {statusLabels[field.status]}
                    </span>
                    {field.value !== null && (
                      <small>
                        Model confidence: {Math.round(field.confidence * 100)}%
                      </small>
                    )}
                    <Sources sources={field.sources} onSelect={onSelect} />
                    {field.reason && (
                      <details>
                        <summary>Details</summary>
                        <p>{field.reason}</p>
                      </details>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function AnswerText({
  answer,
  onSelect,
}: {
  answer: Answer;
  onSelect: CitationAction;
}) {
  const labels = new Map(
    answer.citations.map((source) => [
      `[${source.doc_id}, page ${source.page}, section ${source.section.replace(/[\[\]\r\n]/g, " ")}]`,
      source,
    ]),
  );
  return (
    <p className="answer-text">
      {answer.answer.split(/(\[[^\]]+\])/g).map((part, index) => {
        const source = labels.get(part);
        return source ? (
          <button
            className="citation inline"
            key={index}
            onClick={() => onSelect(source)}
          >
            Page {source.page} · {source.section}
          </button>
        ) : (
          part
        );
      })}
    </p>
  );
}

export function CitationDialog({
  source,
  filename,
  onClose,
}: {
  source: Citation;
  filename: string;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);
  return (
    <dialog ref={dialog} aria-labelledby="source-title" onCancel={onClose}>
      <div className="section-heading">
        <p className="eyebrow">Source evidence</p>
        <button className="secondary" onClick={onClose} autoFocus>
          Close
        </button>
      </div>
      <h2 id="source-title">
        Page {source.page} · {source.section}
      </h2>
      <p className="muted filename">{filename}</p>
      <blockquote>{source.quote}</blockquote>
      <p className="muted">
        Page numbers count from the first page of the uploaded PDF.
      </p>
    </dialog>
  );
}
