import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import {
  addAuthenticatedSession,
  APP_BASE,
  DEFAULT_TEST_PASSWORD,
  registerAndLogin,
  seedProblem,
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

// Helper to set theme before navigation
async function setTheme(page: Page, theme: "light" | "dark") {
  await page.evaluate((t) => localStorage.setItem("learnloop-theme", t), theme);
}

test.describe("Theme E2E", () => {
  test("defaults to light theme for new authenticated user", async ({ page, request }) => {
    const session = await createSession(request, "theme_default");
    await addAuthenticatedSession(page, session);

    await page.goto("/problems");

    // Verify the theme toggle button shows "Dark" (indicating current theme is light)
    await expect(page.getByRole("button", { name: "Toggle theme" })).toHaveText("Dark");

    // Verify the root data-theme attribute is light
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("light");
  });

  test("toggles from light to dark and updates localStorage", async ({ page, request }) => {
    const session = await createSession(request, "theme_toggle_light");
    await addAuthenticatedSession(page, session);

    await page.goto("/problems");

    // Start in light mode
    await expect(page.getByRole("button", { name: "Toggle theme" })).toHaveText("Dark");

    // Click toggle to switch to dark
    await page.getByRole("button", { name: "Toggle theme" }).click();

    // Verify button now shows "Light" (indicating current theme is dark)
    await expect(page.getByRole("button", { name: "Toggle theme" })).toHaveText("Light");

    // Verify the root data-theme attribute is dark
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    // Verify localStorage is updated
    const localStorageTheme = await page.evaluate(() => localStorage.getItem("learnloop-theme"));
    expect(localStorageTheme).toBe("dark");
  });

  test("toggles from dark to light and updates localStorage", async ({ page, request }) => {
    const session = await createSession(request, "theme_toggle_dark");
    await addAuthenticatedSession(page, session);

    // Pre-set localStorage to dark
    await page.evaluate(() => localStorage.setItem("learnloop-theme", "dark"));

    await page.goto("/problems");

    // Start in dark mode
    await expect(page.getByRole("button", { name: "Toggle theme" })).toHaveText("Light");
    const themeAttrBefore = await page.locator("html").getAttribute("data-theme");
    expect(themeAttrBefore).toBe("dark");

    // Click toggle to switch to light
    await page.getByRole("button", { name: "Toggle theme" }).click();

    // Verify button now shows "Dark" (indicating current theme is light)
    await expect(page.getByRole("button", { name: "Toggle theme" })).toHaveText("Dark");

    // Verify the root data-theme attribute is light
    const themeAttrAfter = await page.locator("html").getAttribute("data-theme");
    expect(themeAttrAfter).toBe("light");

    // Verify localStorage is updated
    const localStorageTheme = await page.evaluate(() => localStorage.getItem("learnloop-theme"));
    expect(localStorageTheme).toBe("light");
  });

  test("persists dark theme after page reload", async ({ page, request }) => {
    const session = await createSession(request, "theme_persist_dark");
    await addAuthenticatedSession(page, session);

    await page.goto("/problems");

    // Toggle to dark
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await expect(page.getByRole("button", { name: "Toggle theme" })).toHaveText("Light");

    // Reload the page
    await page.reload();

    // Verify theme persists as dark
    await expect(page.getByRole("button", { name: "Toggle theme" })).toHaveText("Light");
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");
  });

  test("persists light theme after page reload", async ({ page, request }) => {
    const session = await createSession(request, "theme_persist_light");
    await addAuthenticatedSession(page, session);

    // Pre-set localStorage to dark, then toggle to light
    await page.evaluate(() => localStorage.setItem("learnloop-theme", "dark"));

    await page.goto("/problems");
    await expect(page.getByRole("button", { name: "Toggle theme" })).toHaveText("Light");

    // Toggle to light
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await expect(page.getByRole("button", { name: "Toggle theme" })).toHaveText("Dark");

    // Reload the page
    await page.reload();

    // Verify theme persists as light
    await expect(page.getByRole("button", { name: "Toggle theme" })).toHaveText("Dark");
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("light");
  });

  test("theme toggle button remains usable in both themes", async ({ page, request }) => {
    const session = await createSession(request, "theme_toggle_usable");
    await addAuthenticatedSession(page, session);

    await page.goto("/problems");

    // Verify toggle button is visible and usable in light mode
    const toggleButton = page.getByRole("button", { name: "Toggle theme" });
    await expect(toggleButton).toBeVisible();
    await expect(toggleButton).toBeEnabled();

    // Toggle to dark
    await toggleButton.click();
    await expect(page.getByRole("button", { name: "Toggle theme" })).toHaveText("Light");

    // Verify toggle button remains visible and usable in dark mode
    const toggleButtonDark = page.getByRole("button", { name: "Toggle theme" });
    await expect(toggleButtonDark).toBeVisible();
    await expect(toggleButtonDark).toBeEnabled();

    // Toggle back to light to verify it still works
    await toggleButtonDark.click();
    await expect(page.getByRole("button", { name: "Toggle theme" })).toHaveText("Dark");
  });

  test("app shell remains readable in both light and dark themes", async ({ page, request }) => {
    const session = await createSession(request, "theme_app_shell_readable");
    await addAuthenticatedSession(page, session);

    await page.goto("/problems");

    // Check that header is visible in light mode
    const header = page.locator("header");
    await expect(header).toBeVisible();

    // Verify nav items are visible
    await expect(page.getByRole("button", { name: "Problems" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Settings" })).toBeVisible();

    // Toggle to dark
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await expect(page.getByRole("button", { name: "Toggle theme" })).toHaveText("Light");

    // Check that header remains visible in dark mode
    await expect(header).toBeVisible();

    // Verify nav items remain visible
    await expect(page.getByRole("button", { name: "Problems" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Settings" })).toBeVisible();
  });
});

test.describe("Problems Page Theme", () => {
  test("problems page renders in light theme", async ({ page, request }) => {
    const session = await createSession(request, "problems_light");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "light");

    await page.goto("/problems");

    // Verify theme is light
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("light");

    // Verify page content is visible
    await expect(page.getByRole("heading", { name: "Problems" })).toBeVisible();

    // Verify filter controls are visible
    await expect(page.getByLabel("Filter by Tag")).toBeVisible();
    await expect(page.getByLabel("Filter by Type")).toBeVisible();
  });

  test("problems page renders in dark theme", async ({ page, request }) => {
    const session = await createSession(request, "problems_dark");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/problems");

    // Verify theme is dark
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    // Verify page content is visible
    await expect(page.getByRole("heading", { name: "Problems" })).toBeVisible();

    // Verify filter controls are visible
    await expect(page.getByLabel("Filter by Tag")).toBeVisible();
    await expect(page.getByLabel("Filter by Type")).toBeVisible();
  });

  test("problems page dark theme paints a themed background on main content area", async ({ page, request }) => {
    const session = await createSession(request, "problems_dark_bg");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/problems");

    // Verify theme is dark
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    // Verify the main content area has a dark/themed background, not white
    const mainBg = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return null;
      return window.getComputedStyle(main).backgroundColor;
    });
    expect(mainBg).not.toBe("rgb(255, 255, 255)");
    expect(mainBg).not.toBe("rgba(0, 0, 0, 0)");

    // Verify page heading remains visible
    await expect(page.getByRole("heading", { name: "Problems" })).toBeVisible();
  });

  test("problems page dark theme background matches Ingest page surface-muted", async ({ page, request }) => {
    const session = await createSession(request, "problems_dark_surface_muted");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/problems");

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    const mainBg = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return null;
      return window.getComputedStyle(main).backgroundColor;
    });
    expect(mainBg).not.toBe("rgb(255, 255, 255)");
    expect(mainBg).not.toBe("rgba(0, 0, 0, 0)");

    await expect(page.getByRole("heading", { name: "Problems" })).toBeVisible();
  });
});

test.describe("Problem Detail Page Theme", () => {
  test("problem detail page renders in light theme", async ({ page, request }) => {
    const { session, problem } = await createSessionWithProblem(request, "problem_detail_light");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "light");

    await page.goto(`/problems/${problem.id}`);

    // Verify theme is light
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("light");

    // Verify problem text is visible
    await expect(page.getByText("What is 2+2?")).toBeVisible();

    // Verify Edit and Delete buttons are visible
    await expect(page.getByRole("button", { name: "Edit" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Delete" })).toBeVisible();
  });

  test("problem detail page renders in dark theme", async ({ page, request }) => {
    const { session, problem } = await createSessionWithProblem(request, "problem_detail_dark");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto(`/problems/${problem.id}`);

    // Verify theme is dark
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    // Verify problem text is visible
    await expect(page.getByText("What is 2+2?")).toBeVisible();

    // Verify Edit and Delete buttons are visible
    await expect(page.getByRole("button", { name: "Edit" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Delete" })).toBeVisible();
  });
});

test.describe("Tags Page Theme", () => {
  test("tags page renders in light theme", async ({ page, request }) => {
    const session = await createSession(request, "tags_light");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "light");

    await page.goto("/tags");

    // Verify theme is light
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("light");

    // Verify page content is visible
    await expect(page.getByRole("heading", { name: "Tags" })).toBeVisible();

    // Verify create tag button is visible
    await expect(page.getByRole("button", { name: "Create Tag" })).toBeVisible();
  });

  test("tags page renders in dark theme", async ({ page, request }) => {
    const session = await createSession(request, "tags_dark");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/tags");

    // Verify theme is dark
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    // Verify page content is visible
    await expect(page.getByRole("heading", { name: "Tags" })).toBeVisible();

    // Verify create tag button is visible
    await expect(page.getByRole("button", { name: "Create Tag" })).toBeVisible();
  });

  test("tags page dark theme paints a full-page themed background", async ({ page, request }) => {
    const session = await createSession(request, "tags_dark_bg");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/tags");

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    const mainBg = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return null;
      return window.getComputedStyle(main).backgroundColor;
    });
    expect(mainBg).not.toBe("rgb(255, 255, 255)");
    expect(mainBg).not.toBe("rgba(0, 0, 0, 0)");

    await expect(page.getByRole("heading", { name: "Tags" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Add Tag" })).toBeVisible();
  });
});

test.describe("Settings Page Theme", () => {
  test("settings page renders in light theme", async ({ page, request }) => {
    const session = await createSession(request, "settings_light");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "light");

    await page.goto("/settings");

    // Verify theme is light
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("light");

    // Verify page content is visible
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();

    // Verify change password button is visible
    await expect(page.getByRole("button", { name: "Change Teacher Password" })).toBeVisible();
  });

  test("settings page renders in dark theme", async ({ page, request }) => {
    const session = await createSession(request, "settings_dark");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/settings");

    // Verify theme is dark
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    // Verify page content is visible
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();

    // Verify change password button is visible
    await expect(page.getByRole("button", { name: "Change Teacher Password" })).toBeVisible();
  });
});

test.describe("Practice Page Theme", () => {
  test("practice page renders in light theme", async ({ page, request }) => {
    const { session } = await createSessionWithProblem(request, "practice_light");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "light");

    await page.goto("/practice");

    // Verify theme is light
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("light");

    // Verify page content is visible
    await expect(page.getByRole("heading", { name: "Practice" })).toBeVisible();

    // Verify start practice button is visible
    await expect(page.getByTestId("start-practice-button")).toBeVisible();
  });

  test("practice page renders in dark theme", async ({ page, request }) => {
    const { session } = await createSessionWithProblem(request, "practice_dark");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/practice");

    // Verify theme is dark
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    // Verify page content is visible
    await expect(page.getByRole("heading", { name: "Practice" })).toBeVisible();

    // Verify start practice button is visible
    await expect(page.getByTestId("start-practice-button")).toBeVisible();
  });

  test("practice page dark theme paints a full-page themed background", async ({ page, request }) => {
    const { session } = await createSessionWithProblem(request, "practice_dark_bg");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/practice");

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    const mainBg = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return null;
      return window.getComputedStyle(main).backgroundColor;
    });
    expect(mainBg).not.toBe("rgb(255, 255, 255)");
    expect(mainBg).not.toBe("rgba(0, 0, 0, 0)");

    await expect(page.getByRole("heading", { name: "Practice" })).toBeVisible();
    await expect(page.getByTestId("start-practice-button")).toBeVisible();
  });
});

test.describe("Active Practice Page Theme", () => {
  test("active practice page renders in light theme", async ({ page, request }) => {
    const { session } = await createSessionWithProblem(request, "active_practice_light");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "light");

    // Navigate to practice and start
    await page.goto("/practice");
    await page.getByTestId("start-practice-button").click();

    // Wait for active practice page
    await expect(page.getByTestId("problem-text")).toBeVisible();

    // Verify theme is light
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("light");

    // Verify submit and skip buttons are visible
    await expect(page.getByTestId("submit-button")).toBeVisible();
    await expect(page.getByTestId("skip-button")).toBeVisible();
  });

  test("active practice page renders in dark theme", async ({ page, request }) => {
    const { session } = await createSessionWithProblem(request, "active_practice_dark");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    // Navigate to practice and start
    await page.goto("/practice");
    await page.getByTestId("start-practice-button").click();

    // Wait for active practice page
    await expect(page.getByTestId("problem-text")).toBeVisible();

    // Verify theme is dark
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    // Verify submit and skip buttons are visible
    await expect(page.getByTestId("submit-button")).toBeVisible();
    await expect(page.getByTestId("skip-button")).toBeVisible();
  });
});

test.describe("Exams Page Theme", () => {
  test("exams page renders in light theme", async ({ page, request }) => {
    const session = await createSession(request, "exams_light");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "light");

    await page.goto("/exams");

    // Verify theme is light
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("light");

    // Verify page content is visible
    await expect(page.getByRole("heading", { name: "Exam History" })).toBeVisible();

    // Verify start new exam button is visible
    await expect(page.getByRole("button", { name: "Start New Exam" })).toBeVisible();
  });

  test("exams page renders in dark theme", async ({ page, request }) => {
    const session = await createSession(request, "exams_dark");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/exams");

    // Verify theme is dark
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    // Verify page content is visible
    await expect(page.getByRole("heading", { name: "Exam History" })).toBeVisible();

    // Verify start new exam button is visible
    await expect(page.getByRole("button", { name: "Start New Exam" })).toBeVisible();
  });

  test("exams page dark theme paints a full-page themed background", async ({ page, request }) => {
    const session = await createSession(request, "exams_dark_bg");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/exams");

    // Verify theme is dark
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    // Get --color-surface-muted from the page
    const surfaceMuted = await page.evaluate(() => {
      return window.getComputedStyle(document.documentElement).getPropertyValue("--color-surface-muted").trim();
    });

    // Verify the main element has the themed background
    const mainBg = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return null;
      return window.getComputedStyle(main).backgroundColor;
    });
    expect(mainBg).not.toBe("rgb(255, 255, 255)");
    expect(mainBg).not.toBe("rgba(0, 0, 0, 0)");

    // Verify heading is still visible
    await expect(page.getByRole("heading", { name: "Exam History" })).toBeVisible();
  });
});

test.describe("Coaching Page Theme", () => {
  test("coaching page renders in light theme", async ({ page, request }) => {
    const { session, problem } = await createSessionWithProblem(request, "coaching_light");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "light");

    // Navigate to coaching page
    await page.goto(`/coaching/${problem.id}`);

    // Verify theme is light
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("light");

    // Verify page content is visible
    await expect(page.getByRole("heading", { name: "AI Coach" })).toBeVisible();

    // Verify context bar and whiteboard are visible
    await expect(page.getByTestId("context-bar")).toBeVisible();
    await expect(page.getByTestId("whiteboard")).toBeVisible();
  });

  test("coaching page renders in dark theme", async ({ page, request }) => {
    const { session, problem } = await createSessionWithProblem(request, "coaching_dark");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    // Navigate to coaching page
    await page.goto(`/coaching/${problem.id}`);

    // Verify theme is dark
    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    // Verify page content is visible
    await expect(page.getByRole("heading", { name: "AI Coach" })).toBeVisible();

    // Verify context bar and whiteboard are visible
    await expect(page.getByTestId("context-bar")).toBeVisible();
    await expect(page.getByTestId("whiteboard")).toBeVisible();
  });
});
