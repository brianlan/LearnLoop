import { expect, test } from "@playwright/test";

test.describe("Docker Compose Smoke Validation", () => {
  test("backend health check responds with 200 OK and status ok", async ({ request }) => {
    const apiOrigin = process.env.PLAYWRIGHT_API_ORIGIN || "http://127.0.0.1:8000";
    
    // Check both standard health check paths for robustness
    let response = await request.get(`${apiOrigin}/api/v1/health`);
    if (!response.ok()) {
      response = await request.get(`${apiOrigin}/api/health`);
    }
    
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.status).toBe("ok");
  });

  test("frontend healthz endpoint responds with 200 OK", async ({ request }) => {
    // The frontend healthz route should respond with 200 OK.
    // In playwright.compose.config.ts, baseURL is set to http://127.0.0.1:8080
    const response = await request.get("/healthz");
    expect(response.status()).toBe(200);
  });

  test("minimal browser smoke flow: loading the login page and navigating to register", async ({ page }) => {
    // Navigate to the root URL of the running frontend
    await page.goto("/");

    // We should be redirected to the login page (or it should be loaded directly)
    // Check for the Login heading
    const loginHeading = page.getByRole("heading", { name: "Login" });
    await expect(loginHeading).toBeVisible();

    // Verify the presence of unauthenticated login fields
    const usernameInput = page.locator("#username");
    const passwordInput = page.locator("#password");
    await expect(usernameInput).toBeVisible();
    await expect(passwordInput).toBeVisible();

    // Confirm that navigating to the registration page works
    const registerLink = page.getByRole("link", { name: "Register" });
    await expect(registerLink).toBeVisible();
    await registerLink.click();

    // Ensure we reach the registration page and it displays the Register heading
    const registerHeading = page.getByRole("heading", { name: "Register" });
    await expect(registerHeading).toBeVisible();
    await expect(page).toHaveURL(/\/register/);
  });
});
