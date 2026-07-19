import { expect, test } from "@playwright/test";

import {
  addAuthenticatedSession,
  APP_BASE,
  createSession,
  createSessionWithProblem,
  submitPracticeAttempt,
} from "./helpers";

test.use({ baseURL: APP_BASE });

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
