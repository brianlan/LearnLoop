import { expect, test, type APIRequestContext } from "@playwright/test";
import path from "path";
import { fileURLToPath } from "url";
import {
  addAuthenticatedSession,
  API_BASE,
  DEFAULT_TEST_PASSWORD,
  registerAndLogin,
  type AuthSession,
} from "./helpers";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FAKE_VLM_BASE = "http://127.0.0.1:18001";

test.use({ baseURL: "http://127.0.0.1:5173" });
test.describe.configure({ mode: "serial" });

function fixture(name: string): string {
  return path.join(__dirname, "fixtures", name);
}

async function resetFakeVlm(request: APIRequestContext) {
  const response = await request.post(`${FAKE_VLM_BASE}/_control`, {
    data: { clear: true },
  });
  expect(response.ok(), "reset fake VLM").toBe(true);
}

async function setFakeVlmOverride(
  request: APIRequestContext,
  mode: "fail" | "invalid",
  type: "detection" | "extraction" | "classification" | "grading",
  remaining = 1,
) {
  const response = await request.post(`${FAKE_VLM_BASE}/_control`, {
    data: { mode, type, remaining },
  });
  expect(response.ok(), "set fake VLM override").toBe(true);
}

async function createSession(request: APIRequestContext): Promise<AuthSession> {
  return registerAndLogin(
    request,
    `e2e_bulk_${Date.now()}_${Math.random()}`,
    DEFAULT_TEST_PASSWORD,
  );
}

async function getActiveBatchId(
  request: APIRequestContext,
  session: AuthSession,
): Promise<string> {
  const response = await request.get(`${API_BASE}/ingestion-batches/active`, {
    headers: { Cookie: session.cookieHeader },
  });
  await expect(response, "fetch active batch").toBeOK();
  const payload = await response.json();
  expect(payload.batch?.id, "active batch id").toBeTruthy();
  return payload.batch.id;
}

async function waitForStep(page: any, step: string) {
  await expect(page.getByTestId(`bulk-wizard-${step}-step`)).toBeVisible();
}

async function uploadImages(page: any, fileNames: string[]) {
  await page.goto("/ingest");
  await expect(page.getByTestId("bulk-wizard-create-batch")).toBeVisible();
  await page.getByTestId("bulk-wizard-create-batch").click();
  await waitForStep(page, "upload");

  await page
    .getByTestId("bulk-wizard-upload-input")
    .setInputFiles(fileNames.map((name) => fixture(name)));
  await waitForStep(page, "detect");
}

async function detectAndCommitAll(page: any) {
  const imageCards = page.locator('[data-testid^="bulk-detect-image-"]');
  const count = await imageCards.count();
  expect(count, "images to detect").toBeGreaterThan(0);

  for (let i = 0; i < count; i++) {
    const testId = await imageCards.nth(i).getAttribute("data-testid");
    const imageId = testId!.replace("bulk-detect-image-", "");

    await page.getByTestId(`bulk-detect-run-${imageId}`).click();
    await expect(
      page.getByTestId(`bulk-detect-status-${imageId}`),
      `detect image ${imageId}`,
    ).toHaveText("Review boxes", { timeout: 15000 });

    await page.getByTestId(`bulk-detect-commit-${imageId}`).click();
  }

  await waitForStep(page, "review");
}

async function fetchBatchItems(
  request: APIRequestContext,
  session: AuthSession,
  batchId: string,
): Promise<Array<{ itemId: string; status: string; draft?: Record<string, unknown> }>> {
  const response = await request.get(`${API_BASE}/ingestion-batches/${batchId}`, {
    headers: { Cookie: session.cookieHeader },
  });
  await expect(response, "fetch batch").toBeOK();
  const payload = await response.json();
  return payload.batch?.items ?? [];
}

async function waitForAllItemsReady(
  request: APIRequestContext,
  session: AuthSession,
  batchId: string,
) {
  await expect
    .poll(
      async () => {
        const items = await fetchBatchItems(request, session, batchId);
        const activeItems = items.filter((item) => item.status !== "deleted");
        if (activeItems.length === 0) return false;
        return activeItems.every((item) => item.status === "ready");
      },
      { timeout: 30000 },
    )
    .toBe(true);
}

async function waitForItemsSettled(
  request: APIRequestContext,
  session: AuthSession,
  batchId: string,
) {
  await expect
    .poll(
      async () => {
        const items = await fetchBatchItems(request, session, batchId);
        const activeItems = items.filter((item) => item.status !== "deleted");
        return activeItems.every(
          (item) =>
            item.status === "ready" ||
            item.status === "failed" ||
            item.status === "submit-failed",
        );
      },
      { timeout: 30000 },
    )
    .toBe(true);
}

async function fillDraftsViaApi(
  request: APIRequestContext,
  session: AuthSession,
  batchId: string,
) {
  const response = await request.get(
    `${API_BASE}/ingestion-batches/${batchId}`,
    { headers: { Cookie: session.cookieHeader } },
  );
  await expect(response, "fetch batch for draft fill").toBeOK();
  const payload = await response.json();
  const items = (payload.batch?.items ?? []).filter(
    (item: { status: string }) => item.status === "ready",
  );

  for (const item of items) {
    const patchResponse = await request.patch(
      `${API_BASE}/ingestion-batches/${batchId}/items/${item.itemId}`,
      {
        headers: { Cookie: session.cookieHeader },
        data: {
          text: item.draft?.text || "What is 2 + 2?",
          problemType: "fill-in-the-blank",
          correctAnswer: "4",
          subject: item.draft?.subject || "math",
        },
      },
    );
    await expect(patchResponse, `fill draft ${item.itemId}`).toBeOK();
  }
}

async function submitBatchAndVerifyCount(page: any, expectedCount: number) {
  let currentStep = "";
  await expect
    .poll(async () => {
      if (await page.getByTestId("bulk-wizard-submit-step").isVisible()) {
        currentStep = "submit";
        return currentStep;
      }
      if (await page.getByTestId("bulk-wizard-review-step").isVisible()) {
        currentStep = "review";
        return currentStep;
      }
      currentStep = "";
      return "";
    })
    .toMatch(/^(review|submit)$/);

  if (currentStep === "review") {
    const continueButton = page.getByTestId("bulk-review-continue");
    await expect(continueButton).toBeEnabled();
    await continueButton.click();
  }
  await waitForStep(page, "submit");
  await page.getByTestId("bulk-submit-button").click();
  await expect(page.getByTestId("bulk-wizard-complete")).toBeVisible();
  await expect(page.getByTestId("bulk-wizard-complete-count")).toHaveText(
    `${expectedCount} problem(s) created`,
  );
}

test.describe("Bulk ingestion E2E", () => {
  test.beforeEach(async ({ request }) => {
    await resetFakeVlm(request);
  });

  test("happy path uploads multiple images and submits all items", async ({
    page,
    request,
  }) => {
    test.setTimeout(60000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    await uploadImages(page, ["problem-a.png", "problem-b.png"]);
    await detectAndCommitAll(page);

    const batchId = await getActiveBatchId(request, session);
    await waitForAllItemsReady(request, session, batchId);
    await fillDraftsViaApi(request, session, batchId);

    await page.reload();
    await submitBatchAndVerifyCount(page, 2);
  });

  test("single image with a single box submits one problem", async ({
    page,
    request,
  }) => {
    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    await uploadImages(page, ["problem-a.png"]);
    await detectAndCommitAll(page);

    const batchId = await getActiveBatchId(request, session);
    await waitForAllItemsReady(request, session, batchId);
    await fillDraftsViaApi(request, session, batchId);

    await page.reload();
    await submitBatchAndVerifyCount(page, 1);
  });

  test("keeps review editors focused while autosaving", async ({
    page,
    request,
  }) => {
    test.setTimeout(60000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    await uploadImages(page, ["problem-a.png"]);
    await detectAndCommitAll(page);

    const batchId = await getActiveBatchId(request, session);
    await waitForAllItemsReady(request, session, batchId);

    await page.reload();
    await waitForStep(page, "review");

    const answerInput = page.getByTestId("bulk-review-answer");
    await answerInput.fill("focus answer");
    await expect(answerInput).toBeFocused();
    await page.waitForTimeout(700);
    await expect(answerInput).toBeFocused();

    const graphDslInput = page.getByTestId("bulk-review-graphdsl");
    await graphDslInput.fill("board.create('point', [0, 0]);");
    await expect(graphDslInput).toBeFocused();
    await page.waitForTimeout(700);
    await expect(graphDslInput).toBeFocused();

    const tagInput = page.getByTestId("bulk-review-tags-field");
    await tagInput.fill("focus-tag");
    await tagInput.press("Enter");
    await expect(tagInput).toBeFocused();
    await page.waitForTimeout(700);
    await expect(tagInput).toBeFocused();
    await tagInput.fill("second-tag");
    await expect(tagInput).toHaveValue("second-tag");
  });

  test("recovers from detection failure after retry", async ({
    page,
    request,
  }) => {
    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    await setFakeVlmOverride(request, "fail", "detection", 1);
    await uploadImages(page, ["problem-a.png"]);

    const imageCard = page.locator('[data-testid^="bulk-detect-image-"]').first();
    const testId = await imageCard.getAttribute("data-testid");
    const imageId = testId!.replace("bulk-detect-image-", "");

    await page.getByTestId(`bulk-detect-run-${imageId}`).click();
    await expect(
      page.getByTestId(`bulk-detect-status-${imageId}`),
    ).toHaveText("Detection failed", { timeout: 15000 });
    await expect(
      page.getByTestId(`bulk-detect-failure-${imageId}`),
    ).toBeVisible();

    await page.getByTestId(`bulk-detect-run-${imageId}`).click();
    await expect(
      page.getByTestId(`bulk-detect-status-${imageId}`),
    ).toHaveText("Review boxes", { timeout: 15000 });

    await page.getByTestId(`bulk-detect-commit-${imageId}`).click();
    await waitForStep(page, "review");

    const batchId = await getActiveBatchId(request, session);
    await waitForAllItemsReady(request, session, batchId);
    await fillDraftsViaApi(request, session, batchId);

    await page.reload();
    await submitBatchAndVerifyCount(page, 1);
  });

  test("handles extraction failure by deleting the failed item and submitting the rest", async ({
    page,
    request,
  }) => {
    test.setTimeout(60000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    await setFakeVlmOverride(request, "fail", "extraction", 1);
    await uploadImages(page, ["problem-a.png", "problem-b.png"]);
    await detectAndCommitAll(page);

    const batchId = await getActiveBatchId(request, session);

    let failedItemId = "";
    await expect
      .poll(
        async () => {
          const response = await request.get(
            `${API_BASE}/ingestion-batches/${batchId}`,
            { headers: { Cookie: session.cookieHeader } },
          );
          await expect(response, "poll batch for failure").toBeOK();
          const payload = await response.json();
          const failed = (payload.batch?.items ?? []).find(
            (item: { status: string }) => item.status === "failed",
          );
          if (failed) {
            failedItemId = failed.itemId;
            return true;
          }
          return false;
        },
        { timeout: 30000 },
      )
      .toBe(true);

    // The other item should be ready; fill its draft before deleting the failed one.
    await waitForItemsSettled(request, session, batchId);
    await fillDraftsViaApi(request, session, batchId);

    const failedItem = page.getByTestId(`bulk-review-item-${failedItemId}`);
    if (await failedItem.isEnabled()) {
      await failedItem.click();
    }
    await expect(page.getByTestId("bulk-review-status")).toHaveText(
      "Extraction failed",
    );

    await page.getByTestId("bulk-review-delete").click();

    await page.reload();
    await submitBatchAndVerifyCount(page, 1);
  });

  test("resumes review after reload and retries a failed extraction", async ({
    page,
    request,
  }) => {
    test.setTimeout(60000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    await setFakeVlmOverride(request, "fail", "extraction", 1);
    await uploadImages(page, ["problem-a.png"]);
    await detectAndCommitAll(page);

    const batchId = await getActiveBatchId(request, session);
    await waitForItemsSettled(request, session, batchId);

    await page.reload();
    await waitForStep(page, "review");
    await expect(page.getByTestId("bulk-review-status")).toHaveText(
      "Extraction failed",
    );

    await resetFakeVlm(request);
    await page.getByTestId("bulk-review-retry").click();

    await waitForAllItemsReady(request, session, batchId);
    await fillDraftsViaApi(request, session, batchId);

    await page.reload();
    await submitBatchAndVerifyCount(page, 1);
  });
});
