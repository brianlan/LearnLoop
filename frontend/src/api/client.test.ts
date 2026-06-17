import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { api } from "./client";

describe("API Client postFormData", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("sends a POST request with the correct url, credentials, body, and no content-type header", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ success: true }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const formData = new FormData();
    formData.append("key", "value");

    const result = await api.postFormData("/test-path", formData);

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/test-path", {
      method: "POST",
      credentials: "include",
      body: formData,
    });
    expect(result).toEqual({ success: true });
  });

  it("handles HTTP errors correctly by throwing enriched error", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      statusText: "Bad Request",
      json: () => Promise.resolve({
        error: {
          code: "VALIDATION_FAILED",
          message: "Invalid input provided",
        },
      }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const formData = new FormData();
    await expect(api.postFormData("/test-path", formData)).rejects.toThrow("Invalid input provided");

    try {
      await api.postFormData("/test-path", formData);
    } catch (err) {
      const error = err as Error & { code?: string; status?: number };
      expect(error.code).toBe("VALIDATION_FAILED");
      expect(error.status).toBe(400);
    }
  });
});
