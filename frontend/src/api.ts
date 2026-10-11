import type {
  Answer,
  ContractDocument,
  DocumentList,
  Extraction,
} from "./types";

const baseUrl = (
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000"
).replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, options);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error("Cannot reach the API. Check that the backend is running.");
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    throw new ApiError(
      response.status,
      typeof detail === "string"
        ? detail
        : response.status === 422
          ? "Check the request fields and try again."
          : `The request failed (${response.status}). Please try again.`,
    );
  }
  if (body === null)
    throw new Error("The API returned an unreadable response.");
  return body as T;
}

const documentPath = (id: string) => `/documents/${encodeURIComponent(id)}`;
const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  list: (offset = 0, signal?: AbortSignal) =>
    request<DocumentList>(`/documents?limit=20&offset=${offset}`, { signal }),
  upload: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    // The browser supplies the multipart boundary; do not set Content-Type here.
    return request<ContractDocument>("/documents", { method: "POST", body });
  },
  document: (id: string, signal?: AbortSignal) =>
    request<ContractDocument>(documentPath(id), { signal }),
  terms: (id: string, signal?: AbortSignal) =>
    request<Extraction>(`${documentPath(id)}/terms`, { signal }),
  extract: (id: string) =>
    request<Extraction>(`${documentPath(id)}/extract`, json({})),
  ask: (id: string, question: string) =>
    request<Answer>(`${documentPath(id)}/ask`, json({ question })),
};

export function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}
