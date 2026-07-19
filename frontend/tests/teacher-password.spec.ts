import { expect, test } from "@playwright/test";

import {
  addAuthenticatedSession,
  APP_BASE,
  createSession,
  createSessionWithProblem,
  DEFAULT_TEACHER_PASSWORD,
} from "./helpers";

test.use({ baseURL: APP_BASE });

test.describe("Teacher Password E2E", () => {
  test("reveals a protected problem answer with the teacher password", async ({ page, request }) => {
    const { session, problem } = await createSessionWithProblem(request, "teacher_reveal");
    await addAuthenticatedSession(page, session);

    await page.goto(`/problems/${problem.id}`);
    await expect(page.getByRole("heading", { name: new RegExp(`^Problem ${problem.id.slice(0, 8)}`) })).toBeVisible();

    await page.getByRole("button", { name: "Show Answer" }).click();
    await expect(page.getByTestId("teacher-password-modal")).toBeVisible();

    await page.getByTestId("teacher-password-input").fill(DEFAULT_TEACHER_PASSWORD);
    await page.getByTestId("teacher-password-submit").click();

    await expect(page.getByTestId("teacher-password-modal")).not.toBeVisible();
    await expect(page.locator("#answer-container")).toHaveText("4");
  });

  test("keeps the answer protected after an incorrect teacher password", async ({ page, request }) => {
    const { session, problem } = await createSessionWithProblem(request, "teacher_incorrect");
    await addAuthenticatedSession(page, session);

    await page.goto(`/problems/${problem.id}`);
    await page.getByRole("button", { name: "Show Answer" }).click();
    await expect(page.getByTestId("teacher-password-modal")).toBeVisible();

    await page.getByTestId("teacher-password-input").fill("wrong-password");
    await page.getByTestId("teacher-password-submit").click();

    await expect(page.getByTestId("teacher-password-error")).toHaveText("Incorrect teacher password");
    await expect(page.getByTestId("teacher-password-modal")).toBeVisible();
    await expect(page.locator("#answer-container")).toHaveCount(0);
  });

  test("changes the teacher password from settings", async ({ page, request }) => {
    const session = await createSession(request, "teacher_settings");
    await addAuthenticatedSession(page, session);

    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();

    await page.getByTestId("change-teacher-password-button").click();
    await expect(page.getByTestId("change-password-modal")).toBeVisible();

    await page.getByTestId("current-password-input").fill(DEFAULT_TEACHER_PASSWORD);
    await page.getByTestId("new-password-input").fill("new-password-123");
    await page.getByTestId("confirm-password-input").fill("new-password-123");
    await page.getByTestId("change-password-submit").click();

    await expect(page.getByTestId("change-password-modal")).not.toBeVisible();
    await expect(page.getByTestId("success-message")).toHaveText("Teacher password changed successfully");
  });
});
