import { expect, test } from "@playwright/test";
import { APP_BASE } from "./helpers";
import { generateIframeHtml } from "../src/components/GraphSandbox.iframe";

test.use({ baseURL: APP_BASE });

test.describe("GraphSandbox Security", () => {
  test("should block outbound fetch requests from within the iframe", async ({ page }) => {
    // Use page.route for deterministic assertion that no request reaches example.com
    let routeHit = false;
    await page.route("**/example.com/**", (route) => {
      routeHit = true;
      route.abort();
    });

    await page.goto("/");

    // Inject iframe using real generateIframeHtml() and the postMessage protocol
    await page.evaluate((html) => {
      return new Promise<void>((resolve) => {
        const iframe = document.createElement("iframe");
        iframe.id = "test-graph-sandbox-iframe";
        iframe.sandbox = "allow-scripts";
        iframe.style.position = "absolute";
        iframe.style.left = "-9999px";
        iframe.style.top = "-9999px";

        const blob = new Blob([html], { type: "text/html" });
        iframe.src = URL.createObjectURL(blob);

        window.addEventListener("message", function handler(event) {
          if (event.data?.type === "ready") {
            window.removeEventListener("message", handler);
            // Send malicious DSL via postMessage (same protocol as GraphSandbox)
            iframe.contentWindow?.postMessage(
              {
                type: "render",
                payload: `
                  fetch('https://example.com/malicious-test-request').catch(() => {});
                  try {
                    const xhr = new XMLHttpRequest();
                    xhr.open('GET', 'https://example.com/malicious-xhr-request', true);
                    xhr.send();
                  } catch (e) {}
                `,
              },
              "*"
            );

            // Wait for any requests to be attempted, then signal completion
            setTimeout(() => resolve(undefined), 1500);
          }
        });

        document.body.appendChild(iframe);
      });
    }, generateIframeHtml());

    // Wait for the iframe test to complete
    await page.waitForTimeout(2000);

    // Verify no requests to example.com were made
    expect(routeHit).toBe(false);
  });

  test("should block access to parent document from within the iframe", async ({ page }) => {
    await page.goto("/");

    // Inject iframe using real generateIframeHtml() with an appended parent-access probe
    const result = await page.evaluate((html) => {
      return new Promise((resolve) => {
        const iframe = document.createElement("iframe");
        iframe.id = "test-parent-access-iframe";
        iframe.sandbox = "allow-scripts";
        iframe.style.position = "absolute";
        iframe.style.left = "-9999px";
        iframe.style.top = "-9999px";

        // Append a script that probes parent access before </body>
        const probeScript = `
          <script>
            (function() {
              let parentAccessResult = { blocked: false };
              try {
                const _ = window.parent.document;
              } catch (e) {
                parentAccessResult = { blocked: true, message: e instanceof Error ? e.message : String(e) };
              }
              parent.postMessage({ type: 'parent-access-result', result: parentAccessResult }, '*');
            })();
          </script>
        `;
        const modifiedHtml = html.replace("</body>", `${probeScript}</body>`);

        const blob = new Blob([modifiedHtml], { type: "text/html" });
        iframe.src = URL.createObjectURL(blob);

        window.addEventListener("message", function handler(event) {
          if (event.data?.type === "parent-access-result") {
            window.removeEventListener("message", handler);
            resolve(event.data.result);
          }
        });

        document.body.appendChild(iframe);
      });
    }, generateIframeHtml());

    // Verify parent access was blocked
    expect((result as any).blocked).toBe(true);
  });
});
