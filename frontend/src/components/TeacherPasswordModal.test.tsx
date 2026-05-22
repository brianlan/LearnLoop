import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TeacherPasswordModal } from "./TeacherPasswordModal";

// Mock the API client
vi.mock("@/api/client", () => ({
  api: {
    verifyTeacherPassword: vi.fn(),
  },
}));

import { api } from "@/api/client";

describe("TeacherPasswordModal", () => {
  const mockOnClose = vi.fn();
  const mockOnVerified = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not render when isOpen is false", () => {
    render(
      <TeacherPasswordModal
        isOpen={false}
        onClose={mockOnClose}
        onVerified={mockOnVerified}
      />
    );
    expect(screen.queryByTestId("teacher-password-modal")).not.toBeInTheDocument();
  });

  it("renders modal with password input and submit button when open", () => {
    render(
      <TeacherPasswordModal
        isOpen={true}
        onClose={mockOnClose}
        onVerified={mockOnVerified}
      />
    );
    expect(screen.getByTestId("teacher-password-modal")).toBeInTheDocument();
    expect(screen.getByTestId("teacher-password-input")).toBeInTheDocument();
    expect(screen.getByTestId("teacher-password-submit")).toBeInTheDocument();
    expect(screen.getByTestId("teacher-password-cancel")).toBeInTheDocument();
  });

  it("successful verification calls onVerified and closes", async () => {
    vi.mocked(api.verifyTeacherPassword).mockResolvedValueOnce({ ok: true });

    render(
      <TeacherPasswordModal
        isOpen={true}
        onClose={mockOnClose}
        onVerified={mockOnVerified}
      />
    );

    fireEvent.change(screen.getByTestId("teacher-password-input"), {
      target: { value: "correct-password" },
    });
    fireEvent.click(screen.getByTestId("teacher-password-submit"));

    await waitFor(() => {
      expect(api.verifyTeacherPassword).toHaveBeenCalledWith("correct-password");
      expect(mockOnVerified).toHaveBeenCalled();
      expect(mockOnClose).toHaveBeenCalled();
    });
  });

  it("failed verification shows error message", async () => {
    vi.mocked(api.verifyTeacherPassword).mockRejectedValueOnce(
      new Error("Incorrect teacher password")
    );

    render(
      <TeacherPasswordModal
        isOpen={true}
        onClose={mockOnClose}
        onVerified={mockOnVerified}
      />
    );

    fireEvent.change(screen.getByTestId("teacher-password-input"), {
      target: { value: "wrong-password" },
    });
    fireEvent.click(screen.getByTestId("teacher-password-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("teacher-password-error")).toHaveTextContent(
        "Incorrect teacher password"
      );
    });

    expect(mockOnVerified).not.toHaveBeenCalled();
    expect(mockOnClose).not.toHaveBeenCalled();
  });

  it("empty submission shows client-side error", async () => {
    render(
      <TeacherPasswordModal
        isOpen={true}
        onClose={mockOnClose}
        onVerified={mockOnVerified}
      />
    );

    fireEvent.click(screen.getByTestId("teacher-password-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("teacher-password-error")).toHaveTextContent(
        "Password cannot be empty"
      );
    });

    expect(api.verifyTeacherPassword).not.toHaveBeenCalled();
  });

  it("network error shows network error message", async () => {
    vi.mocked(api.verifyTeacherPassword).mockRejectedValueOnce(
      new Error("Network failure")
    );

    render(
      <TeacherPasswordModal
        isOpen={true}
        onClose={mockOnClose}
        onVerified={mockOnVerified}
      />
    );

    fireEvent.change(screen.getByTestId("teacher-password-input"), {
      target: { value: "some-password" },
    });
    fireEvent.click(screen.getByTestId("teacher-password-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("teacher-password-error")).toHaveTextContent(
        "Network error. Please try again."
      );
    });
  });

  it("retry after failure allows resubmission", async () => {
    vi.mocked(api.verifyTeacherPassword)
      .mockRejectedValueOnce(new Error("Incorrect teacher password"))
      .mockResolvedValueOnce({ ok: true });

    render(
      <TeacherPasswordModal
        isOpen={true}
        onClose={mockOnClose}
        onVerified={mockOnVerified}
      />
    );

    // First attempt fails
    fireEvent.change(screen.getByTestId("teacher-password-input"), {
      target: { value: "wrong-password" },
    });
    fireEvent.click(screen.getByTestId("teacher-password-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("teacher-password-error")).toBeInTheDocument();
    });

    // Retry with correct password
    fireEvent.change(screen.getByTestId("teacher-password-input"), {
      target: { value: "correct-password" },
    });
    fireEvent.click(screen.getByTestId("teacher-password-submit"));

    await waitFor(() => {
      expect(mockOnVerified).toHaveBeenCalled();
      expect(mockOnClose).toHaveBeenCalled();
    });

    expect(api.verifyTeacherPassword).toHaveBeenCalledTimes(2);
  });

  it("Escape key closes modal", () => {
    render(
      <TeacherPasswordModal
        isOpen={true}
        onClose={mockOnClose}
        onVerified={mockOnVerified}
      />
    );

    fireEvent.keyDown(window, { key: "Escape" });

    expect(mockOnClose).toHaveBeenCalled();
  });

  it("Cancel button closes modal", () => {
    render(
      <TeacherPasswordModal
        isOpen={true}
        onClose={mockOnClose}
        onVerified={mockOnVerified}
      />
    );

    fireEvent.click(screen.getByTestId("teacher-password-cancel"));

    expect(mockOnClose).toHaveBeenCalled();
  });
});