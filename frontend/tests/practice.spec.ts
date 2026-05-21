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

async function submitPracticeAttempt(
  request: APIRequestContext,
  cookie: string,
  problemId: string,
  answer: string,
) {
  const resp = await request.post(`${API_BASE}/practice/attempts`, {
    headers: { Cookie: cookie },
    data: { problemId, submittedAnswer: answer },
  });
  return resp.json();
}

let authCookie: string;
let testProblemId: string;

test.beforeAll(async ({ request }) => {
  const username = `e2e_practice_${Date.now()}`;
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
});

test.describe("Practice E2E", () => {
  test("AC-01: Navigate to /practice, verify page loads", async ({ page }) => {
    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    await page.goto("/practice");
    await expect(page.getByText("Practice")).toBeVisible();
  });

  test("AC-02: With practice history data, verify summary rows display", async ({
    page,
    request,
  }) => {
    await submitPracticeAttempt(request, authCookie, testProblemId, "4");

    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    await page.goto("/practice");
    await expect(page.getByTestId(`history-row-${testProblemId}`)).toBeVisible();
  });

  test("AC-03: Click expand on a history row, verify attempt details shown", async ({
    page,
    request,
  }) => {
    await submitPracticeAttempt(request, authCookie, testProblemId, "4");

    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    await page.goto("/practice");
    await expect(page.getByTestId(`history-row-${testProblemId}`)).toBeVisible();
    await page.getByTestId(`history-row-${testProblemId}`).click();
    await expect(page.getByTestId(`attempts-${testProblemId}`)).toBeVisible();
  });

  test("AC-04: Click Start Practice, verify problem content rendered", async ({ page }) => {
    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    await page.goto("/practice");
    await page.getByTestId("start-practice-button").click();
    await expect(page.getByTestId("problem-text")).toBeVisible();
  });

  test("AC-05: Submit answer, verify feedback indicator shown", async ({ page }) => {
    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    await page.goto("/practice");
    await page.getByTestId("start-practice-button").click();
    await expect(page.getByTestId("problem-text")).toBeVisible();

    const input = page.getByRole("textbox");
    await input.fill("4");
    await page.getByTestId("submit-button").click();
    await expect(page.getByTestId("grading-feedback")).toBeVisible();
  });

  test("AC-06: After feedback, click Next, verify new problem shown", async ({
    page,
    request,
  }) => {
    await seedProblem(request, authCookie, "What is 3+3?", "fill-in-the-blank", "6");

    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    await page.goto("/practice");
    await page.getByTestId("start-practice-button").click();
    await expect(page.getByTestId("problem-text")).toBeVisible();

    const input = page.getByRole("textbox");
    await input.fill("4");
    await page.getByTestId("submit-button").click();
    await expect(page.getByTestId("grading-feedback")).toBeVisible();
    await page.getByTestId("next-button").click();
    await expect(page.getByTestId("problem-text")).toBeVisible();
  });

  test("AC-07: Click Skip, verify new problem shown", async ({ page, request }) => {
    await seedProblem(request, authCookie, "What is 5+5?", "fill-in-the-blank", "10");

    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    await page.goto("/practice");
    await page.getByTestId("start-practice-button").click();
    await expect(page.getByTestId("problem-text")).toBeVisible();

    await page.getByTestId("skip-button").click();
    await expect(page.getByTestId("problem-text")).toBeVisible();
  });

  test("AC-08: Click Quit, verify landing page shown", async ({ page }) => {
    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    await page.goto("/practice");
    await page.getByTestId("start-practice-button").click();
    await expect(page.getByTestId("problem-text")).toBeVisible();

    await page.getByTestId("quit-button").click();
    await expect(page.getByTestId("start-practice-button")).toBeVisible();
  });

  test("AC-13: With no problems, click Start Practice, verify message", async ({
    page,
    request,
  }) => {
    const username = `e2e_empty_${Date.now()}`;
    const password = "testpass123";
    const emptyCookie = await registerAndLogin(request, username, password);

    await page.context().addCookies([
      {
        name: "session",
        value: emptyCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    await page.goto("/practice");
    await page.getByTestId("start-practice-button").click();
    await expect(page.getByTestId("status-message")).toBeVisible();
  });
});