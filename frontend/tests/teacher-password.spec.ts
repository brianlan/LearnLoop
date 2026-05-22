import { test, expect, type APIRequestContext } from "@playwright/test";

const API_BASE = "http://127.0.0.1:8000/api/v1";

test.use({ baseURL: "http://127.0.0.1:5173" });

async function registerAndLogin(request: APIRequestContext, username: string, password: string) {
  await request.post(`${API_BASE}/auth/register`, {
    data: { username, password },
  });
  const loginResp = await request.post(`${API_BASE}/auth/login`, {
    data: { username, password },
  });
  const setCookie = loginResp.headers()["set-cookie"];
  return setCookie || "";
}

async function seedProblem(
  request: APIRequestContext,
  cookie: string,
  text: string,
  problemType: string,
  correctAnswer: string,
) {
  const previewResp = await request.post(`${API_BASE}/ingest`, {
    headers: { Cookie: cookie },
    data: { text, problemType, correctAnswer },
  });
  const preview = await previewResp.json();
  const confirmResp = await request.post(`${API_BASE}/ingest/${preview.id}/confirm`, {
    headers: { Cookie: cookie },
  });
  return confirmResp.json();
}

async function seedExam(
  request: APIRequestContext,
  cookie: string,
  maxProblemCount: number,
) {
  const resp = await request.post(`${API_BASE}/exams`, {
    headers: { Cookie: cookie },
    data: { maxProblemCount },
  });
  return resp.json();
}

async function submitExamAnswers(
  request: APIRequestContext,
  cookie: string,
  examId: string,
  items: { itemId: string; answer: string }[],
) {
  for (const item of items) {
    await request.patch(`${API_BASE}/exams/${examId}/items/${item.itemId}/answer`, {
      headers: { Cookie: cookie },
      data: { answer: item.answer },
    });
  }
}

async function submitExam(
  request: APIRequestContext,
  cookie: string,
  examId: string,
) {
  const resp = await request.post(`${API_BASE}/exams/${examId}/submit`, {
    headers: { Cookie: cookie },
  });
  return resp.json();
}

const DEFAULT_TEACHER_PASSWORD = "default-teacher-password";

let authCookie: string;
let testProblemId: string;
let testExamId: string;
let testExamItemId: string;

test.beforeAll(async ({ request }) => {
  const username = `e2e_teacher_pw_${Date.now()}`;
  const password = "testpass123";
  authCookie = await registerAndLogin(request, username, password);

  const problem = await seedProblem(
    request,
    authCookie,
    "What is 2+2?",
    "fill-in-the-blank",
    "4",
  );
  testProblemId = problem.id;

  const exam = await seedExam(request, authCookie, 1);
  testExamId = exam.exam.id;
  testExamItemId = exam.exam.items[0].itemId;

  await submitExamAnswers(request, authCookie, testExamId, [
    { itemId: testExamItemId, answer: "4" },
  ]);
  await submitExam(request, authCookie, testExamId);
});

test.describe("Teacher Password E2E", () => {
  test("AC-5: Problem detail — Show Answer → modal → enter password → answer revealed", async ({
    page,
  }) => {
    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    await page.goto(`/problems/${testProblemId}`);
    await expect(page.getByText(`Problem`)).toBeVisible();

    await page.getByRole("button", { name: "Show Answer" }).click();
    await expect(page.getByTestId("teacher-password-modal")).toBeVisible();

    await page.getByTestId("teacher-password-input").fill(DEFAULT_TEACHER_PASSWORD);
    await page.getByTestId("teacher-password-submit").click();

    await expect(page.getByTestId("teacher-password-modal")).not.toBeVisible();
    await expect(page.getByText("4")).toBeVisible();
  });

  test("AC-6: Problem detail edit — Edit → Edit Answer → modal → password → answer editable → save", async ({
    page,
  }) => {
    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    await page.goto(`/problems/${testProblemId}`);
    await expect(page.getByText(`Problem`)).toBeVisible();

    await page.getByRole("button", { name: "Edit" }).click();

    await expect(page.getByTestId("edit-answer-button")).toBeVisible();
    await page.getByTestId("edit-answer-button").click();

    await expect(page.getByTestId("teacher-password-modal")).toBeVisible();
    await page.getByTestId("teacher-password-input").fill(DEFAULT_TEACHER_PASSWORD);
    await page.getByTestId("teacher-password-submit").click();

    await expect(page.getByTestId("teacher-password-modal")).not.toBeVisible();
    await expect(page.getByTestId("edit-answer-input")).toBeVisible();

    await page.getByTestId("edit-answer-input").fill("5");
    await page.getByRole("button", { name: "Save" }).click();

    await expect(page.getByText("5")).toBeVisible();
  });

  test("AC-7: Exam detail — Reveal Answer → modal → password → answer revealed", async ({
    page,
  }) => {
    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    await page.goto(`/exams/${testExamId}`);
    await expect(page.getByText("Exam Results")).toBeVisible();

    const revealButton = page.getByTestId(`reveal-answer-${testExamItemId}`);
    await expect(revealButton).toBeVisible();
    await revealButton.click();

    await expect(page.getByTestId("teacher-password-modal")).toBeVisible();
    await page.getByTestId("teacher-password-input").fill(DEFAULT_TEACHER_PASSWORD);
    await page.getByTestId("teacher-password-submit").click();

    await expect(page.getByTestId("teacher-password-modal")).not.toBeVisible();
    await expect(page.getByText("Correct Answer:")).toBeVisible();
  });

  test("AC-8: Settings — Change Teacher Password → modal → enter current + new + confirm → success", async ({
    page,
    request,
  }) => {
    const username = `e2e_change_pw_${Date.now()}`;
    const password = "testpass123";
    const changePwCookie = await registerAndLogin(request, username, password);

    await page.context().addCookies([
      {
        name: "session",
        value: changePwCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    await page.goto("/settings");
    await expect(page.getByText("Settings")).toBeVisible();

    await page.getByTestId("change-teacher-password-button").click();
    await expect(page.getByTestId("change-password-modal")).toBeVisible();

    await page.getByTestId("current-password-input").fill(DEFAULT_TEACHER_PASSWORD);
    await page.getByTestId("new-password-input").fill("new-password-123");
    await page.getByTestId("confirm-password-input").fill("new-password-123");
    await page.getByTestId("change-password-submit").click();

    await expect(page.getByTestId("change-password-modal")).not.toBeVisible();
    await expect(page.getByTestId("success-message")).toBeVisible();
    await expect(page.getByTestId("success-message")).toHaveText(
      "Teacher password changed successfully",
    );
  });

  test("AC-9: Incorrect password shows error and allows retry", async ({ page }) => {
    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    await page.goto(`/problems/${testProblemId}`);
    await expect(page.getByText(`Problem`)).toBeVisible();

    await page.getByRole("button", { name: "Show Answer" }).click();
    await expect(page.getByTestId("teacher-password-modal")).toBeVisible();

    await page.getByTestId("teacher-password-input").fill("wrong-password");
    await page.getByTestId("teacher-password-submit").click();

    await expect(page.getByTestId("teacher-password-error")).toBeVisible();
    await expect(page.getByTestId("teacher-password-error")).toHaveText(
      "Incorrect teacher password",
    );

    await expect(page.getByTestId("teacher-password-modal")).toBeVisible();

    await page.getByTestId("teacher-password-input").fill(DEFAULT_TEACHER_PASSWORD);
    await page.getByTestId("teacher-password-submit").click();

    await expect(page.getByTestId("teacher-password-modal")).not.toBeVisible();
    await expect(page.getByText("4")).toBeVisible();
  });
});