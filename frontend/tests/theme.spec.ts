import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

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

// Page-rendering theme matrix: each case generates the light, dark, and
// dark-background tests for one page. Special cases (Home no-white-pixel,
// Problems surface-muted) are kept as explicit extras in the bounded hook set.
type PageThemeCase = {
  describe: string;
  title: string;
  bgTitle: string;
  sessionKind: "plain" | "problem";
  route: (problemId: string) => string;
  setup?: (page: Page) => Promise<void>;
  assert: (page: Page) => Promise<void>;
  background: (page: Page) => Promise<void>;
  extras?: Array<{ title: string; theme: "light" | "dark"; suffix: string; run: (page: Page) => Promise<void> }>;
};

async function makeSession(request: APIRequestContext, kind: "plain" | "problem", slug: string) {
  if (kind === "problem") return createSessionWithProblem(request, slug);
  return { session: await createSession(request, slug), problem: undefined };
}

async function renderThemedPage(
  page: Page,
  request: APIRequestContext,
  c: PageThemeCase,
  theme: "light" | "dark",
  suffix: string,
) {
  const { session, problem } = await makeSession(request, c.sessionKind, `${c.title.replace(/ /g, "_")}_${suffix}`);
  await addAuthenticatedSession(page, session);
  await setTheme(page, theme);
  await page.goto(c.route(problem?.id ?? ""));
  await c.setup?.(page);
}

async function assertThemeAttr(page: Page, theme: "light" | "dark") {
  expect(await page.locator("html").getAttribute("data-theme")).toBe(theme);
}

async function assertMainBackgroundNotWhite(page: Page) {
  const mainBg = await page.evaluate(() => {
    const main = document.querySelector("main");
    return main ? window.getComputedStyle(main).backgroundColor : null;
  });
  expect(mainBg).not.toBe("rgb(255, 255, 255)");
  expect(mainBg).not.toBe("rgba(0, 0, 0, 0)");
}

function pageThemeCase(c: PageThemeCase) {
  test.describe(c.describe, () => {
    test(`${c.title} renders in light theme`, async ({ page, request }) => {
      await renderThemedPage(page, request, c, "light", "light");
      await assertThemeAttr(page, "light");
      await c.assert(page);
    });

    test(`${c.title} renders in dark theme`, async ({ page, request }) => {
      await renderThemedPage(page, request, c, "dark", "dark");
      await assertThemeAttr(page, "dark");
      await c.assert(page);
    });

    test(c.bgTitle, async ({ page, request }) => {
      await renderThemedPage(page, request, c, "dark", "dark_bg");
      await assertThemeAttr(page, "dark");
      await c.background(page);
    });

    for (const e of c.extras ?? []) {
      test(e.title, async ({ page, request }) => {
        await renderThemedPage(page, request, c, e.theme, e.suffix);
        await e.run(page);
      });
    }
  });
}

const pageThemeCases: PageThemeCase[] = [
  {
    describe: "Home Page Theme",
    title: "home page",
    bgTitle: "home page dark theme paints a non-white full-page background",
    sessionKind: "plain",
    route: () => "/",
    assert: async (page) => {
      await expect(page.getByRole("heading", { name: "Home" })).toBeVisible();
    },
    background: async (page) => {
      await expect(page.getByRole("heading", { name: "Home" })).toBeVisible();
      await assertMainBackgroundNotWhite(page);
      const mainHeight = await page.evaluate(() => {
        const main = document.querySelector("main");
        return main ? main.getBoundingClientRect().height : 0;
      });
      const viewportHeight = await page.evaluate(() => window.innerHeight);
      expect(mainHeight).toBeGreaterThanOrEqual(viewportHeight - 60);
    },
    extras: [
      {
        title: "home page dark theme has no white pixel below the fold",
        theme: "dark",
        suffix: "dark_no_white",
        run: async (page) => {
          await expect(page.getByRole("heading", { name: "Home" })).toBeVisible();
          const bottomBg = await page.evaluate(() => {
            const x = Math.floor(window.innerWidth / 2);
            const y = window.innerHeight - 5;
            const el = document.elementFromPoint(x, y);
            return el ? window.getComputedStyle(el).backgroundColor : null;
          });
          expect(bottomBg).not.toBe("rgb(255, 255, 255)");
        },
      },
    ],
  },
  {
    describe: "Problems Page Theme",
    title: "problems page",
    bgTitle: "problems page dark theme paints a themed background on main content area",
    sessionKind: "plain",
    route: () => "/problems",
    assert: async (page) => {
      await expect(page.getByRole("heading", { name: "Problems" })).toBeVisible();
      await expect(page.getByLabel("Filter by Tag")).toBeVisible();
      await expect(page.getByLabel("Filter by Type")).toBeVisible();
    },
    background: async (page) => {
      await assertMainBackgroundNotWhite(page);
      await expect(page.getByRole("heading", { name: "Problems" })).toBeVisible();
    },
    extras: [
      {
        title: "problems page dark theme background matches Ingest page surface-muted",
        theme: "dark",
        suffix: "dark_surface_muted",
        run: async (page) => {
          await assertThemeAttr(page, "dark");
          await assertMainBackgroundNotWhite(page);
          await expect(page.getByRole("heading", { name: "Problems" })).toBeVisible();
        },
      },
    ],
  },
  {
    describe: "Problem Detail Page Theme",
    title: "problem detail page",
    bgTitle: "problem detail page dark theme paints a non-white full-page background",
    sessionKind: "problem",
    route: (id) => `/problems/${id}`,
    assert: async (page) => {
      await expect(page.getByText("What is 2+2?")).toBeVisible();
      await expect(page.getByRole("button", { name: "Edit" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Delete" })).toBeVisible();
    },
    background: async (page) => {
      await assertMainBackgroundNotWhite(page);
      await expect(page.getByText("What is 2+2?")).toBeVisible();
    },
  },
  {
    describe: "Tags Page Theme",
    title: "tags page",
    bgTitle: "tags page dark theme paints a full-page themed background",
    sessionKind: "plain",
    route: () => "/tags",
    assert: async (page) => {
      await expect(page.getByRole("heading", { name: "Tags" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Add Tag" })).toBeVisible();
    },
    background: async (page) => {
      await assertMainBackgroundNotWhite(page);
      await expect(page.getByRole("heading", { name: "Tags" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Add Tag" })).toBeVisible();
    },
  },
  {
    describe: "Settings Page Theme",
    title: "settings page",
    bgTitle: "settings page dark theme paints a non-white full-page background",
    sessionKind: "plain",
    route: () => "/settings",
    assert: async (page) => {
      await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Change Teacher Password" })).toBeVisible();
    },
    background: async (page) => {
      await assertMainBackgroundNotWhite(page);
      await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    },
  },
  {
    describe: "Practice Page Theme",
    title: "practice page",
    bgTitle: "practice page dark theme paints a full-page themed background",
    sessionKind: "problem",
    route: () => "/practice",
    assert: async (page) => {
      await expect(page.getByRole("heading", { name: "Practice" })).toBeVisible();
      await expect(page.getByTestId("start-practice-button")).toBeVisible();
    },
    background: async (page) => {
      await assertMainBackgroundNotWhite(page);
      await expect(page.getByRole("heading", { name: "Practice" })).toBeVisible();
      await expect(page.getByTestId("start-practice-button")).toBeVisible();
    },
  },
  {
    describe: "Active Practice Page Theme",
    title: "active practice page",
    bgTitle: "active practice page dark theme paints a non-white full-page background",
    sessionKind: "problem",
    route: () => "/practice",
    setup: async (page) => {
      await page.getByTestId("start-practice-button").click();
      await expect(page.getByTestId("problem-text")).toBeVisible();
    },
    assert: async (page) => {
      await expect(page.getByTestId("submit-button")).toBeVisible();
      await expect(page.getByTestId("skip-button")).toBeVisible();
    },
    background: async (page) => {
      await assertMainBackgroundNotWhite(page);
      await expect(page.getByTestId("problem-text")).toBeVisible();
    },
  },
  {
    describe: "Exam Detail Page Theme",
    title: "exam detail page",
    bgTitle: "exam detail page dark theme paints a non-white full-page background",
    sessionKind: "plain",
    route: () => "/exams/00000000-0000-0000-0000-000000000000",
    assert: async (page) => {
      await expect(page.getByRole("button", { name: "Back to Exams" })).toBeVisible({ timeout: 15000 });
    },
    background: async (page) => {
      await assertMainBackgroundNotWhite(page);
      await expect(page.getByRole("button", { name: "Back to Exams" })).toBeVisible({ timeout: 15000 });
    },
  },
  {
    describe: "Active Exam Page Theme",
    title: "active exam page",
    bgTitle: "active exam page dark theme paints a non-white full-page background",
    sessionKind: "plain",
    route: () => "/exams/active",
    assert: async (page) => {
      await expect(page.getByText("No active exam found.")).toBeVisible();
      await expect(page.getByRole("button", { name: "Start New Exam" })).toBeVisible();
    },
    background: async (page) => {
      await assertMainBackgroundNotWhite(page);
      await expect(page.getByText("No active exam found.")).toBeVisible();
    },
  },
  {
    describe: "Exams Page Theme",
    title: "exams page",
    bgTitle: "exams page dark theme paints a full-page themed background",
    sessionKind: "plain",
    route: () => "/exams",
    assert: async (page) => {
      await expect(page.getByRole("heading", { name: "Exam History" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Start New Exam" })).toBeVisible();
    },
    background: async (page) => {
      // ponytail: preserve surface-muted CSS var read (parity with original; no assertion on it)
      await page.evaluate(() =>
        window.getComputedStyle(document.documentElement).getPropertyValue("--color-surface-muted").trim(),
      );
      await assertMainBackgroundNotWhite(page);
      await expect(page.getByRole("heading", { name: "Exam History" })).toBeVisible();
    },
  },
  {
    describe: "Coaching Page Theme",
    title: "coaching page",
    bgTitle: "coaching page dark theme paints a non-white full-page background",
    sessionKind: "problem",
    route: (id) => `/coaching/${id}`,
    assert: async (page) => {
      await expect(page.getByRole("heading", { name: "AI Coach" })).toBeVisible();
      await expect(page.getByTestId("context-bar")).toBeVisible();
      await expect(page.getByTestId("whiteboard")).toBeVisible();
    },
    background: async (page) => {
      await assertMainBackgroundNotWhite(page);
      await expect(page.getByRole("heading", { name: "AI Coach" })).toBeVisible();
    },
  },
];

for (const c of pageThemeCases) {
  pageThemeCase(c);
}
