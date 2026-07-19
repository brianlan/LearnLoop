import { expect, type APIRequestContext, type APIResponse, type Page } from "@playwright/test";

const PLAYWRIGHT_API_ORIGIN = process.env.PLAYWRIGHT_API_ORIGIN ?? "http://127.0.0.1:8000";

export const API_BASE = `${PLAYWRIGHT_API_ORIGIN}/api/v1`;
export const APP_BASE = "http://127.0.0.1:5173";
export const DEFAULT_TEST_PASSWORD = "testpass123";
export const DEFAULT_TEACHER_PASSWORD = "default-teacher-password";
export const SESSION_COOKIE_NAME = "ll_session";

const PNG_BYTES = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+tmX8AAAAASUVORK5CYII=",
  "base64",
);

export type AuthSession = {
  cookieHeader: string;
  sessionToken: string;
};

type ProblemSeedInput = {
  text: string;
  problemType: "fill-in-the-blank" | "short-answer";
  correctAnswer: string;
  tags?: string[];
};

async function expectOk(response: APIResponse, label: string) {
  if (response.ok()) {
    return;
  }

  throw new Error(`${label} failed: ${response.status()} ${await response.text()}`);
}

export async function registerAndLogin(
  request: APIRequestContext,
  username: string,
  password = DEFAULT_TEST_PASSWORD,
): Promise<AuthSession> {
  const registerResponse = await request.post(`${API_BASE}/auth/register`, {
    data: { username, password },
  });
  await expectOk(registerResponse, "register");

  const loginResponse = await request.post(`${API_BASE}/auth/login`, {
    data: { username, password },
  });
  await expectOk(loginResponse, "login");

  const setCookie = loginResponse.headers()["set-cookie"];
  const sessionToken = setCookie?.match(new RegExp(`${SESSION_COOKIE_NAME}=([^;]+)`))?.[1];
  expect(sessionToken, "session cookie should be present after login").toBeTruthy();

  return {
    cookieHeader: `${SESSION_COOKIE_NAME}=${sessionToken!}`,
    sessionToken: sessionToken!,
  };
}

export async function addAuthenticatedSession(page: Page, session: AuthSession): Promise<void> {
  await page.context().addCookies([
    {
      name: SESSION_COOKIE_NAME,
      value: session.sessionToken,
      domain: "127.0.0.1",
      path: "/",
    },
  ]);
}

export async function seedProblem(
  request: APIRequestContext,
  session: AuthSession,
  input: ProblemSeedInput,
) {
  const previewResponse = await request.post(`${API_BASE}/ingestion-previews`, {
    headers: { Cookie: session.cookieHeader },
    multipart: {
      image: {
        name: "problem.png",
        mimeType: "image/png",
        buffer: PNG_BYTES,
      },
    },
  });
  await expectOk(previewResponse, "create ingestion preview");

  const previewPayload = await previewResponse.json();
  const previewId = previewPayload.preview?.id;
  expect(previewId, "preview id should be present").toBeTruthy();

  await expect
    .poll(async () => {
      const statusResponse = await request.get(`${API_BASE}/ingestion-previews/${previewId}`, {
        headers: { Cookie: session.cookieHeader },
      });
      await expectOk(statusResponse, "fetch ingestion preview");
      const statusPayload = await statusResponse.json();
      return statusPayload.preview?.status;
    }, { timeout: 30000 })
    .toMatch(/^(ready|vlm-failed)$/);

  const patchResponse = await request.patch(`${API_BASE}/ingestion-previews/${previewId}`, {
    headers: { Cookie: session.cookieHeader },
    data: {
      text: input.text,
      problemType: input.problemType,
      correctAnswer: input.correctAnswer,
      tags: input.tags ?? [],
    },
  });
  await expectOk(patchResponse, "patch ingestion preview");

  const confirmResponse = await request.post(`${API_BASE}/ingestion-previews/${previewId}/confirm`, {
    headers: { Cookie: session.cookieHeader },
  });
  await expectOk(confirmResponse, "confirm ingestion preview");

  const confirmPayload = await confirmResponse.json();
  const problem = confirmPayload.problem;
  expect(problem?.id, "confirmed problem id should be present").toBeTruthy();
  return problem;
}

export async function submitPracticeAttempt(
  request: APIRequestContext,
  session: AuthSession,
  problemId: string,
  answer: string,
) {
  const response = await request.post(`${API_BASE}/practice/attempts`, {
    headers: { Cookie: session.cookieHeader },
    data: { problemId, submittedAnswer: answer },
  });
  await expectOk(response, "submit practice attempt");
  return response.json();
}

export async function seedActiveExam(
  request: APIRequestContext,
  session: AuthSession,
  problemInput: ProblemSeedInput,
) {
  const problem = await seedProblem(request, session, problemInput);

  const createResponse = await request.post(`${API_BASE}/exams`, {
    headers: { Cookie: session.cookieHeader },
    data: { maxProblemCount: 1 },
  });
  await expectOk(createResponse, "create active exam");

  const createPayload = await createResponse.json();
  expect(createPayload.exam?.id, "created exam id should be present").toBeTruthy();
  return createPayload.exam;
}

export async function createSession(request: APIRequestContext, prefix: string): Promise<AuthSession> {
  return registerAndLogin(request, `e2e_${prefix}_${Date.now()}_${Math.random()}`, DEFAULT_TEST_PASSWORD);
}

export async function createSessionWithProblem(request: APIRequestContext, prefix: string) {
  const session = await createSession(request, prefix);
  const problem = await seedProblem(request, session, {
    text: "What is 2+2?",
    problemType: "fill-in-the-blank",
    correctAnswer: "4",
  });

  return { session, problem };
}
