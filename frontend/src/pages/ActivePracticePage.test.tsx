import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { api } from "@/api/client";
import { ActivePracticePage } from "./ActivePracticePage";

const mockFetch = vi.fn();
global.fetch = mockFetch;

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

function renderActivePracticePage(problem?: { id: string; text: string; type: string; imageUrl?: string; graphDsl?: string }) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[{ pathname: "/practice/active", state: { problem } }]}>
        <ActivePracticePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const mockProblem = {
  id: "problem-1",
  text: "What is 2+2?",
  type: "fill-in-the-blank",
};

describe("ActivePracticePage", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockNavigate.mockReset();
    vi.spyOn(api, "getSolutionStatus").mockResolvedValue({ status: "none" });
  });

  it("renders problem text", async () => {
    renderActivePracticePage(mockProblem);

    await waitFor(() => {
      expect(screen.getByTestId("problem-text")).toBeInTheDocument();
    });
    expect(screen.getByText("What is 2+2?")).toBeInTheDocument();
  });

  it("shows submit button", async () => {
    renderActivePracticePage(mockProblem);

    await waitFor(() => {
      expect(screen.getByTestId("submit-button")).toBeInTheDocument();
    });
  });

  it("shows skip and quit buttons", async () => {
    renderActivePracticePage(mockProblem);

    await waitFor(() => {
      expect(screen.getByTestId("skip-button")).toBeInTheDocument();
      expect(screen.getByTestId("quit-button")).toBeInTheDocument();
    });
  });

  it("submit triggers grading and shows feedback", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ gradingStatus: "correct", gradingMethod: "normalized-match" }),
    });

    renderActivePracticePage(mockProblem);

    await waitFor(() => {
      expect(screen.getByTestId("submit-button")).toBeInTheDocument();
    });

    const input = screen.getByRole("textbox");
    await user.type(input, "4");
    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("grading-feedback")).toBeInTheDocument();
    });
    expect(screen.getByText("Correct!")).toBeInTheDocument();
  });

  it("shows incorrect feedback for wrong answer", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ gradingStatus: "incorrect", gradingMethod: "normalized-match" }),
    });

    renderActivePracticePage(mockProblem);

    await waitFor(() => {
      expect(screen.getByTestId("submit-button")).toBeInTheDocument();
    });

    const input = screen.getByRole("textbox");
    await user.type(input, "5");
    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("grading-feedback")).toBeInTheDocument();
    });
    expect(screen.getByText("Incorrect")).toBeInTheDocument();
  });

  it("next button fetches new problem", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ gradingStatus: "correct", gradingMethod: "normalized-match" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ status: "ok", problem: { id: "problem-2", text: "New question", type: "fill-in-the-blank" } }),
      });

    renderActivePracticePage(mockProblem);

    await waitFor(() => {
      expect(screen.getByTestId("submit-button")).toBeInTheDocument();
    });

    const input = screen.getByRole("textbox");
    await user.type(input, "4");
    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("next-button")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("next-button"));

    await waitFor(() => {
      expect(screen.getByText("New question")).toBeInTheDocument();
    });
  });

  it("skip button fetches new problem without submitting", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: "ok", problem: { id: "problem-2", text: "Skipped question", type: "fill-in-the-blank" } }),
    });

    renderActivePracticePage(mockProblem);

    await waitFor(() => {
      expect(screen.getByTestId("skip-button")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("skip-button"));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(1);
      expect(mockFetch.mock.calls[0][0]).toBe("/api/v1/practice/next");
    });
  });

  it("quit button navigates to landing page", async () => {
    renderActivePracticePage(mockProblem);

    await waitFor(() => {
      expect(screen.getByTestId("quit-button")).toBeInTheDocument();
    });

    screen.getByTestId("quit-button").click();

    expect(mockNavigate).toHaveBeenCalledWith("/practice");
  });

  it("shows loading state during grading", async () => {
    mockFetch.mockImplementation(() => new Promise(() => {}));

    const user = userEvent.setup();
    renderActivePracticePage(mockProblem);

    await waitFor(() => {
      expect(screen.getByTestId("submit-button")).toBeInTheDocument();
    });

    const input = screen.getByRole("textbox");
    await user.type(input, "4");
    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByText("Grading...")).toBeInTheDocument();
    });
  });

  it("shows pending-review when VLM fails", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ gradingStatus: "pending-review", gradingMethod: "vlm" }),
    });

    renderActivePracticePage({ id: "p1", text: "Short answer question", type: "short-answer" });

    await waitFor(() => {
      expect(screen.getByTestId("submit-button")).toBeInTheDocument();
    });

    const textarea = screen.getByRole("textbox");
    await user.type(textarea, "my answer");
    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("grading-feedback")).toBeInTheDocument();
    });
    expect(screen.getByText("Pending Review")).toBeInTheDocument();
  });

  it("redirects to landing page when no problem provided", async () => {
    renderActivePracticePage(undefined);

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/practice", { replace: true });
    });
  });

  it("renders AI Explain button when status is ready", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ gradingStatus: "correct", gradingMethod: "normalized-match" }),
    });
    vi.spyOn(api, "getSolutionStatus").mockResolvedValue({ status: "ready" });

    renderActivePracticePage(mockProblem);

    await waitFor(() => {
      expect(screen.getByTestId("submit-button")).toBeInTheDocument();
    });

    const input = screen.getByRole("textbox");
    await user.type(input, "4");
    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("explain-button")).toBeInTheDocument();
    });
    expect(screen.getByTestId("explain-button")).toHaveTextContent("AI Explain");
    expect(screen.getByTestId("explain-button")).not.toBeDisabled();
  });

  it("shows generating message on click when status is pending", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ gradingStatus: "correct", gradingMethod: "normalized-match" }),
    });
    vi.spyOn(api, "getSolutionStatus").mockResolvedValue({ status: "pending" });

    renderActivePracticePage(mockProblem);

    await waitFor(() => {
      expect(screen.getByTestId("submit-button")).toBeInTheDocument();
    });

    const input = screen.getByRole("textbox");
    await user.type(input, "4");
    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("explain-button")).toBeInTheDocument();
    });
    expect(screen.getByTestId("explain-button")).toHaveTextContent("AI Explain (Generating...)");

    await user.click(screen.getByTestId("explain-button"));

    await waitFor(() => {
      expect(screen.getByTestId("explain-info-message")).toBeInTheDocument();
    });
    expect(screen.getByTestId("explain-info-message")).toHaveTextContent(
      "Solution is being generated, please try again shortly"
    );
  });

  it("disables AI Explain button when status is failed", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ gradingStatus: "correct", gradingMethod: "normalized-match" }),
    });
    vi.spyOn(api, "getSolutionStatus").mockResolvedValue({ status: "failed" });

    renderActivePracticePage(mockProblem);

    await waitFor(() => {
      expect(screen.getByTestId("submit-button")).toBeInTheDocument();
    });

    const input = screen.getByRole("textbox");
    await user.type(input, "4");
    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("explain-button")).toBeInTheDocument();
    });
    expect(screen.getByTestId("explain-button")).toHaveTextContent("AI Explain (Unavailable)");
    expect(screen.getByTestId("explain-button")).toBeDisabled();
  });

  it("hides AI Explain button when status is none", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ gradingStatus: "correct", gradingMethod: "normalized-match" }),
    });
    vi.spyOn(api, "getSolutionStatus").mockResolvedValue({ status: "none" });

    renderActivePracticePage(mockProblem);

    await waitFor(() => {
      expect(screen.getByTestId("submit-button")).toBeInTheDocument();
    });

    const input = screen.getByRole("textbox");
    await user.type(input, "4");
    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByTestId("next-button")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("explain-button")).not.toBeInTheDocument();
  });

  it("renders GraphSandbox when problem has graphDsl", async () => {
    const graphProblem = {
      id: "problem-1",
      text: "Triangle problem",
      type: "short-answer",
      graphDsl: "board.create('point', [0, 0], {name:'A'});",
    };
    vi.spyOn(api, "getSolutionStatus").mockResolvedValue({ status: "none" });

    renderActivePracticePage(graphProblem);

    await waitFor(() => {
      expect(screen.getByTestId("submit-button")).toBeInTheDocument();
    });
    expect(screen.getByText("Triangle problem")).toBeInTheDocument();
    expect(screen.getByTestId("jsxgraph-iframe")).toBeInTheDocument();
  });

  it("does not render GraphSandbox when problem has no graphDsl", async () => {
    vi.spyOn(api, "getSolutionStatus").mockResolvedValue({ status: "none" });

    renderActivePracticePage(mockProblem);

    await waitFor(() => {
      expect(screen.getByTestId("submit-button")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("jsxgraph-iframe")).not.toBeInTheDocument();
  });
});
