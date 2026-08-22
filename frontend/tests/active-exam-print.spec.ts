import { expect, test } from "@playwright/test";

import {
  addAuthenticatedSession,
  API_BASE,
  APP_BASE,
  createSession,
  seedActiveExam,
  seedProblem,
} from "./helpers";

test.use({ baseURL: APP_BASE });

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
    await expect(page.getByTestId("print-preview-min-height-input")).not.toBeVisible();
    await expect(page.locator("header")).not.toBeVisible();
  });

  test("print preview applies the configured minimum question height in layout", async ({ page, request }) => {
    const session = await createSession(request, "active_exam_print_min_height");
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

    const item = paper.getByTestId("print-preview-item");
    const input = page.getByTestId("print-preview-min-height-input");
    await expect(input).toHaveValue("250");

    const defaultBox = await item.boundingBox();
    expect(defaultBox).not.toBeNull();
    expect(defaultBox!.height).toBeGreaterThanOrEqual(250);

    await input.fill("400");
    await expect(input).toHaveValue("400");

    const tallerBox = await item.boundingBox();
    expect(tallerBox).not.toBeNull();
    expect(tallerBox!.height).toBeGreaterThanOrEqual(400);
  });

  test("print preview controls stay topmost above the wrapped header at narrow widths", async ({ page, request }) => {
    const session = await createSession(request, "active_exam_print_narrow");
    for (let i = 0; i < 6; i += 1) {
      await seedProblem(request, session, {
        text: `Question ${i + 1}: What is ${i + 1}+${i + 1}?`,
        problemType: "fill-in-the-blank",
        correctAnswer: String(2 * (i + 1)),
      });
    }
    const createResponse = await request.post(`${API_BASE}/exams`, {
      headers: { Cookie: session.cookieHeader },
      data: { maxProblemCount: 6 },
    });
    expect(createResponse.ok()).toBeTruthy();
    await addAuthenticatedSession(page, session);

    await page.setViewportSize({ width: 480, height: 800 });
    await page.goto("/exams/active");
    await expect(page.getByRole("heading", { name: "Active Exam" })).toBeVisible();

    await page.getByRole("button", { name: "Print" }).click();
    const paper = page.getByTestId("print-preview-paper");
    await expect(paper).toBeVisible();

    // The wrapped sticky header covers the top of the viewport at 480px, so
    // the preview controls must still be the topmost hit targets there.
    for (const [label, button] of [
      ["Cancel", page.getByRole("button", { name: "Cancel" }).first()],
      ["Print", page.getByTestId("print-preview-print-button")],
    ] as const) {
      const box = await button.boundingBox();
      if (!box) {
        throw new Error(`${label} button should be visible`);
      }
      const hit = await page.evaluate(
        ({ x, y }) => {
          const el = document.elementFromPoint(x, y);
          if (!el) return { found: false, inHeader: false, inButton: false };
          return {
            found: true,
            inHeader: el.closest("header") !== null,
            inButton: el.closest("button") !== null,
          };
        },
        { x: box.x + box.width / 2, y: box.y + box.height / 2 },
      );
      expect(hit.found, `${label} center should hit an element`).toBe(true);
      expect(hit.inHeader, `${label} center should not be intercepted by the sticky header`).toBe(false);
      expect(hit.inButton, `${label} center should hit the ${label} button`).toBe(true);
    }

    await page.getByRole("button", { name: "Cancel" }).first().click();
    await expect(paper).not.toBeVisible();
  });
});
