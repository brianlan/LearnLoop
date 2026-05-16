import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { AppRoutes } from "./App";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import { ProtectedRoute } from "./components/ProtectedRoute";

const mockFetch = vi.fn();
global.fetch = mockFetch;

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
}

type AuthSessionResponse =
  | { authenticated: false }
  | { authenticated: true; user: { id: string; username: string } };

function renderWithRouterAndAuth(
  initialEntries: string[] = ["/"],
  authResponse: AuthSessionResponse = { authenticated: false },
) {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: async () => authResponse,
  });

  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={initialEntries}>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("App", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("renders the login page when not authenticated", async () => {
    renderWithRouterAndAuth(["/login"], { authenticated: false });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Login" })).toBeInTheDocument();
    });
  });

  it("redirects from root to problems when authenticated", async () => {
    renderWithRouterAndAuth(["/"], {
      authenticated: true,
      user: { id: "abc123", username: "test" },
    });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Problems" })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Problems" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ingest" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Exams" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Logout" })).toBeInTheDocument();
  });
});

describe("Route guards", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  function renderWithAuthRoute(
    initialEntries: string[] = ["/"],
    authResponse: AuthSessionResponse = { authenticated: false },
  ) {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => authResponse,
    });

    return render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter initialEntries={initialEntries}>
          <AuthProvider>
            <Routes>
              <Route path="/login" element={<div>Login Page</div>} />
              <Route
                path="/protected"
                element={
                  <ProtectedRoute>
                    <div>Protected Content</div>
                  </ProtectedRoute>
                }
              />
            </Routes>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("redirects to login when accessing protected route without auth", async () => {
    renderWithAuthRoute(["/protected"], { authenticated: false });

    await waitFor(() => {
      expect(screen.getByText("Login Page")).toBeInTheDocument();
    });
  });

  it("allows access to protected route when authenticated", async () => {
    renderWithAuthRoute(["/protected"], {
      authenticated: true,
      user: { id: "abc123", username: "test" },
    });

    await waitFor(() => {
      expect(screen.getByText("Protected Content")).toBeInTheDocument();
    });
  });
});

describe("AuthContext", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("loads user session on mount", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ authenticated: true, user: { id: "abc123", username: "testuser" } }),
    });

    function TestComponent() {
      const { user, isAuthenticated, isLoading } = useAuth();
      if (isLoading) return <div>Loading</div>;
      return (
        <div>
          <span data-testid="auth">{isAuthenticated ? "true" : "false"}</span>
          <span data-testid="username">{user?.username || "none"}</span>
        </div>
      );
    }

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <AuthProvider>
            <TestComponent />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("auth").textContent).toBe("true");
    });
    expect(screen.getByTestId("username").textContent).toBe("testuser");
  });

  it("handles unauthenticated session on mount", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ authenticated: false }),
    });

    function TestComponent() {
      const { user, isAuthenticated, isLoading } = useAuth();
      if (isLoading) return <div>Loading</div>;
      return (
        <div>
          <span data-testid="auth">{isAuthenticated ? "true" : "false"}</span>
          <span data-testid="username">{user?.username || "none"}</span>
        </div>
      );
    }

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <AuthProvider>
            <TestComponent />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("auth").textContent).toBe("false");
    });
    expect(screen.getByTestId("username").textContent).toBe("none");
  });

  it("login function updates auth state", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ authenticated: false }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ user: { id: "newuser-id", username: "newuser" } }),
      });

    function TestComponent() {
      const { user, isAuthenticated, isLoading, login } = useAuth();
      if (isLoading) return <div>Loading</div>;
      return (
        <div>
          <span data-testid="auth">{isAuthenticated ? "true" : "false"}</span>
          <span data-testid="username">{user?.username || "none"}</span>
          <button onClick={() => login("newuser", "password")}>Login</button>
        </div>
      );
    }

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <AuthProvider>
            <TestComponent />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("auth").textContent).toBe("false");
    });

    screen.getByText("Login").click();

    await waitFor(() => {
      expect(screen.getByTestId("auth").textContent).toBe("true");
    });
    expect(screen.getByTestId("username").textContent).toBe("newuser");
  });

  it("logout function clears auth state", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ authenticated: true, user: { id: "abc123", username: "testuser" } }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ok: true }),
      });

    function TestComponent() {
      const { user, isAuthenticated, isLoading, logout } = useAuth();
      if (isLoading) return <div>Loading</div>;
      return (
        <div>
          <span data-testid="auth">{isAuthenticated ? "true" : "false"}</span>
          <span data-testid="username">{user?.username || "none"}</span>
          <button onClick={() => logout()}>Logout</button>
        </div>
      );
    }

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <AuthProvider>
            <TestComponent />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("auth").textContent).toBe("true");
    });

    screen.getByText("Logout").click();

    await waitFor(() => {
      expect(screen.getByTestId("auth").textContent).toBe("false");
    });
    expect(screen.getByTestId("username").textContent).toBe("none");
  });

  it("register function keeps auth state unauthenticated until login", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ authenticated: false }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ user: { id: "registered-id", username: "registered" } }),
      });

    function TestComponent() {
      const { user, isAuthenticated, isLoading, register } = useAuth();
      if (isLoading) return <div>Loading</div>;
      return (
        <div>
          <span data-testid="auth">{isAuthenticated ? "true" : "false"}</span>
          <span data-testid="username">{user?.username || "none"}</span>
          <button onClick={() => register("registered", "password")}>Register</button>
        </div>
      );
    }

    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter>
          <AuthProvider>
            <TestComponent />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("auth").textContent).toBe("false");
    });

    screen.getByText("Register").click();

    await waitFor(() => {
      expect(screen.getByTestId("auth").textContent).toBe("false");
    });
    expect(screen.getByTestId("username").textContent).toBe("none");
  });
});

describe("API client", () => {
  beforeEach(() => {
    mockFetch.mockClear();
  });

  it("makes requests with credentials: include", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ authenticated: true, user: { id: "abc123", username: "test" } }),
    });

    const { api } = await import("./api/client");
    await api.getMe();

    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/auth/me",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("login sends POST with credentials", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ user: { id: "abc123", username: "test" } }),
    });

    const { api } = await import("./api/client");
    await api.login("test", "pass");

    const calls = mockFetch.mock.calls;
    expect(calls[0][0]).toBe("/api/v1/auth/login");
    expect(calls[0][1]).toMatchObject({
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
    });
  });
});
