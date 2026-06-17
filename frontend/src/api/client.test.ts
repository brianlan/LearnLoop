import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { api, ApiError } from "./client";

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

  it("handles HTTP errors correctly by throwing ApiError", async () => {
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
      expect(err).toBeInstanceOf(ApiError);
      const error = err as ApiError;
      expect(error.code).toBe("VALIDATION_FAILED");
      expect(error.status).toBe(400);
      expect(error.name).toBe("ApiError");
    }
  });

  it("falls back to HTTP status text when error body is missing", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      json: () => Promise.reject(new Error("no json")),
    });
    vi.stubGlobal("fetch", mockFetch);

    const formData = new FormData();
    try {
      await api.postFormData("/test-path", formData);
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const error = err as ApiError;
      expect(error.message).toBe("HTTP 500: Internal Server Error");
      expect(error.status).toBe(500);
      expect(error.code).toBeUndefined();
    }
  });
});
