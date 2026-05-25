// API client wrapper for cookie-based authentication
// Uses fetch with credentials: 'include' to send HttpOnly cookies automatically

import type { CoachingConversation } from "@/types/coaching";

const API_BASE = "/api/v1";

export interface User {
  id: string;
  username: string;
}

export interface AuthResponse {
  user: User;
}

export interface MeResponse {
  authenticated: boolean;
  user?: User;
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const error = new Error(
      errorData.error?.message || `HTTP ${response.status}: ${response.statusText}`,
    );
    (error as Error & { code?: string; status?: number }).code = errorData.error?.code;
    (error as Error & { code?: string; status?: number }).status = response.status;
    throw error;
  }
  return response.json() as Promise<T>;
}

/**
 * API client for making authenticated requests.
 * Uses cookie-based auth with credentials: 'include'.
 */
export const api = {
  /**
   * Get the coaching conversation for a problem
   */
  async getCoachingConversation(problemId: string): Promise<CoachingConversation> {
    return this.get<CoachingConversation>(`/coaching/${problemId}/conversation`);
  },

  /**
   * Send a coaching message for a problem
   */
  async sendCoachingMessage(problemId: string, message: string): Promise<CoachingConversation> {
    return this.post<CoachingConversation>(`/coaching/${problemId}/messages`, { message });
  },

  /**
   * Clear the coaching conversation for a problem
   */
  async clearCoachingConversation(problemId: string): Promise<void> {
    return this.delete<void>(`/coaching/${problemId}/conversation`);
  },

  /**
   * Get the current authenticated user session
   */
  async getMe(): Promise<MeResponse> {
    const response = await fetch(`${API_BASE}/auth/me`, {
      credentials: "include",
    });
    return handleResponse<MeResponse>(response);
  },

  /**
   * Login with username and password
   */
  async login(username: string, password: string): Promise<AuthResponse> {
    const response = await fetch(`${API_BASE}/auth/login`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify({ username, password }),
    });
    return handleResponse<AuthResponse>(response);
  },

  /**
   * Register a new user
   */
  async register(username: string, password: string): Promise<AuthResponse> {
    const response = await fetch(`${API_BASE}/auth/register`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify({ username, password }),
    });
    return handleResponse<AuthResponse>(response);
  },

  /**
   * Logout the current user
   */
  async logout(): Promise<{ ok: boolean }> {
    const response = await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
    return handleResponse<{ ok: boolean }>(response);
  },

  /**
   * Verify teacher password
   */
  async verifyTeacherPassword(password: string): Promise<{ ok: boolean }> {
    const response = await fetch(`${API_BASE}/teacher-password/verify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify({ password }),
    });
    return handleResponse<{ ok: boolean }>(response);
  },

  /**
   * Change teacher password
   */
  async changeTeacherPassword(
    currentPassword: string,
    newPassword: string,
    confirmPassword: string
  ): Promise<{ ok: boolean }> {
    const response = await fetch(`${API_BASE}/teacher-password/change`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
        confirm_password: confirmPassword,
      }),
    });
    return handleResponse<{ ok: boolean }>(response);
  },

  /**
   * Get canonical solution generation status for a problem
   */
  async getSolutionStatus(problemId: string): Promise<{ status: string }> {
    return this.get<{ status: string }>(`/problems/${problemId}/solution-status`);
  },

  /**
   * Generic GET request
   */
  async get<T>(path: string): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
      credentials: "include",
    });
    return handleResponse<T>(response);
  },

  /**
   * Generic POST request
   */
  async post<T>(path: string, body: unknown): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify(body),
    });
    return handleResponse<T>(response);
  },

  /**
   * Generic PUT request
   */
  async put<T>(path: string, body: unknown): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify(body),
    });
    return handleResponse<T>(response);
  },

  /**
   * Generic PATCH request
   */
  async patch<T>(path: string, body: unknown): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      credentials: "include",
      body: JSON.stringify(body),
    });
    return handleResponse<T>(response);
  },

  /**
   * Generic DELETE request
   */
  async delete<T>(path: string): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "DELETE",
      credentials: "include",
    });
    return handleResponse<T>(response);
  },
};

export default api;
