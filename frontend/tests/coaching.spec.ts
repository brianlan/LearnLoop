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

async function createExam(
  request: APIRequestContext,
  cookie: string,
  problemIds: string[],
) {
  const resp = await request.post(`${API_BASE}/exams`, {
    headers: { Cookie: cookie },
    data: { problemIds },
  });
  return resp.json();
}

let authCookie: string;
let testProblemId: string;

test.beforeAll(async ({ request }) => {
  const username = `e2e_coaching_${Date.now()}`;
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

test.describe("Coaching E2E - Practice Workflow", () => {
  test("AC-03, AC-06: Practice → judgement → AI Explain → coaching page loads → send message → receive response", async ({
    page,
    request,
  }) => {
    // Submit a practice attempt first
    await submitPracticeAttempt(request, authCookie, testProblemId, "4");

    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    // Navigate to practice and start
    await page.goto("/practice");
    await expect(page.getByText("Practice")).toBeVisible();

    // Start practice session
    await page.getByTestId("start-practice-button").click();
    await expect(page.getByTestId("problem-text")).toBeVisible();

    // Submit answer to get feedback
    const input = page.getByRole("textbox");
    await input.fill("4");
    await page.getByTestId("submit-button").click();
    await expect(page.getByTestId("grading-feedback")).toBeVisible();

    // Wait for solution status to become "ready"
    await expect.poll(async () => {
      const statusResp = await request.get(`${API_BASE}/problems/${testProblemId}/solution-status`, {
        headers: { Cookie: authCookie },
      });
      const status = await statusResp.json();
      return status.status;
    }, { timeout: 30000 }).toBe("ready");

    // Reload to refresh solution status query
    await page.reload();
    await expect(page.getByTestId("grading-feedback")).toBeVisible();

    // Click AI Explain button
    await page.getByTestId("explain-button").click();

    // Verify coaching page loads
    await expect(page.getByText("AI Coach")).toBeVisible();
    await expect(page.getByTestId("context-bar")).toBeVisible();

    // Send a message
    const chatInput = page.getByTestId("chat-input");
    await chatInput.fill("Can you explain this problem?");
    await page.getByTestId("send-button").click();

    // Verify response appears
    await expect(page.getByTestId("chat-log")).not.toContainText("No messages yet");
    await expect.poll(() => page.getByTestId("chat-log").textContent()).toContain("Coach");
  });

  test("AC-07: Mode shortcuts produce distinct responses", async ({ page, request }) => {
    await submitPracticeAttempt(request, authCookie, testProblemId, "4");

    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    // Wait for solution to be ready
    await expect.poll(async () => {
      const statusResp = await request.get(`${API_BASE}/problems/${testProblemId}/solution-status`, {
        headers: { Cookie: authCookie },
      });
      const status = await statusResp.json();
      return status.status;
    }, { timeout: 30000 }).toBe("ready");

    // Navigate directly to coaching page
    await page.goto(`/coaching/${testProblemId}`);

    // Clear any existing conversation
    const clearButton = page.getByTestId("clear-button");
    if (await clearButton.isEnabled()) {
      page.on("dialog", dialog => dialog.accept());
      await clearButton.click();
      await page.waitForTimeout(500);
    }

    // Click Explain shortcut
    await page.getByTestId("shortcut-explain").click();
    await expect(page.getByTestId("chat-log")).not.toContainText("No messages yet");
    const explainContent = await page.getByTestId("chat-log").textContent();

    // Clear conversation
    page.on("dialog", dialog => dialog.accept());
    await clearButton.click();
    await page.waitForTimeout(500);

    // Click Hint shortcut
    await page.getByTestId("shortcut-hint").click();
    await expect(page.getByTestId("chat-log")).not.toContainText("No messages yet");
    const hintContent = await page.getByTestId("chat-log").textContent();

    // Verify responses are different
    expect(explainContent).not.toBe(hintContent);
  });
});

test.describe("Coaching E2E - Whiteboard", () => {
  test("AC-08, AC-09: Whiteboard renders from JSXGraph DSL, multiple pages navigable", async ({
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

    // Wait for solution to be ready
    await expect.poll(async () => {
      const statusResp = await request.get(`${API_BASE}/problems/${testProblemId}/solution-status`, {
        headers: { Cookie: authCookie },
      });
      const status = await statusResp.json();
      return status.status;
    }, { timeout: 30000 }).toBe("ready");

    await page.goto(`/coaching/${testProblemId}`);

    // Clear existing conversation
    const clearButton = page.getByTestId("clear-button");
    if (await clearButton.isEnabled()) {
      page.on("dialog", dialog => dialog.accept());
      await clearButton.click();
      await page.waitForTimeout(500);
    }

    // Request a drawing
    await page.getByTestId("shortcut-draw").click();
    await expect(page.getByTestId("chat-log")).not.toContainText("No messages yet");

    // Wait for whiteboard to render (may take time for LLM response)
    await expect.poll(
      async () => {
        const whiteboard = page.getByTestId("whiteboard");
        const emptyState = page.getByTestId("whiteboard-empty");
        return (await whiteboard.isVisible()) && !(await emptyState.isVisible());
      },
      { timeout: 60000 }
    ).toBeTruthy();

    // Check for whiteboard pagination if multiple pages
    const pageIndicator = page.getByTestId("whiteboard-page-indicator");
    const whiteboardNext = page.getByTestId("whiteboard-next");
    const whiteboardPrev = page.getByTestId("whiteboard-prev");

    // If there are multiple pages, test navigation
    const pageCount = await pageIndicator.textContent();
    if (pageCount && !pageCount.includes("1 / 1")) {
      // Navigate to next page
      await whiteboardNext.click();
      await expect(pageIndicator).not.toContainText("1 /");
      // Navigate back
      await whiteboardPrev.click();
      await expect(pageIndicator).toContainText("1 /");
    }
  });
});

test.describe("Coaching E2E - Conversation Persistence", () => {
  test("AC-10: Conversation persists — coach in practice, then access from exam review shows same history", async ({
    page,
    request,
  }) => {
    // Submit practice attempt
    await submitPracticeAttempt(request, authCookie, testProblemId, "4");

    // Create an exam with the problem
    const exam = await createExam(request, authCookie, [testProblemId]);

    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    // Wait for solution to be ready
    await expect.poll(async () => {
      const statusResp = await request.get(`${API_BASE}/problems/${testProblemId}/solution-status`, {
        headers: { Cookie: authCookie },
      });
      const status = await statusResp.json();
      return status.status;
    }, { timeout: 30000 }).toBe("ready");

    // Navigate to coaching from practice
    await page.goto(`/coaching/${testProblemId}`);

    // Clear existing conversation
    const clearButton = page.getByTestId("clear-button");
    if (await clearButton.isEnabled()) {
      page.on("dialog", dialog => dialog.accept());
      await clearButton.click();
      await page.waitForTimeout(500);
    }

    // Send a unique message
    const uniqueMessage = `Test message ${Date.now()}`;
    await page.getByTestId("chat-input").fill(uniqueMessage);
    await page.getByTestId("send-button").click();
    await expect(page.getByTestId("chat-log")).toContainText(uniqueMessage);

    // Go back to practice
    await page.getByTestId("back-button").click();
    await expect(page.getByText("Practice")).toBeVisible();

    // Navigate to exam detail
    await page.goto(`/exams/${exam.exam.id}`);
    await expect(page.getByText("Exam")).toBeVisible();

    // Click AI Explain for the exam item
    await page.getByTestId(`explain-button-${testProblemId}`).click();

    // Verify same conversation appears
    await expect(page.getByTestId("chat-log")).toContainText(uniqueMessage);
  });
});

test.describe("Coaching E2E - Clear Conversation", () => {
  test("AC-11: Clear conversation deletes all messages and whiteboard pages", async ({
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

    // Wait for solution to be ready
    await expect.poll(async () => {
      const statusResp = await request.get(`${API_BASE}/problems/${testProblemId}/solution-status`, {
        headers: { Cookie: authCookie },
      });
      const status = await statusResp.json();
      return status.status;
    }, { timeout: 30000 }).toBe("ready");

    await page.goto(`/coaching/${testProblemId}`);

    // Send a message to ensure conversation has content
    await page.getByTestId("chat-input").fill("Test message to clear");
    await page.getByTestId("send-button").click();
    await expect(page.getByTestId("chat-log")).not.toContainText("No messages yet");

    // Clear conversation
    const clearButton = page.getByTestId("clear-button");
    page.on("dialog", dialog => dialog.accept());
    await clearButton.click();

    // Verify empty state
    await expect(page.getByTestId("chat-log")).toContainText("No messages yet");
    await expect(page.getByTestId("whiteboard-empty")).toBeVisible();

    // Verify clear button is disabled
    await expect(clearButton).toBeDisabled();
  });
});

test.describe("Coaching E2E - Navigation", () => {
  test("AC-15: Back button returns to source page", async ({ page, request }) => {
    await submitPracticeAttempt(request, authCookie, testProblemId, "4");

    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    // Wait for solution to be ready
    await expect.poll(async () => {
      const statusResp = await request.get(`${API_BASE}/problems/${testProblemId}/solution-status`, {
        headers: { Cookie: authCookie },
      });
      const status = await statusResp.json();
      return status.status;
    }, { timeout: 30000 }).toBe("ready");

    // Test from practice
    await page.goto(`/coaching/${testProblemId}`);
    await page.getByTestId("back-button").click();
    await expect(page).toHaveURL(/\/practice/);

    // Test from exam
    const exam = await createExam(request, authCookie, [testProblemId]);
    await page.goto(`/coaching/${testProblemId}`, {
      state: { from: `/exams/${exam.exam.id}` }
    } as any);

    // Navigate to exam, then coaching
    await page.goto(`/exams/${exam.exam.id}`);
    await page.getByTestId(`explain-button-${testProblemId}`).click();
    await expect(page.getByText("AI Coach")).toBeVisible();
    await page.getByTestId("back-button").click();
    await expect(page).toHaveURL(new RegExp(`/exams/${exam.exam.id}`));
  });
});

test.describe("Coaching E2E - Exam Safety", () => {
  test("AC-05: AI Explain NOT visible during active exam", async ({ page, request }) => {
    // Create an exam
    const exam = await createExam(request, authCookie, [testProblemId]);

    await page.context().addCookies([
      {
        name: "session",
        value: authCookie.match(/session=([^;]+)/)?.[1] || "",
        domain: "127.0.0.1",
        path: "/",
      },
    ]);

    // Start the exam
    await page.goto(`/exams/${exam.exam.id}`);
    await page.getByRole("button", { name: /start exam/i }).click();

    // During active exam, AI Explain should not be visible
    await expect(page).toHaveURL(/\/exams\/active/);

    // Verify no explain button is visible during exam
    await expect(page.getByTestId(/explain-button/)).not.toBeVisible();
  });
});

test.describe("Coaching E2E - Failed Solution Safety", () => {
  // PARTIAL TEST: AC-14 requires a backend API or test utility to force solution status to "failed".
  // Without this capability, we cannot properly test that the AI Explain button is disabled
  // for permanently failed solutions. This test is skipped until such an API/utility is available.
  //
  // To complete this test:
  // 1. Create a backend API endpoint or test utility to set solution status to "failed"
  // 2. Use it here to set the new problem's solution status to "failed"
  // 3. Assert that the explain button is disabled
  test.skip("AC-14: AI Explain disabled for permanently failed solution", async ({ page, request }) => {
    // This test is intentionally skipped - see above comment for details.
    // Once a backend API exists to force solution status to "failed", implement:
    // 1. Create problem and set solution status to "failed"
    // 2. Navigate to practice with that problem
    // 3. Assert explainButton is disabled
  });
});
