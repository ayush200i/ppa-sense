import { expect, test, type Page } from "@playwright/test";
import {
  fieldLabels,
  type ContractDocument,
  type Extraction,
  type SourcedField,
} from "../src/types";

const document: ContractDocument = {
  doc_id: "sample-a",
  filename: "Sunrise Solar — PPA.pdf",
  status: "ready",
  size_bytes: 1234567,
  created_at: "2026-10-10T10:00:00Z",
  chunks_stored: 142,
  warnings: [],
  error: null,
};
const citation = {
  doc_id: document.doc_id,
  page: 7,
  section: "4. Tariff",
  quote: "The tariff is USD 45 per MWh.",
};
const terms: Extraction = {
  doc_id: document.doc_id,
  terms: Object.fromEntries<SourcedField>(
    Object.keys(fieldLabels).map((name) => [
      name,
      {
        value: null,
        sources: [],
        confidence: 0,
        status: "not_found",
        reason: "No evidence found.",
        attempts: 1,
      },
    ]),
  ) as Extraction["terms"],
  needs_review: Object.keys(fieldLabels).filter(
    (name) => name !== "price_tariff",
  ) as Extraction["needs_review"],
  warnings: [],
};
terms.terms.price_tariff = {
  value: {
    kind: "tariff",
    amount: 45,
    currency: "USD",
    unit: "MWh",
    basis: "Excluding taxes",
  },
  sources: [citation],
  confidence: 0.9,
  status: "validated",
  reason: "Direct tariff clause.",
  attempts: 1,
};

async function mockApi(
  page: Page,
  options: { empty?: boolean; saved?: boolean; failPoll?: boolean } = {},
) {
  let uploaded = !options.empty;
  let saved = options.saved ?? false;
  let failPoll = options.failPoll ?? false;
  let statusReads = 0;
  const requests: { path: string; method: string; body: string | null }[] = [];
  await page.route("http://127.0.0.1:8000/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    requests.push({ path, method, body: request.postData() });
    let body: unknown;
    let status = 200;
    if (path === "/documents" && method === "POST") {
      uploaded = true;
      body = { ...document, status: "queued", chunks_stored: 0 };
      status = 202;
    } else if (path === "/documents") {
      body = { documents: uploaded ? [document] : [], limit: 20, offset: 0 };
    } else if (path.endsWith("/terms")) {
      status = saved ? 200 : 404;
      body = saved ? terms : { detail: "No saved terms; run extraction first" };
    } else if (path.endsWith("/extract")) {
      saved = true;
      body = terms;
    } else if (path.endsWith("/ask")) {
      const question = request.postDataJSON().question;
      body = question.includes("missing")
        ? { answer: "not found in the contract", citations: [] }
        : {
            answer: `The tariff is USD 45 per MWh. [${document.doc_id}, page 7, section 4. Tariff]`,
            citations: [citation],
          };
    } else if (failPoll) {
      failPoll = false;
      status = 500;
      body = { detail: "Status temporarily unavailable" };
    } else {
      statusReads++;
      body =
        options.empty && statusReads === 1
          ? { ...document, status: "processing" }
          : document;
    }
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  return requests;
}

test("upload, poll, extract, cite, ask and reload saved terms", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  const requests = await mockApi(page, { empty: true });
  await page.goto("/");
  await expect(page.getByLabel("Contract PDF")).toBeEnabled();
  await page.getByLabel("Contract PDF").setInputFiles({
    name: "contract.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.7 test fixture"),
  });
  await page.getByRole("button", { name: "Upload contract" }).click();
  await expect(page.getByText("Preparing your contract…")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Extract terms" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Extract terms" }).click();
  await expect(page.getByRole("row")).toHaveCount(15);
  await page.getByRole("button", { name: "Page 7", exact: false }).click();
  await expect(page.getByRole("dialog")).toContainText(citation.quote);
  await expect(page.getByRole("dialog")).toContainText("Page 7");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByLabel("Your question").fill("What is the tariff?");
  await page.getByRole("button", { name: "Ask question" }).click();
  await expect(page.getByRole("log")).toContainText(
    "The tariff is USD 45 per MWh.",
  );
  await page
    .getByRole("button", { name: "Page 7 · 4. Tariff", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText(citation.quote);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.screenshot({
    path: "test-results/workspace-desktop.png",
    fullPage: true,
  });
  await page.reload();
  await expect(page.getByRole("table")).toBeVisible();
  expect(
    requests.filter((request) => request.path.endsWith("/extract")),
  ).toHaveLength(1);
  const upload = requests.find(
    (request) => request.path === "/documents" && request.method === "POST",
  );
  expect(upload?.body).toContain('name="file"');
  expect(
    JSON.parse(
      requests.find((request) => request.path.endsWith("/ask"))!.body!,
    ),
  ).toEqual({ question: "What is the tariff?" });
});

test("poll failure is retryable; absent evidence has no citations", async ({
  page,
}) => {
  await mockApi(page, { failPoll: true });
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText(
    "Status temporarily unavailable",
  );
  await page.getByRole("button", { name: "Retry status check" }).click();
  await expect(page.getByRole("alert")).not.toBeVisible();
  await page.getByLabel("Your question").fill("Find the missing clause");
  await page.getByRole("button", { name: "Ask question" }).click();
  await expect(page.getByRole("log")).toContainText(
    "not found in the contract",
  );
  await expect(page.getByRole("log").getByRole("button")).toHaveCount(0);
});

test("provider failure stays visible and preserves the question on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, { saved: true });
  await page.route("**/documents/*/ask", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        detail: "Answer service is not configured or available",
      }),
    }),
  );
  await page.goto("/");
  await page.getByLabel("Your question").fill("What is the tariff?");
  await page.getByRole("button", { name: "Ask question" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Answer service is not configured",
  );
  await expect(page.getByLabel("Your question")).toHaveValue(
    "What is the tariff?",
  );
  expect(
    await page.evaluate(
      () => window.document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/workspace-mobile.png",
    fullPage: true,
  });
});

test("switching documents clears chat and ignores an old terms request", async ({
  page,
}) => {
  await mockApi(page, { saved: true });
  const other = { ...document, doc_id: "sample-b", filename: "Wind EPC.pdf" };
  let release: () => void = () => {};
  const delayed = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/documents?*", (route) =>
    route.fulfill({
      json: { documents: [document, other], limit: 20, offset: 0 },
    }),
  );
  await page.route("**/documents/sample-a/terms", async (route) => {
    await delayed;
    await route.fulfill({ json: terms });
  });
  await page.route("**/documents/sample-b", (route) =>
    route.fulfill({ json: other }),
  );
  await page.route("**/documents/sample-b/terms", (route) =>
    route.fulfill({ status: 404, json: { detail: "No saved terms" } }),
  );
  await page.goto("/");
  await page.getByLabel("Your question").fill("What is the tariff?");
  await page.getByRole("button", { name: "Ask question" }).click();
  await expect(page.getByRole("log")).toContainText("USD 45");
  await page.getByLabel("Active contract").selectOption("sample-b");
  release();
  await expect(
    page.getByRole("heading", { name: "Wind EPC.pdf" }),
  ).toBeVisible();
  await expect(page.getByRole("log")).not.toContainText("USD 45");
  await expect(page.getByRole("table")).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "Extract terms" }),
  ).toBeEnabled();
});
