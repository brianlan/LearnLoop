import { expect, test } from "@playwright/test";
import { crc32, deflateSync } from "node:zlib";

function pngChunk(type: string, data: Buffer): Buffer {
  const typeBuf = Buffer.from(type, "ascii");
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])) >>> 0, 0);
  return Buffer.concat([len, typeBuf, data, crc]);
}

/** Build a valid 1x1 PNG whose total file size is at least `minBytes`. */
function buildLargePng(minBytes: number): Buffer {
  const sig = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

  const ihdrData = Buffer.alloc(13);
  ihdrData.writeUInt32BE(1, 0); // width
  ihdrData.writeUInt32BE(1, 4); // height
  ihdrData[8] = 8; // bit depth
  ihdrData[9] = 2; // color type: RGB

  // Raw row: filter byte (0) + one black pixel (R, G, B)
  const idatData = deflateSync(Buffer.from([0, 0, 0, 0]));

  const ihdrChunk = pngChunk("IHDR", ihdrData);
  const idatChunk = pngChunk("IDAT", idatData);
  const iendChunk = pngChunk("IEND", Buffer.alloc(0));

  // Pad with a standard tEXt ancillary chunk to reach the target size.
  const fixedSize = sig.length + ihdrChunk.length + idatChunk.length + iendChunk.length;
  const textOverhead = 12 + Buffer.from("Comment\0", "ascii").length; // chunk framing + keyword
  const padLen = Math.max(0, minBytes - fixedSize - textOverhead);
  const textData = Buffer.concat([Buffer.from("Comment\0", "ascii"), Buffer.alloc(padLen, 0x41)]);

  return Buffer.concat([sig, ihdrChunk, pngChunk("tEXt", textData), idatChunk, iendChunk]);
}

type MultipartFile = { filename: string; mimeType: string; buffer: Buffer };

/** Build a raw multipart/form-data body with multiple files under one field name. */
function buildMultipartBody(fieldName: string, files: MultipartFile[]): { body: Buffer; contentType: string } {
  const boundary = `----LLBoundary${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  const parts: Buffer[] = [];
  for (const file of files) {
    parts.push(Buffer.from(`--${boundary}\r\n`));
    parts.push(Buffer.from(
      `Content-Disposition: form-data; name="${fieldName}"; filename="${file.filename}"\r\n` +
      `Content-Type: ${file.mimeType}\r\n\r\n`,
    ));
    parts.push(file.buffer);
    parts.push(Buffer.from("\r\n"));
  }
  parts.push(Buffer.from(`--${boundary}--\r\n`));
  return {
    body: Buffer.concat(parts),
    contentType: `multipart/form-data; boundary=${boundary}`,
  };
}

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

  test("multi-image upload above 1 MiB succeeds through Nginx proxy", async ({ request }) => {
    // All requests use relative URLs resolved against baseURL (http://127.0.0.1:8080),
    // exercising the Nginx proxy rather than the backend directly (port 8000).
    const username = `bulkuser-${Date.now()}`;
    const password = "testpass123";

    // Register and authenticate through the proxy.
    await request.post("/api/v1/auth/register", { data: { username, password } });
    const loginRes = await request.post("/api/v1/auth/login", { data: { username, password } });
    expect(loginRes.ok(), `login failed: ${loginRes.status()}`).toBeTruthy();

    const setCookie = loginRes.headers()["set-cookie"] ?? "";
    const sessionToken = setCookie.match(/ll_session=([^;]+)/)?.[1];
    expect(sessionToken, "session cookie should be present after login").toBeTruthy();
    const cookieHeader = `ll_session=${sessionToken}`;

    // Create an ingestion batch through the proxy.
    const batchRes = await request.post("/api/v1/ingestion-batches", {
      headers: { Cookie: cookieHeader },
    });
    expect(batchRes.status()).toBe(201);
    const batchId = (await batchRes.json()).batch.id;

    // Two valid PNGs whose combined size exceeds 1 MiB (Nginx default limit).
    const pngA = buildLargePng(600 * 1024);
    const pngB = buildLargePng(600 * 1024);
    expect(pngA.length + pngB.length).toBeGreaterThan(1024 * 1024);

    // Build multipart body manually — Playwright's multipart param does not
    // support multiple files under the same field name ("images").
    const { body, contentType } = buildMultipartBody("images", [
      { filename: "a.png", mimeType: "image/png", buffer: pngA },
      { filename: "b.png", mimeType: "image/png", buffer: pngB },
    ]);

    // Upload through the Nginx proxy (port 8080). Returns 413 before the fix.
    const uploadRes = await request.post(`/api/v1/ingestion-batches/${batchId}/images`, {
      headers: { Cookie: cookieHeader, "Content-Type": contentType },
      data: body,
    });

    if (uploadRes.status() !== 201) {
      throw new Error(`Upload failed: ${uploadRes.status()} ${await uploadRes.text()}`);
    }
    const { batch } = await uploadRes.json();
    expect(batch.images).toHaveLength(2);
    expect(batch.images.every((img: { status: string }) => img.status === "uploaded")).toBe(true);
  });
});
