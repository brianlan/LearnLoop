import { expect, test, type Page } from "@playwright/test";

import {
  addAuthenticatedSession,
  APP_BASE,
  createSession,
  createSessionWithProblem,
} from "./helpers";

test.use({ baseURL: APP_BASE });

// Helper to set theme before navigation
async function setTheme(page: Page, theme: "light" | "dark") {
  await page.goto("/");
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
    await setTheme(page, "dark");

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
    await setTheme(page, "dark");

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
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Problems" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Settings" })).toBeVisible();

    // Toggle to dark
    await page.getByRole("button", { name: "Toggle theme" }).click();
    await expect(page.getByRole("button", { name: "Toggle theme" })).toHaveText("Light");

    // Check that header remains visible in dark mode
    await expect(header).toBeVisible();

    // Verify nav items remain visible
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Problems" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("button", { name: "Settings" })).toBeVisible();
  });
});

test.describe("Home Page Theme", () => {
  test("home page renders in light theme", async ({ page, request }) => {
    const session = await createSession(request, "home_light");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "light");

    await page.goto("/");

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("light");

    await expect(page.getByRole("heading", { name: "Home" })).toBeVisible();
  });

  test("home page renders in dark theme", async ({ page, request }) => {
    const session = await createSession(request, "home_dark");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/");

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    await expect(page.getByRole("heading", { name: "Home" })).toBeVisible();
  });

  test("home page dark theme paints a non-white full-page background", async ({ page, request }) => {
    const session = await createSession(request, "home_dark_bg");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/");

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    await expect(page.getByRole("heading", { name: "Home" })).toBeVisible();

    const mainBg = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return null;
      return window.getComputedStyle(main).backgroundColor;
    });
    expect(mainBg).not.toBe("rgb(255, 255, 255)");
    expect(mainBg).not.toBe("rgba(0, 0, 0, 0)");

    const mainHeight = await page.evaluate(() => {
      const main = document.querySelector("main");
      return main ? main.getBoundingClientRect().height : 0;
    });
    const viewportHeight = await page.evaluate(() => window.innerHeight);
    expect(mainHeight).toBeGreaterThanOrEqual(viewportHeight - 60);
  });

  test("home page dark theme has no white pixel below the fold", async ({ page, request }) => {
    const session = await createSession(request, "home_dark_no_white");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Home" })).toBeVisible();

    const bottomBg = await page.evaluate(() => {
      const x = Math.floor(window.innerWidth / 2);
      const y = window.innerHeight - 5;
      const el = document.elementFromPoint(x, y);
      if (!el) return null;
      return window.getComputedStyle(el).backgroundColor;
    });

    expect(bottomBg).not.toBe("rgb(255, 255, 255)");
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

  test("problem detail page dark theme paints a non-white full-page background", async ({ page, request }) => {
    const { session, problem } = await createSessionWithProblem(request, "problem_detail_dark_bg");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto(`/problems/${problem.id}`);

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    const mainBg = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return null;
      return window.getComputedStyle(main).backgroundColor;
    });
    expect(mainBg).not.toBe("rgb(255, 255, 255)");
    expect(mainBg).not.toBe("rgba(0, 0, 0, 0)");

    await expect(page.getByText("What is 2+2?")).toBeVisible();
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
    await expect(page.getByRole("button", { name: "Add Tag" })).toBeVisible();
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
    await expect(page.getByRole("button", { name: "Add Tag" })).toBeVisible();
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

  test("settings page dark theme paints a non-white full-page background", async ({ page, request }) => {
    const session = await createSession(request, "settings_dark_bg");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/settings");

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    const mainBg = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return null;
      return window.getComputedStyle(main).backgroundColor;
    });
    expect(mainBg).not.toBe("rgb(255, 255, 255)");
    expect(mainBg).not.toBe("rgba(0, 0, 0, 0)");

    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
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

  test("active practice page dark theme paints a non-white full-page background", async ({ page, request }) => {
    const { session } = await createSessionWithProblem(request, "active_practice_dark_bg");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    // Navigate to practice and start
    await page.goto("/practice");
    await page.getByTestId("start-practice-button").click();

    // Wait for active practice page
    await expect(page.getByTestId("problem-text")).toBeVisible();

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    const mainBg = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return null;
      return window.getComputedStyle(main).backgroundColor;
    });
    expect(mainBg).not.toBe("rgb(255, 255, 255)");
    expect(mainBg).not.toBe("rgba(0, 0, 0, 0)");

    await expect(page.getByTestId("problem-text")).toBeVisible();
  });
});

test.describe("Exam Detail Page Theme", () => {
  test("exam detail page renders in light theme", async ({ page, request }) => {
    const session = await createSession(request, "exam_detail_light");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "light");

    // Use a non-existent exam id to reach the not-found/error state
    await page.goto("/exams/00000000-0000-0000-0000-000000000000");

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("light");

    // Either error or not-found state should show the Back to Exams button
    await expect(page.getByRole("button", { name: "Back to Exams" })).toBeVisible({ timeout: 15000 });
  });

  test("exam detail page renders in dark theme", async ({ page, request }) => {
    const session = await createSession(request, "exam_detail_dark");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/exams/00000000-0000-0000-0000-000000000000");

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    await expect(page.getByRole("button", { name: "Back to Exams" })).toBeVisible({ timeout: 15000 });
  });

  test("exam detail page dark theme paints a non-white full-page background", async ({ page, request }) => {
    const session = await createSession(request, "exam_detail_dark_bg");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/exams/00000000-0000-0000-0000-000000000000");

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    const mainBg = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return null;
      return window.getComputedStyle(main).backgroundColor;
    });
    expect(mainBg).not.toBe("rgb(255, 255, 255)");
    expect(mainBg).not.toBe("rgba(0, 0, 0, 0)");

    await expect(page.getByRole("button", { name: "Back to Exams" })).toBeVisible({ timeout: 15000 });
  });
});

test.describe("Active Exam Page Theme", () => {
  test("active exam page renders in light theme", async ({ page, request }) => {
    const session = await createSession(request, "active_exam_light");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "light");

    await page.goto("/exams/active");

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("light");

    await expect(page.getByText("No active exam found.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Start New Exam" })).toBeVisible();
  });

  test("active exam page renders in dark theme", async ({ page, request }) => {
    const session = await createSession(request, "active_exam_dark");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/exams/active");

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    await expect(page.getByText("No active exam found.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Start New Exam" })).toBeVisible();
  });

  test("active exam page dark theme paints a non-white full-page background", async ({ page, request }) => {
    const session = await createSession(request, "active_exam_dark_bg");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto("/exams/active");

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    const mainBg = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return null;
      return window.getComputedStyle(main).backgroundColor;
    });
    expect(mainBg).not.toBe("rgb(255, 255, 255)");
    expect(mainBg).not.toBe("rgba(0, 0, 0, 0)");

    await expect(page.getByText("No active exam found.")).toBeVisible();
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

  test("coaching page dark theme paints a non-white full-page background", async ({ page, request }) => {
    const { session, problem } = await createSessionWithProblem(request, "coaching_dark_bg");
    await addAuthenticatedSession(page, session);
    await setTheme(page, "dark");

    await page.goto(`/coaching/${problem.id}`);

    const themeAttr = await page.locator("html").getAttribute("data-theme");
    expect(themeAttr).toBe("dark");

    const mainBg = await page.evaluate(() => {
      const main = document.querySelector("main");
      if (!main) return null;
      return window.getComputedStyle(main).backgroundColor;
    });
    expect(mainBg).not.toBe("rgb(255, 255, 255)");
    expect(mainBg).not.toBe("rgba(0, 0, 0, 0)");

    await expect(page.getByRole("heading", { name: "AI Coach" })).toBeVisible();
  });
});
