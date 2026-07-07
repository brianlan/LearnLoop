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

  it("falls back to HTTP status text when json resolves to null", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      json: () => Promise.resolve(null),
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

// Characterization tests for auth-related client methods. These pin the exact
// request shape (URL, method, headers, credentials, body) so the methods can be
// safely delegated to the generic this.get/this.post helpers without changing
// runtime behavior. logout is intentionally NOT delegated because its request
// shape (no Content-Type header, no body) differs from this.post.
describe("API Client auth methods", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("getMe sends a request to /auth/me with credentials and returns the parsed response", async () => {
    const meResponse = { authenticated: true, user: { id: "u1", username: "alice" } };
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(meResponse),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await api.getMe();

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/auth/me", {
      credentials: "include",
    });
    expect(result).toEqual(meResponse);
  });

  it("login sends a POST to /auth/login with JSON content type, credentials, and { username, password } body", async () => {
    const authResponse = { user: { id: "u1", username: "alice" } };
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(authResponse),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await api.login("alice", "secret");

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ username: "alice", password: "secret" }),
    });
    expect(result).toEqual(authResponse);
  });

  it("register sends a POST to /auth/register with JSON content type, credentials, and { username, password } body", async () => {
    const authResponse = { user: { id: "u2", username: "bob" } };
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(authResponse),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await api.register("bob", "password");

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ username: "bob", password: "password" }),
    });
    expect(result).toEqual(authResponse);
  });

  it("verifyTeacherPassword sends a POST to /teacher-password/verify with JSON content type, credentials, and { password } body", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await api.verifyTeacherPassword("teacher-pw");

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/teacher-password/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ password: "teacher-pw" }),
    });
    expect(result).toEqual({ ok: true });
  });

  it("changeTeacherPassword sends a POST to /teacher-password/change with JSON content type, credentials, and snake_case password body keys", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await api.changeTeacherPassword("current", "new", "confirm");

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/teacher-password/change", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        current_password: "current",
        new_password: "new",
        confirm_password: "confirm",
      }),
    });
    expect(result).toEqual({ ok: true });
  });

  it("logout sends a POST to /auth/logout with credentials and no content-type header or body", async () => {
    // Characterization: logout is intentionally excluded from consolidation.
    // Its request shape has no Content-Type header and no body, so it cannot
    // delegate to this.post without changing its request shape.
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await api.logout();

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/auth/logout", {
      method: "POST",
      credentials: "include",
    });
    expect(result).toEqual({ ok: true });
  });
});
