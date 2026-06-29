import { expect, test } from "@playwright/test";

import {
  addAuthenticatedSession,
  APP_BASE,
  DEFAULT_TEST_PASSWORD,
  registerAndLogin,
  seedActiveExam,
  type AuthSession,
} from "./helpers";

test.use({ baseURL: APP_BASE });

async function createSession(
  request: Parameters<typeof registerAndLogin>[0],
  prefix: string,
): Promise<AuthSession> {
  return registerAndLogin(request, `e2e_${prefix}_${Date.now()}_${Math.random()}`, DEFAULT_TEST_PASSWORD);
}

test.describe("Active Exam print preview", () => {
  test("renders all exam content in the print preview for a one-problem exam", async ({ page, request }) => {
    const session = await createSession(request, "active_exam_print");
    const problemText = "What is 2+2?";
    await seedActiveExam(request, session, {
      text: problemText,
      problemType: "fill-in-the-blank",
      correctAnswer: "4",
    });
    await addAuthenticatedSession(page, session);

    await page.goto("/exams/active");
    await expect(page.getByRole("heading", { name: "Active Exam" })).toBeVisible();

    await page.getByRole("button", { name: "Print" }).click();

    const paper = page.getByTestId("print-preview-paper");
    await expect(paper).toBeVisible();
    await expect(paper.getByText("Exam Paper")).toBeVisible();
    await expect(paper.getByText("Question 1")).toBeVisible();
    await expect(paper.getByText(problemText)).toBeVisible();
  });

  test("print preview hides controls and app shell under print media", async ({ page, request }) => {
    const session = await createSession(request, "active_exam_print_media");
    await seedActiveExam(request, session, {
      text: "Short question?",
      problemType: "fill-in-the-blank",
      correctAnswer: "yes",
    });
    await addAuthenticatedSession(page, session);

    await page.goto("/exams/active");
    await page.getByRole("button", { name: "Print" }).click();
    const paper = page.getByTestId("print-preview-paper");
    await expect(paper).toBeVisible();

    await page.emulateMedia({ media: "print" });

    // Paper content remains visible.
    await expect(paper.getByText("Exam Paper")).toBeVisible();
    await expect(paper.getByText("Question 1")).toBeVisible();

    // Preview controls and app shell should be hidden by print CSS.
    await expect(page.getByTestId("print-preview-print-button")).not.toBeVisible();
    await expect(page.getByRole("button", { name: "Cancel" }).first()).not.toBeVisible();
    await expect(page.locator("header")).not.toBeVisible();
  });
});
