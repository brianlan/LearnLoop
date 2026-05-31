import { expect, test, type APIRequestContext } from "@playwright/test";

import {
  addAuthenticatedSession,
  APP_BASE,
  DEFAULT_TEST_PASSWORD,
  registerAndLogin,
  type AuthSession,
} from "./helpers";

test.use({ baseURL: APP_BASE });

async function createSession(request: APIRequestContext, prefix: string): Promise<AuthSession> {
  return registerAndLogin(request, `e2e_${prefix}_${Date.now()}_${Math.random()}`, DEFAULT_TEST_PASSWORD);
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