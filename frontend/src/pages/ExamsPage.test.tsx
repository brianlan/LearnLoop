import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { ExamsPage } from "./ExamsPage";

const mockFetch = vi.fn();
global.fetch = mockFetch;

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

function renderExamsPage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <ExamsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function renderLoadedExamsPage() {
  mockFetch.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ items: [], page: 1, pageSize: 10, total: 0 }),
  });

  renderExamsPage();
  await screen.findByText("No submitted exams yet");
}

function createExamResponse() {
  return {
    exam: {
      id: "exam-new",
      state: "in-progress",
      configSnapshot: {
        maxProblemCount: 5,
        selectionPolicy: {
          cooldownDays: 7,
          lastWrongWeight: 1,
          failureRateWeight: 1,
          recencyWeight: 1,
          minProblemAgeDays: 0,
        },
        generatedAt: "2024-01-01T00:00:00Z",
      },
      items: [],
      summary: {
        totalProblems: 0,
        answeredProblems: 0,
        gradedProblems: 0,
        pendingProblems: 0,
        correctProblems: 0,
        failedProblems: 0,
        score: null,
      },
      createdAt: "2024-01-01T00:00:00Z",
      updatedAt: "2024-01-01T00:00:00Z",
    },
  };
}

describe("ExamsPage", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("shows the discarded toggle even when submitted exam history is empty", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [], page: 1, pageSize: 10, total: 0 }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [
            {
              id: "exam-discarded",
              state: "discarded",
              createdAt: "2024-01-01T00:00:00Z",
              discardedAt: "2024-01-01T01:00:00Z",
              summary: {
                totalProblems: 1,
                answeredProblems: 0,
                gradedProblems: 0,
                pendingProblems: 0,
                correctProblems: 0,
                failedProblems: 0,
                score: null,
              },
            },
          ],
          page: 1,
          pageSize: 10,
          total: 1,
        }),
      });

    renderExamsPage();

    const toggle = await screen.findByLabelText("Show discarded");
    expect(screen.getByText("No submitted exams yet")).toBeInTheDocument();

    await user.click(toggle);

    await waitFor(() => {
      expect(mockFetch).toHaveBeenLastCalledWith(
        expect.stringContaining("includeDiscarded=true"),
        expect.any(Object),
      );
    });
    expect(await screen.findByText("Exam exam-discarded")).toBeInTheDocument();
  });

  it("opens the create modal without sending a create request", async () => {
    const user = userEvent.setup();
    await renderLoadedExamsPage();

    await user.click(screen.getByRole("button", { name: "Start New Exam" }));

    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Start New Exam" })).toBeInTheDocument();
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it("shows default synchronized controls with product limits", async () => {
    const user = userEvent.setup();
    await renderLoadedExamsPage();

    await user.click(screen.getByRole("button", { name: "Start New Exam" }));

    const input = screen.getByLabelText("Problem count");
    const slider = screen.getByLabelText("Problem count slider");
    expect(input).toHaveValue(5);
    expect(input).toHaveAttribute("min", "1");
    expect(input).toHaveAttribute("max", "20");
    expect(input).toHaveAttribute("step", "1");
    expect(slider).toHaveValue("5");
    expect(slider).toHaveAttribute("min", "1");
    expect(slider).toHaveAttribute("max", "20");
    expect(slider).toHaveAttribute("step", "1");
  });

  it("keeps numeric and slider controls synchronized", async () => {
    const user = userEvent.setup();
    await renderLoadedExamsPage();
    await user.click(screen.getByRole("button", { name: "Start New Exam" }));

    const input = screen.getByLabelText("Problem count");
    const slider = screen.getByLabelText("Problem count slider");
    await user.clear(input);
    await user.type(input, "12");
    expect(slider).toHaveValue("12");

    fireEvent.change(slider, { target: { value: "7" } });
    expect(input).toHaveValue(7);
  });

  it.each(["", "1.5", "0", "-1", "21"])(
    "shows validation and disables creation for invalid value %s",
    async (value) => {
      const user = userEvent.setup();
      await renderLoadedExamsPage();
      await user.click(screen.getByRole("button", { name: "Start New Exam" }));

      const input = screen.getByLabelText("Problem count");
      await user.clear(input);
      if (value) {
        await user.type(input, value);
      }

      expect(screen.getByRole("alert")).toHaveTextContent("Enter a whole number from 1 to 20.");
      expect(screen.getByRole("button", { name: "Create Exam" })).toBeDisabled();
    },
  );

  it("clears validation and submits the selected valid problem count", async () => {
    const user = userEvent.setup();
    await renderLoadedExamsPage();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => createExamResponse(),
    });
    await user.click(screen.getByRole("button", { name: "Start New Exam" }));

    const input = screen.getByLabelText("Problem count");
    await user.clear(input);
    await user.type(input, "21");
    expect(screen.getByRole("button", { name: "Create Exam" })).toBeDisabled();

    await user.clear(input);
    await user.type(input, "8");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create Exam" }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/v1/exams"),
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ maxProblemCount: 8 }),
        }),
      );
    });
  });

  it("closes without creating an exam when canceled and resets on reopen", async () => {
    const user = userEvent.setup();
    await renderLoadedExamsPage();
    await user.click(screen.getByRole("button", { name: "Start New Exam" }));

    const input = screen.getByLabelText("Problem count");
    await user.clear(input);
    await user.type(input, "9");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockFetch).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Start New Exam" }));
    expect(screen.getByLabelText("Problem count")).toHaveValue(5);
  });

  it("prevents duplicate submission and displays loading feedback while creating", async () => {
    const user = userEvent.setup();
    await renderLoadedExamsPage();
    let resolveCreate: (value: unknown) => void = () => undefined;
    mockFetch.mockReturnValueOnce(new Promise((resolve) => {
      resolveCreate = resolve;
    }));
    await user.click(screen.getByRole("button", { name: "Start New Exam" }));

    await user.click(screen.getByRole("button", { name: "Create Exam" }));

    expect(screen.getByRole("button", { name: "Creating..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(mockFetch).toHaveBeenCalledTimes(2);

    resolveCreate({ ok: true, json: async () => createExamResponse() });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("closes the configuration modal and shows the continuation prompt when an active exam exists", async () => {
    const user = userEvent.setup();
    await renderLoadedExamsPage();
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      statusText: "Conflict",
      json: async () => ({ error: { code: "ACTIVE_EXAM_EXISTS", message: "Active exam exists" } }),
    });
    await user.click(screen.getByRole("button", { name: "Start New Exam" }));

    await user.click(screen.getByRole("button", { name: "Create Exam" }));

    expect(await screen.findByText("An active exam already exists. Would you like to continue it?")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
