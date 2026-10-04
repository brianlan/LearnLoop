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

  // Snapshot all image ids up front: committing an image removes its card from
  // the DOM, so iterating the live list by index (nth(i)) races the re-render
  // and can wait forever for an index that no longer exists.
  const imageIds: string[] = [];
  for (let i = 0; i < count; i++) {
    const testId = await imageCards.nth(i).getAttribute("data-testid");
    imageIds.push(testId!.replace("bulk-detect-image-", ""));
  }

  for (const imageId of imageIds) {
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
): Promise<
  Array<{
    itemId: string;
    status: string;
    draft?: Record<string, unknown>;
    contentRevision?: number;
    submit?: { submittedProblemId?: string | null };
    variation?: {
      status: string;
      validation?: { verdict?: string; reports?: unknown[]; failures?: Array<{ kind?: string; evidence?: string }> } | null;
      candidate?: Record<string, unknown> | null;
    } | null;
  }>
> {
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
  problemType: "fill-in-the-blank" | "short-answer" = "fill-in-the-blank",
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
          problemType,
          correctAnswer: "4",
          subject: item.draft?.subject || "math",
          // Variant batches require the autosave-identity revision guard.
          expectedRevision: item.contentRevision,
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
    // Wait for the debounced draft-save PATCH response instead of a fixed timeout.
    await page.waitForResponse(
      (r) => r.request().method() === "PATCH" && r.url().includes("/ingestion-batches/") && r.url().includes("/items/"),
    );
    await expect(answerInput).toBeFocused();

    const graphDslInput = page.getByTestId("bulk-review-graphdsl");
    await graphDslInput.fill("board.create('point', [0, 0]);");
    await expect(graphDslInput).toBeFocused();
    await page.waitForResponse(
      (r) => r.request().method() === "PATCH" && r.url().includes("/ingestion-batches/") && r.url().includes("/items/"),
    );
    await expect(graphDslInput).toBeFocused();

    const tagInput = page.getByTestId("bulk-review-tags-field");
    await tagInput.fill("focus-tag");
    await tagInput.press("Enter");
    await expect(tagInput).toBeFocused();
    await page.waitForResponse(
      (r) => r.request().method() === "PATCH" && r.url().includes("/ingestion-batches/") && r.url().includes("/items/"),
    );
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

type VariantRole = "generator" | "validator-1" | "validator-2" | "helper";
type VariantScenario = Record<VariantRole, string>;

async function setVariantScenario(
  request: APIRequestContext,
  variants: Partial<VariantScenario>,
) {
  const response = await request.post(`${FAKE_VLM_BASE}/_control`, {
    data: { variants },
  });
  await expect(response, "set variant scenario").toBeOK();
}

async function getFakeVariantState(request: APIRequestContext) {
  const response = await request.get(`${FAKE_VLM_BASE}/_control`);
  await expect(response, "read fake VLM state").toBeOK();
  return (await response.json()) as {
    variants: VariantScenario;
    variantCounts: Record<VariantRole, number>;
    grading: { count: number; hadImage: boolean };
  };
}

test.describe("Variant ingestion E2E", () => {
  test.beforeEach(async ({ request }) => {
    await resetFakeVlm(request);
  });

  async function getItemIds(
    request: APIRequestContext,
    session: AuthSession,
    batchId: string,
  ): Promise<string[]> {
    const items = await fetchBatchItems(request, session, batchId);
    return items
      .filter((item) => item.status !== "deleted")
      .map((item) => item.itemId);
  }

  async function getVariation(
    request: APIRequestContext,
    session: AuthSession,
    batchId: string,
    itemId: string,
  ) {
    const items = await fetchBatchItems(request, session, batchId);
    return items.find((item) => item.itemId === itemId)?.variation ?? null;
  }

  async function waitForVariation(
    request: APIRequestContext,
    session: AuthSession,
    batchId: string,
    itemId: string,
    predicate: (
      variation: NonNullable<
        Awaited<ReturnType<typeof getVariation>>
      > | null,
    ) => boolean,
    description: string,
    timeout = 45000,
  ) {
    const deadline = Date.now() + timeout;
    for (;;) {
      const observed = await getVariation(request, session, batchId, itemId);
      if (predicate(observed as never)) return observed;
      if (Date.now() > deadline) {
        throw new Error(
          `${description} — last observed: ${JSON.stringify(observed)}`,
        );
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }

  async function startVariantBatch(
    page: any,
    request: APIRequestContext,
    session: AuthSession,
    mode: "data-only" | "transfer-variant",
    images: string[],
  ): Promise<string> {
    await page.goto("/ingest");
    await expect(page.getByTestId("bulk-wizard-create-batch")).toBeVisible();
    await page.getByTestId(`bulk-wizard-mode-${mode}`).check();
    await page.getByTestId("bulk-wizard-create-batch").click();
    await waitForStep(page, "upload");

    await page
      .getByTestId("bulk-wizard-upload-input")
      .setInputFiles(images.map((name) => fixture(name)));
    await waitForStep(page, "detect");
    await detectAndCommitAll(page);

    const batchId = await getActiveBatchId(request, session);
    await waitForAllItemsReady(request, session, batchId);
    await fillDraftsViaApi(
      request,
      session,
      batchId,
      // Short-answer variants exercise VLM grading downstream.
      "short-answer",
    );
    await page.reload();
    await waitForStep(page, "review");
    return batchId;
  }

  // The selected item's own row button is disabled; select only when needed.
  async function selectReviewItem(page: any, itemId: string) {
    const row = page.getByTestId(`bulk-review-item-${itemId}`);
    if (await row.isEnabled()) await row.click();
  }

  async function generateVariant(
    page: any,
    request: APIRequestContext,
    session: AuthSession,
    batchId: string,
    itemId: string,
  ) {
    await selectReviewItem(page, itemId);
    await page.getByTestId("bulk-review-generate").click();
    return waitForVariation(
      request,
      session,
      batchId,
      itemId,
      (variation) =>
        variation?.status === "ready" &&
        variation.validation?.verdict === "pass",
      `variant for ${itemId} reaches current PASS`,
    );
  }

  async function getSubmittedProblemId(
    request: APIRequestContext,
    session: AuthSession,
    batchId: string,
  ): Promise<string> {
    const items = await fetchBatchItems(request, session, batchId);
    const submitted = items.find(
      (item) => item.status === "submitted" && item.submit?.submittedProblemId,
    );
    expect(submitted, "a submitted item with a problem id").toBeTruthy();
    return submitted!.submit!.submittedProblemId!;
  }

  async function gradeVariantInPractice(page: any) {
    await page.goto("/practice");
    await page.getByTestId("start-practice-button").click();
    await expect(page).toHaveURL(/\/practice\/active/);
    await expect(page.getByTestId("problem-text")).toBeVisible();

    // A non-exact answer forces the VLM grading path for short-answer.
    await page.getByRole("textbox").fill("four");
    await page.getByTestId("submit-button").click();
    await expect(page.getByTestId("grading-feedback")).toBeVisible();
  }

  test("data-only happy path: generate, submit, provenance, practice and exam grading", async ({
    page,
    request,
  }) => {
    test.setTimeout(150000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    // Two variants: practice grades one, the exam the other (grading a
    // problem puts it into the selection cooldown window).
    const batchId = await startVariantBatch(
      page,
      request,
      session,
      "data-only",
      ["problem-a.png", "problem-b.png"],
    );
    for (const itemId of await getItemIds(request, session, batchId)) {
      await generateVariant(page, request, session, batchId, itemId);
    }

    await expect(page.getByTestId("bulk-review-continue")).toBeEnabled();
    await page.getByTestId("bulk-review-continue").click();
    await waitForStep(page, "submit");
    await page.getByTestId("bulk-submit-button").click();
    await expect(page.getByTestId("bulk-wizard-complete")).toBeVisible();
    await expect(page.getByTestId("bulk-wizard-complete-count")).toHaveText(
      "2 problem(s) created",
    );

    const items = await fetchBatchItems(request, session, batchId);
    const problemIds = items
      .filter((item) => item.status === "submitted" && item.submit?.submittedProblemId)
      .map((item) => item.submit!.submittedProblemId!);
    expect(problemIds, "two submitted variant problems").toHaveLength(2);

    // The submitted candidates must be self-consistent: the fake generator
    // bumps `2 + 2` to `3 + 3`, so each problem's answer must be `6`.
    for (const problemId of problemIds) {
      const problemResponse = await request.get(`${API_BASE}/problems/${problemId}`, {
        headers: { Cookie: session.cookieHeader },
      });
      await expect(problemResponse, "fetch submitted problem").toBeOK();
      const problem = (await problemResponse.json()).problem;
      expect(problem.text).toBe("What is 3 + 3?");
      expect(problem.correctAnswer.display).toBe("6");
    }

    // Practice grading of a submitted variant; the fake grading role must
    // never receive an image.
    await gradeVariantInPractice(page);

    // Exam grading of the remaining variant problem.
    const examResponse = await request.post(`${API_BASE}/exams`, {
      headers: { Cookie: session.cookieHeader },
      data: { maxProblemCount: 1 },
    });
    await expect(examResponse, "create exam").toBeOK();
    const examId = (await examResponse.json()).exam.id;

    let examItems: Array<{ itemId: string; problemId: string }> = [];
    await expect
      .poll(
        async () => {
          const exam = await request.get(`${API_BASE}/exams/${examId}`, {
            headers: { Cookie: session.cookieHeader },
          });
          await expect(exam, "fetch exam").toBeOK();
          examItems = ((await exam.json()).exam?.items ?? []) as Array<{ itemId: string; problemId: string }>;
          return examItems.length;
        },
        { timeout: 30000, message: "exam selection picks the remaining variant" },
      )
      .toBe(1);
    expect(problemIds).toContain(examItems[0].problemId);

    const answerResponse = await request.patch(
      `${API_BASE}/exams/${examId}/items/${examItems[0].itemId}/answer`,
      {
        headers: { Cookie: session.cookieHeader },
        data: { answer: "four" },
      },
    );
    await expect(answerResponse, "save exam answer").toBeOK();

    const submitResponse = await request.post(`${API_BASE}/exams/${examId}/submit`, {
      headers: { Cookie: session.cookieHeader },
    });
    await expect(submitResponse, "submit exam").toBeOK();

    const examDeadline = Date.now() + 30000;
    let examPayload: {
      state?: string;
      items?: Array<{ grading?: { status?: string; isCorrect?: boolean | null; method?: string | null } }>;
    } = {};
    for (;;) {
      const exam = await request.get(`${API_BASE}/exams/${examId}`, {
        headers: { Cookie: session.cookieHeader },
      });
      await expect(exam, "fetch graded exam").toBeOK();
      examPayload = (await exam.json()).exam ?? {};
      const grading = examPayload.items?.[0]?.grading;
      if (grading?.isCorrect === true) break;
      if (Date.now() > examDeadline) {
        throw new Error(
          `exam item graded correct — last observed: ${JSON.stringify(examPayload)}`,
        );
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }

    // Provenance of the exam-graded variant: collapsed by default, expands
    // to the admitted evidence; the source snapshot is withheld (#660).
    await page.goto(`/problems/${examItems[0].problemId}`);
    await expect(
      page.getByTestId("problem-variation-provenance"),
    ).toBeVisible();
    await page.getByTestId("problem-variation-provenance-toggle").click();
    const provenance = page.getByTestId("problem-variation-provenance-body");
    await expect(provenance).toBeVisible();
    await expect(provenance).toContainText(
      "Source evidence is retained but withheld.",
    );
    await expect(provenance).toContainText("What is 3 + 3?");
    await expect(provenance).toContainText("validator-1");
    await expect(provenance).toContainText("variant-generator");

    const fakeState = await getFakeVariantState(request);
    expect(fakeState.grading.count, "grading calls (practice + exam)").toBeGreaterThanOrEqual(2);
    expect(fakeState.grading.hadImage, "no image in grading context").toBe(false);
    expect(fakeState.variantCounts.generator).toBe(2);
    expect(fakeState.variantCounts["validator-1"]).toBe(2);
    expect(fakeState.variantCounts["validator-2"]).toBe(2);
    expect(fakeState.variantCounts.helper).toBe(4);
  });

  test("transfer-variant carries a graph and grades in practice", async ({
    page,
    request,
  }) => {
    test.setTimeout(120000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    const batchId = await startVariantBatch(
      page,
      request,
      session,
      "transfer-variant",
      ["problem-a.png"],
    );
    const itemId = (await getItemIds(request, session, batchId))[0];

    // Give the source a graph (pre-candidate the editor targets the source):
    // the candidate must carry one too.
    await page
      .getByTestId("bulk-review-graphdsl")
      .fill("board.create('point', [0, 0]);");
    await page.waitForResponse(
      (r) =>
        r.request().method() === "PATCH" &&
        r.url().includes("/ingestion-batches/"),
    );

    await generateVariant(page, request, session, batchId, itemId);
    await page.getByTestId("bulk-review-continue").click();
    await waitForStep(page, "submit");
    await page.getByTestId("bulk-submit-button").click();
    await expect(page.getByTestId("bulk-wizard-complete")).toBeVisible();

    const problemId = await getSubmittedProblemId(request, session, batchId);
    await page.goto(`/problems/${problemId}`);
    await page.getByTestId("problem-variation-provenance-toggle").click();
    await expect(page.getByTestId("problem-variation-provenance-body")).toContainText(
      "board.create",
    );

    await gradeVariantInPractice(page);
    const fakeState = await getFakeVariantState(request);
    expect(fakeState.grading.count).toBeGreaterThanOrEqual(1);
    expect(fakeState.grading.hadImage).toBe(false);
  });

  test("generation runs in the background while another item is edited", async ({
    page,
    request,
  }) => {
    test.setTimeout(120000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    const batchId = await startVariantBatch(
      page,
      request,
      session,
      "data-only",
      ["problem-a.png", "problem-b.png"],
    );
    const [itemA, itemB] = await getItemIds(request, session, batchId);

    await selectReviewItem(page, itemA);
    await page.getByTestId("bulk-review-generate").click();

    // While item A generates, switch to item B and autosave a draft edit.
    await selectReviewItem(page, itemB);
    await page.getByTestId("bulk-review-text").fill("Edited source text about 7 apples");
    await page.waitForResponse(
      (r) =>
        r.request().method() === "PATCH" &&
        r.url().includes("/ingestion-batches/"),
    );

    await waitForVariation(
      request,
      session,
      batchId,
      itemA,
      (variation) =>
        variation?.status === "ready" &&
        variation.validation?.verdict === "pass",
      `variant for ${itemA} reaches current PASS`,
    );

    // Item B untouched: no variant, Continue stays gated by its missing PASS.
    const variationB = await getVariation(request, session, batchId, itemB);
    expect(variationB?.status ?? "not-requested").toBe("not-requested");
    await expect(page.getByTestId("bulk-review-continue")).toBeDisabled();

    // Generate item B too, then submit both.
    await generateVariant(page, request, session, batchId, itemB);
    await page.getByTestId("bulk-review-continue").click();
    await waitForStep(page, "submit");
    await page.getByTestId("bulk-submit-button").click();
    await expect(page.getByTestId("bulk-wizard-complete-count")).toHaveText(
      "2 problem(s) created",
    );
  });

  test("reload mid-validation resumes without regenerating", async ({
    page,
    request,
  }) => {
    test.setTimeout(120000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    await setVariantScenario(request, { "validator-2": "slow" });
    const batchId = await startVariantBatch(
      page,
      request,
      session,
      "data-only",
      ["problem-a.png"],
    );
    const itemId = (await getItemIds(request, session, batchId))[0];

    await page.getByTestId("bulk-review-generate").click();
    await waitForVariation(
      request,
      session,
      batchId,
      itemId,
      (variation) => variation?.status === "validating",
      `variant for ${itemId} enters validation`,
    );

    await page.reload();
    await expect(page.getByTestId("bulk-review-variation-status")).toHaveText(
      "Variant: validating...",
      { timeout: 15000 },
    );
    await expect(page.getByTestId("bulk-review-variation-status")).toHaveText(
      "Variant: ready",
      { timeout: 45000 },
    );
    await expect(page.getByTestId("bulk-review-continue")).toBeEnabled();

    const fakeState = await getFakeVariantState(request);
    // Exactly one generation: the resumed validation reused the checkpointed
    // candidate ("revalidation never regenerates").
    expect(fakeState.variantCounts.generator).toBe(1);
  });

  test("reload mid-generation resumes without restarting generation", async ({
    page,
    request,
  }) => {
    test.setTimeout(120000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    await setVariantScenario(request, { generator: "slow" });
    const batchId = await startVariantBatch(
      page,
      request,
      session,
      "data-only",
      ["problem-a.png"],
    );
    const itemId = (await getItemIds(request, session, batchId))[0];

    await page.getByTestId("bulk-review-generate").click();
    await waitForVariation(
      request,
      session,
      batchId,
      itemId,
      (variation) => variation?.status === "generating",
      `variant for ${itemId} enters generation`,
    );

    await page.reload();
    await expect(page.getByTestId("bulk-review-variation-status")).toHaveText(
      "Variant: generating...",
      { timeout: 15000 },
    );
    await expect(page.getByTestId("bulk-review-variation-status")).toHaveText(
      "Variant: ready",
      { timeout: 45000 },
    );
    await expect(page.getByTestId("bulk-review-continue")).toBeEnabled();

    const fakeState = await getFakeVariantState(request);
    // The reload resumed the in-flight generation instead of starting another
    // one.
    expect(fakeState.variantCounts.generator).toBe(1);
  });

  test("generator failure shows evidence and Generate Again recovers", async ({
    page,
    request,
  }) => {
    test.setTimeout(120000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    await setVariantScenario(request, { generator: "fail" });
    const batchId = await startVariantBatch(
      page,
      request,
      session,
      "data-only",
      ["problem-a.png"],
    );
    const itemId = (await getItemIds(request, session, batchId))[0];

    await page.getByTestId("bulk-review-generate").click();
    await waitForVariation(
      request,
      session,
      batchId,
      itemId,
      (variation) => variation?.status === "failed",
      `variant for ${itemId} fails`,
    );

    await expect(page.getByTestId("bulk-review-variation-status")).toHaveText(
      "Variant: failed",
    );
    await expect(page.getByTestId("bulk-review-evidence")).toBeVisible();
    await expect(
      page.getByTestId("bulk-review-evidence-failure-kind").first(),
    ).toContainText("Model execution failure");
    await expect(page.getByTestId("bulk-review-continue")).toBeDisabled();
    // No accept/override/fallback control exists on the failure evidence.
    await expect(
      page.getByRole("button", { name: /accept|override|fallback/i }),
    ).toHaveCount(0);

    await setVariantScenario(request, { generator: "pass" });
    await page.getByTestId("bulk-review-generate").click();
    await waitForVariation(
      request,
      session,
      batchId,
      itemId,
      (variation) =>
        variation?.status === "ready" &&
        variation.validation?.verdict === "pass",
      `variant for ${itemId} reaches current PASS after retry`,
    );
    await expect(page.getByTestId("bulk-review-continue")).toBeEnabled();

    const fakeState = await getFakeVariantState(request);
    expect(fakeState.variantCounts.generator).toBe(2);
  });

  test("semantic candidate edit requires revalidation and never regenerates", async ({
    page,
    request,
  }) => {
    test.setTimeout(120000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    const batchId = await startVariantBatch(
      page,
      request,
      session,
      "data-only",
      ["problem-a.png"],
    );
    const itemId = (await getItemIds(request, session, batchId))[0];
    await generateVariant(page, request, session, batchId, itemId);

    // Semantic edit on the candidate invalidates the approval. The edit stays
    // mathematically consistent — `What is 4 + 3?` solves to `7`, matching the
    // edited answer — so revalidation can honestly approve the new candidate.
    await page.getByTestId("bulk-review-edit-candidate").click();
    await page.getByTestId("bulk-review-text").fill("What is 4 + 3?");
    await page.getByTestId("bulk-review-answer").fill("7");
    await page.waitForResponse(
      (r) =>
        r.request().method() === "PATCH" &&
        r.url().includes("/variation/candidate"),
    );
    await expect(page.getByTestId("bulk-review-variation-status")).toHaveText(
      "Variant: needs validation",
      { timeout: 15000 },
    );
    await expect(page.getByTestId("bulk-review-continue")).toBeDisabled();

    await page.getByTestId("bulk-review-revalidate").click();
    await waitForVariation(
      request,
      session,
      batchId,
      itemId,
      (variation) =>
        variation?.status === "ready" &&
        variation.validation?.verdict === "pass" &&
        variation.validation?.reports !== undefined,
      `variant for ${itemId} re-approves after revalidation`,
    );
    await expect(page.getByTestId("bulk-review-continue")).toBeEnabled();

    const revalidated = (await getVariation(request, session, batchId, itemId))
      ?.candidate;
    expect(revalidated?.text).toBe("What is 4 + 3?");
    expect(revalidated?.correctAnswer).toBe("7");

    const fakeState = await getFakeVariantState(request);
    // Revalidation re-ran both validators and the helper but never the
    // generator.
    expect(fakeState.variantCounts.generator).toBe(1);
    expect(fakeState.variantCounts["validator-1"]).toBe(2);
    expect(fakeState.variantCounts["validator-2"]).toBe(2);
    expect(fakeState.variantCounts.helper).toBe(4);
  });

  test("tags-only candidate edit keeps the approval", async ({
    page,
    request,
  }) => {
    test.setTimeout(120000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    const batchId = await startVariantBatch(
      page,
      request,
      session,
      "data-only",
      ["problem-a.png"],
    );
    const itemId = (await getItemIds(request, session, batchId))[0];
    await generateVariant(page, request, session, batchId, itemId);

    await page.getByTestId("bulk-review-tags-field").fill("variant-tag");
    await page.getByTestId("bulk-review-tags-field").press("Enter");
    await page.waitForResponse(
      (r) =>
        r.request().method() === "PATCH" &&
        r.url().includes("/ingestion-batches/"),
    );

    const variation = await getVariation(request, session, batchId, itemId);
    expect(variation?.status).toBe("ready");
    expect(variation?.validation?.verdict).toBe("pass");
    await expect(page.getByTestId("bulk-review-continue")).toBeEnabled();
  });

  test("source edit invalidates the variant and requires regeneration", async ({
    page,
    request,
  }) => {
    test.setTimeout(120000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    const batchId = await startVariantBatch(
      page,
      request,
      session,
      "data-only",
      ["problem-a.png"],
    );
    const itemId = (await getItemIds(request, session, batchId))[0];
    await generateVariant(page, request, session, batchId, itemId);

    await page.getByTestId("bulk-review-edit-source").click();
    await expect(
      page.getByTestId("bulk-review-source-invalidation-warning"),
    ).toBeVisible();
    await page.getByTestId("bulk-review-text").fill("John has 5 apples");
    await page.waitForResponse(
      (r) =>
        r.request().method() === "PATCH" &&
        r.url().includes("/ingestion-batches/") &&
        !r.url().includes("/variation/"),
    );

    await waitForVariation(
      request,
      session,
      batchId,
      itemId,
      (variation) => variation === null || variation.status === "not-requested",
      `variant for ${itemId} resets after source edit`,
    );
    await expect(page.getByTestId("bulk-review-variation-status")).toHaveText(
      "Variant: not generated",
      { timeout: 15000 },
    );
    await expect(page.getByTestId("bulk-review-continue")).toBeDisabled();

    // A fresh generation works from the edited source.
    await generateVariant(page, request, session, batchId, itemId);
    await page.getByTestId("bulk-review-edit-candidate").click();
    await expect(page.getByTestId("bulk-review-text")).toHaveValue(
      /John has 6 apples/,
    );
  });

  test("helper mismatch fails validation with visible evidence", async ({
    page,
    request,
  }) => {
    test.setTimeout(120000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    await setVariantScenario(request, { helper: "different" });
    const batchId = await startVariantBatch(
      page,
      request,
      session,
      "data-only",
      ["problem-a.png"],
    );
    const itemId = (await getItemIds(request, session, batchId))[0];

    await page.getByTestId("bulk-review-generate").click();
    await waitForVariation(
      request,
      session,
      batchId,
      itemId,
      (variation) => variation?.status === "failed",
      `variant for ${itemId} fails on helper mismatch`,
    );

    await expect(page.getByTestId("bulk-review-evidence")).toBeVisible();
    await expect(page.getByTestId("bulk-review-evidence")).toContainText(
      "helper comparison for variant answer: different",
    );
    await expect(
      page.getByTestId("bulk-review-evidence-helper-variant").first(),
    ).toContainText("different");
    await expect(page.getByTestId("bulk-review-continue")).toBeDisabled();
    await expect(
      page.getByRole("button", { name: /accept|override|fallback/i }),
    ).toHaveCount(0);
  });

  test("helper uncertainty fails validation with visible evidence", async ({
    page,
    request,
  }) => {
    test.setTimeout(120000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    await setVariantScenario(request, { helper: "uncertain" });
    const batchId = await startVariantBatch(
      page,
      request,
      session,
      "data-only",
      ["problem-a.png"],
    );
    const itemId = (await getItemIds(request, session, batchId))[0];

    await page.getByTestId("bulk-review-generate").click();
    await waitForVariation(
      request,
      session,
      batchId,
      itemId,
      (variation) => variation?.status === "failed",
      `variant for ${itemId} fails on helper uncertainty`,
    );

    await expect(page.getByTestId("bulk-review-evidence")).toBeVisible();
    await expect(page.getByTestId("bulk-review-evidence")).toContainText(
      "helper comparison for variant answer: uncertain",
    );
    await expect(page.getByTestId("bulk-review-continue")).toBeDisabled();
  });

  test("second-validator transport failure fails closed on one report", async ({
    page,
    request,
  }) => {
    test.setTimeout(120000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    await setVariantScenario(request, { "validator-2": "fail" });
    const batchId = await startVariantBatch(
      page,
      request,
      session,
      "data-only",
      ["problem-a.png"],
    );
    const itemId = (await getItemIds(request, session, batchId))[0];

    await page.getByTestId("bulk-review-generate").click();
    await waitForVariation(
      request,
      session,
      batchId,
      itemId,
      (variation) => variation?.status === "failed",
      `variant for ${itemId} fails when validator-2 errors`,
    );

    // Only validator-1 completed; the transport failure fails the whole run.
    await expect(page.getByTestId("bulk-review-evidence")).toBeVisible();
    await expect(page.getByTestId("bulk-review-evidence-report")).toHaveCount(1);
    await expect(page.getByTestId("bulk-review-evidence-failure").first()).toContainText(
      "validator-2",
    );
    await expect(page.getByTestId("bulk-review-continue")).toBeDisabled();

    const fakeState = await getFakeVariantState(request);
    expect(fakeState.variantCounts["validator-1"]).toBe(1);
    expect(fakeState.variantCounts["validator-2"]).toBe(1);
    // The helper ran for validator-1's report only.
    expect(fakeState.variantCounts.helper).toBe(1);
  });

  test("an unsolvable validator report blocks approval and skips its helper call", async ({
    page,
    request,
  }) => {
    test.setTimeout(120000);

    const session = await createSession(request);
    await addAuthenticatedSession(page, session);

    await setVariantScenario(request, { "validator-1": "unsolvable" });
    const batchId = await startVariantBatch(
      page,
      request,
      session,
      "data-only",
      ["problem-a.png"],
    );
    const itemId = (await getItemIds(request, session, batchId))[0];

    await page.getByTestId("bulk-review-generate").click();
    await waitForVariation(
      request,
      session,
      batchId,
      itemId,
      (variation) => variation?.status === "failed",
      `variant for ${itemId} fails when validator-1 cannot solve`,
    );

    await expect(page.getByTestId("bulk-review-evidence")).toBeVisible();
    await expect(page.getByTestId("bulk-review-evidence-failure").first()).toContainText(
      "could not solve",
    );
    await expect(page.getByTestId("bulk-review-continue")).toBeDisabled();

    const fakeState = await getFakeVariantState(request);
    // Helper skipped for the unsolvable validator-1 report, called once for
    // validator-2.
    expect(fakeState.variantCounts.helper).toBe(1);
  });
});
