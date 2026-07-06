// API client wrapper for cookie-based authentication
// Uses fetch with credentials: 'include' to send HttpOnly cookies automatically

import type { CoachingConversation } from "@/types/coaching";

const API_BASE = "/api/v1";

export class ApiError extends Error {
  code?: string;
  status: number;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

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
    const rawData = await response.json().catch(() => ({}));
    const errorData = rawData && typeof rawData === "object" ? rawData : {};
    throw new ApiError(
      errorData.error?.message || `HTTP ${response.status}: ${response.statusText}`,
      response.status,
      errorData.error?.code,
    );
  }
  if (response.status === 204) {
    return undefined as unknown as T;
  }
  return response.json() as Promise<T>;
}

/**
 * API client for making authenticated requests.
 * Uses cookie-based auth with credentials: 'include'.
 */
export const api = {
  async getCoachingConversation(problemId: string): Promise<CoachingConversation> {
    return this.get<CoachingConversation>(`/coaching/${problemId}/conversation`);
  },

  async sendCoachingMessage(problemId: string, message: string): Promise<CoachingConversation> {
    return this.post<CoachingConversation>(`/coaching/${problemId}/messages`, { message });
  },

  async clearCoachingConversation(problemId: string): Promise<void> {
    return this.delete<void>(`/coaching/${problemId}/conversation`);
  },

  async getMe(): Promise<MeResponse> {
    const response = await fetch(`${API_BASE}/auth/me`, {
      credentials: "include",
    });
    return handleResponse<MeResponse>(response);
  },

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

  async logout(): Promise<{ ok: boolean }> {
    const response = await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
    return handleResponse<{ ok: boolean }>(response);
  },

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
   * Regenerate the canonical solution for a problem.
   */
  async regenerateSolution(problemId: string): Promise<{ status: string }> {
    return this.post<{ status: string }>(`/problems/${problemId}/solution-regeneration`, {});
  },

  async get<T>(path: string): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
      credentials: "include",
    });
    return handleResponse<T>(response);
  },

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

  async delete<T>(path: string): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "DELETE",
      credentials: "include",
    });
    return handleResponse<T>(response);
  },

  async postFormData<T>(path: string, formData: FormData): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      credentials: "include",
      body: formData,
    });
    return handleResponse<T>(response);
  },
};
