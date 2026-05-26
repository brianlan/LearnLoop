import { expect, test, type APIRequestContext } from "@playwright/test";

import {
  addAuthenticatedSession,
  APP_BASE,
  DEFAULT_TEST_PASSWORD,
  registerAndLogin,
  seedProblem,
  submitPracticeAttempt,
  type AuthSession,
} from "./helpers";

test.use({ baseURL: APP_BASE });

async function createSession(request: APIRequestContext, prefix: string): Promise<AuthSession> {
  return registerAndLogin(request, `e2e_${prefix}_${Date.now()}_${Math.random()}`, DEFAULT_TEST_PASSWORD);
}

async function createSessionWithProblem(request: APIRequestContext, prefix: string) {
  const session = await createSession(request, prefix);
  const problem = await seedProblem(request, session, {
    text: "What is 2+2?",
    problemType: "fill-in-the-blank",
    correctAnswer: "4",
  });

  return { session, problem };
}

test.describe("Practice E2E", () => {
  test("loads the protected practice page for an authenticated user", async ({ page, request }) => {
    const session = await createSession(request, "practice_page");
    await addAuthenticatedSession(page, session);

    await page.goto("/practice");

    await expect(page.getByRole("heading", { name: "Practice" })).toBeVisible();
    await expect(page.getByTestId("start-practice-button")).toBeVisible();
  });

  test("shows practice history and expands attempts", async ({ page, request }) => {
    const { session, problem } = await createSessionWithProblem(request, "practice_history");
    await submitPracticeAttempt(request, session, problem.id, "4");
    await addAuthenticatedSession(page, session);

    await page.goto("/practice");

    const historyRow = page.getByTestId(`history-row-${problem.id}`);
    await expect(historyRow).toBeVisible();
    await historyRow.click();
    await expect(page.getByTestId(`attempts-${problem.id}`)).toBeVisible();
  });

  test("starts a practice session and submits an answer", async ({ page, request }) => {
    const { session } = await createSessionWithProblem(request, "practice_submit");
    await addAuthenticatedSession(page, session);

    await page.goto("/practice");
    await page.getByTestId("start-practice-button").click();

    await expect(page).toHaveURL(/\/practice\/active/);
    await expect(page.getByTestId("problem-text")).toBeVisible();

    await page.getByRole("textbox").fill("4");
    await page.getByTestId("submit-button").click();

    await expect(page.getByTestId("grading-feedback")).toBeVisible();
  });

  test("shows an empty-state message when no problems exist", async ({ page, request }) => {
    const session = await createSession(request, "practice_empty");
    await addAuthenticatedSession(page, session);

    await page.goto("/practice");
    await page.getByTestId("start-practice-button").click();

    await expect(page.getByTestId("status-message")).toBeVisible();
  });
});
