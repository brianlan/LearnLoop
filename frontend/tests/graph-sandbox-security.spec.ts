import { expect, test, type Page } from "@playwright/test";
import { APP_BASE } from "./helpers";

test.use({ baseURL: APP_BASE });

test.describe("GraphSandbox Security", () => {
  let testPage: Page;

  test.beforeEach(async ({ page }) => {
    testPage = page;
  });

  test("should block outbound fetch requests from within the iframe", async ({ page }) => {
    // Track all network requests
    const requestedUrls: string[] = [];
    page.on("request", (request) => {
      requestedUrls.push(request.url());
    });

    // Navigate to the app
    await page.goto("/");

    // Inject a test iframe with the same CSP and sandbox as GraphSandbox
    await page.evaluate(() => {
      return new Promise((resolve) => {
        const iframe = document.createElement("iframe");
        iframe.id = "test-graph-sandbox-iframe";
        iframe.sandbox = "allow-scripts";
        iframe.style.position = "absolute";
        iframe.style.left = "-9999px";
        iframe.style.top = "-9999px";

        // Same CSP as GraphSandbox
        const iframeHtml = `
          <!DOCTYPE html>
          <html>
          <head>
            <meta charset="UTF-8">
            <meta http-equiv="Content-Security-Policy" content="
              default-src 'none';
              script-src 'unsafe-eval' 'unsafe-inline' https://cdn.jsdelivr.net/npm/jsxgraph@1.10.0/distrib/jsxgraphcore.js;
              style-src 'unsafe-inline';
              img-src data:;
              connect-src 'none';
              font-src 'none';
              frame-src 'none';
              object-src 'none';
              media-src 'none';
              base-uri 'none';
              form-action 'none';
            ">
            <script src="https://cdn.jsdelivr.net/npm/jsxgraph@1.10.0/distrib/jsxgraphcore.js"></script>
            <script>
              // Try to make a fetch request
              fetch('https://example.com/malicious-test-request')
                .catch(() => {});

              // Also try XMLHttpRequest
              try {
                const xhr = new XMLHttpRequest();
                xhr.open('GET', 'https://example.com/malicious-xhr-request', true);
                xhr.send();
              } catch (e) {}

              // Notify parent that we've attempted the requests
              parent.postMessage({ type: 'test-done' }, '*');
            </script>
          </head>
          <body>
            <div id="jxgbox"></div>
          </body>
          </html>
        `;

        const blob = new Blob([iframeHtml], { type: "text/html" });
        const blobUrl = URL.createObjectURL(blob);
        iframe.src = blobUrl;

        // Wait for the iframe to send the test-done message
        window.addEventListener("message", function handler(event) {
          if (event.data?.type === "test-done") {
            window.removeEventListener("message", handler);
            resolve(undefined);
          }
        });

        document.body.appendChild(iframe);
      });
    });

    // Wait a bit for any requests to go through
    await page.waitForTimeout(1000);

    // Verify no requests to example.com were made
    const hasExampleComRequest = requestedUrls.some(url =>
      url.includes("example.com")
    );
    expect(hasExampleComRequest).toBe(false);
  });

  test("should block access to parent document from within the iframe", async ({ page }) => {
    // Navigate to the app
    await page.goto("/");

    // Inject a test iframe to check parent access
    const result = await page.evaluate(() => {
      return new Promise((resolve) => {
        const iframe = document.createElement("iframe");
        iframe.id = "test-parent-access-iframe";
        iframe.sandbox = "allow-scripts";
        iframe.style.position = "absolute";
        iframe.style.left = "-9999px";
        iframe.style.top = "-9999px";

        const iframeHtml = `
          <!DOCTYPE html>
          <html>
          <head>
            <meta charset="UTF-8">
            <script>
              let parentAccessResult: { blocked: boolean; message?: string } = { blocked: false };
              try {
                // Try to access parent.document
                const _ = window.parent.document;
                parentAccessResult = { blocked: false };
              } catch (e) {
                parentAccessResult = { blocked: true, message: e instanceof Error ? e.message : String(e) };
              }

              // Notify parent of the result
              parent.postMessage({ type: 'parent-access-result', result: parentAccessResult }, '*');
            </script>
          </head>
          <body></body>
          </html>
        `;

        const blob = new Blob([iframeHtml], { type: "text/html" });
        const blobUrl = URL.createObjectURL(blob);
        iframe.src = blobUrl;

        window.addEventListener("message", function handler(event) {
          if (event.data?.type === "parent-access-result") {
            window.removeEventListener("message", handler);
            resolve(event.data.result);
          }
        });

        document.body.appendChild(iframe);
      });
    });

    // Verify parent access was blocked
    expect((result as any).blocked).toBe(true);
  });
});
