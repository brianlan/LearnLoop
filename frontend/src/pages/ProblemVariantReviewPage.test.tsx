import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { ProblemVariantReviewPage } from "./ProblemVariantReviewPage";
import type { ProblemVariantSession } from "@/types/problemVariants";
import type { ProblemVariantSessionResponse } from "@/api/problemVariants";

vi.mock("@/api/client", () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock("@/api/problemVariants", async () => ({
  createProblemVariant: vi.fn(),
  getActiveProblemVariantSession: vi.fn(),
  editProblemVariantCandidate: vi.fn(),
  generateProblemVariant: vi.fn(),
  revalidateProblemVariant: vi.fn(),
  attestProblemVariant: vi.fn(),
  submitProblemVariant: vi.fn(),
  discardProblemVariant: vi.fn(),
}));

import { api } from "@/api/client";
import {
  createProblemVariant,
  discardProblemVariant,
  editProblemVariantCandidate,
  generateProblemVariant,
  getActiveProblemVariantSession,
  submitProblemVariant,
} from "@/api/problemVariants";

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
    defaultOptions: { queries: { retry: false } },
  });
}

function renderPage(problemId = "src-1") {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[`/problems/${problemId}/variant-review`]}>
        <Routes>
          <Route
            path="/problems/:id/variant-review"
            element={<ProblemVariantReviewPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const PROBLEM = {
  id: "src-1",
  problemType: "short-answer",
  text: "What is 2+2?",
  tags: ["algebra"],
  graphDsl: null,
  imageUrl: "/api/v1/problems/src-1/image",
  correctAnswer: { display: "4", normalizedText: "4", normalizedSet: [], format: "single" },
  isDeleted: false,
  isDisabled: false,
  createdAt: "2024-01-01T00:00:00Z",
  updatedAt: "2024-01-01T00:00:00Z",
};

function queuedSession(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    sessionId: "sess-1",
    problemId: "src-1",
    mode: "transfer-variant",
    contentRevision: 0,
    tags: [],
    variation: {
      status: "queued",
      generationCount: 1,
      original: {
        text: "What is 2+2?",
        problemType: "short-answer",
        graphDsl: null,
        correctAnswer: "4",
        subject: "math",
      },
      candidate: null,
      validation: null,
      validatedRevision: null,
      attestation: null,
      queuedAt: "2024-01-01T00:00:00Z",
    },
    submit: null,
    discardedAt: null,
    createdAt: "2024-01-01T00:00:00Z",
    updatedAt: "2024-01-01T00:00:00Z",
    ...overrides,
  } as unknown as ProblemVariantSession;
}

function readySession() {
  return queuedSession({
    contentRevision: 1,
    variation: {
      status: "ready",
      generationCount: 1,
      original: {
        text: "What is 2+2?",
        problemType: "short-answer",
        graphDsl: null,
        correctAnswer: "4",
        subject: "math",
      },
      candidate: {
        text: "What is 3+5?",
        problemType: "short-answer",
        graphDsl: null,
        correctAnswer: "8",
      },
      validation: {
        verdict: "pass",
        failures: [],
        reports: [],
      },
      validatedRevision: 1,
      attestation: null,
      queuedAt: "2024-01-01T00:00:00Z",
    },
  });
}

describe("ProblemVariantReviewPage", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    for (const fn of [
      createProblemVariant,
      getActiveProblemVariantSession,
      editProblemVariantCandidate,
      submitProblemVariant,
      discardProblemVariant,
    ] as Array<ReturnType<typeof vi.fn>>) {
      fn.mockReset();
    }
    mockNavigate.mockReset();
    vi.mocked(api.get).mockResolvedValue({ problem: PROBLEM });
  });

  it("offers the mode choice and creates a session from the source problem", async () => {
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({ session: null });
    vi.mocked(createProblemVariant).mockResolvedValue({ session: queuedSession() });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("variant-create-start")).toBeInTheDocument();
    });
    // The source problem is never modified: only the mode travels.
    fireEvent.click(screen.getByTestId("variant-mode-data-only"));
    fireEvent.click(screen.getByTestId("variant-create-start"));

    await waitFor(() => {
      expect(createProblemVariant).toHaveBeenCalledWith("src-1", "data-only");
    });
    await waitFor(() => {
      expect(screen.getByTestId("bulk-review-generate")).toBeInTheDocument();
    });
    // The stored source renders read-only: semantic fields are disabled
    // before a candidate exists.
    expect(screen.getByTestId("bulk-review-text")).toBeDisabled();
  });

  it("enters the existing session when create conflicts (409)", async () => {
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({ session: null });
    const conflict = Object.assign(new Error("exists"), {
      status: 409,
      code: "VARIANT_SESSION_EXISTS",
    });
    vi.mocked(createProblemVariant).mockRejectedValue(conflict);
    vi.mocked(getActiveProblemVariantSession).mockResolvedValueOnce({
      session: null,
    });
    // Second call (enter-existing) returns the live session.
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({
      session: queuedSession(),
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("variant-create-start")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("variant-create-start"));

    await waitFor(() => {
      expect(screen.getByTestId("bulk-review-generate")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("variant-entry-error")).not.toBeInTheDocument();
  });

  it("shows non-session-exist 409 errors instead of entering a session", async () => {
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({ session: null });
    const conflict = Object.assign(new Error("No audit image available"), {
      status: 409,
      code: "VARIANT_AUDIT_MISSING",
    });
    vi.mocked(createProblemVariant).mockRejectedValue(conflict);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("variant-create-start")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("variant-create-start"));

    await waitFor(() => {
      expect(screen.getByTestId("variant-entry-error")).toHaveTextContent(
        "No audit image available",
      );
    });
    // Entering an existing session is only for the session-exists race.
    expect(getActiveProblemVariantSession).toHaveBeenCalledTimes(1);
  });

  it("keeps Generate available while a session is in flight (self-heal)", async () => {
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({
      // A session stranded in generating (e.g. by a server restart) must
      // stay recoverable through the backend's Generate override.
      session: queuedSession({
        variation: {
          ...queuedSession().variation,
          status: "generating",
        },
      }),
    });
    vi.mocked(generateProblemVariant).mockResolvedValue({
      session: queuedSession({ contentRevision: 1 }),
    });

    renderPage();

    const generateButton = await screen.findByTestId("bulk-review-generate");
    await waitFor(() => {
      expect(generateButton).not.toBeDisabled();
    });
    fireEvent.click(generateButton);

    await waitFor(() => {
      expect(generateProblemVariant).toHaveBeenCalledWith("src-1", "sess-1", 0);
    });
  });

  it("submits a ready variant and navigates to the admitted problem", async () => {
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({
      session: readySession(),
    });
    vi.mocked(submitProblemVariant).mockResolvedValue({
      problemId: "admitted-9",
      alreadySubmitted: false,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("variant-submit")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("variant-submit"));

    await waitFor(() => {
      expect(submitProblemVariant).toHaveBeenCalledWith("src-1", "sess-1");
    });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/problems/admitted-9");
    });
  });

  it("reports submit errors instead of navigating", async () => {
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({
      session: readySession(),
    });
    vi.mocked(submitProblemVariant).mockRejectedValue(new Error("stale"));

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("variant-submit")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("variant-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("variant-submit-error")).toHaveTextContent("stale");
    });
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("saves candidate edits through the session PATCH", async () => {
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({
      session: readySession(),
    });
    vi.mocked(editProblemVariantCandidate).mockImplementation(async (_p, _s, body) => {
      const current = readySession();
      return {
        session: {
          ...current,
          contentRevision: (body as { expectedRevision: number }).expectedRevision + 1,
          variation: {
            ...current.variation,
            status: "needs-validation",
            candidate: {
              ...current.variation?.candidate,
              correctAnswer: (body as { correctAnswer?: string }).correctAnswer ?? "",
            },
          },
        } as ProblemVariantSession,
      };
    });

    renderPage();

    const answer = await screen.findByDisplayValue("8");
    fireEvent.change(answer, { target: { value: "8.5" } });

    await waitFor(() => {
      expect(editProblemVariantCandidate).toHaveBeenCalled();
    });
    const call = vi.mocked(editProblemVariantCandidate).mock.calls[0];
    expect(call[0]).toBe("src-1");
    expect(call[1]).toBe("sess-1");
    const body = call[2] as Record<string, unknown>;
    expect(body["correctAnswer"]).toBe("8.5");
    expect(body["expectedRevision"]).toBe(1);
    await waitFor(() => {
      expect(screen.getByTestId("bulk-review-variation-status")).toHaveTextContent(
        "needs validation",
      );
    });
  });

  it("blocks submit while candidate edits are still saving", async () => {
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({
      session: readySession(),
    });
    let resolveSave: (value: ProblemVariantSessionResponse) => void =
      () => undefined;
    vi.mocked(editProblemVariantCandidate).mockImplementation(
      () =>
        new Promise<ProblemVariantSessionResponse>((resolve) => {
          resolveSave = resolve;
        }),
    );

    renderPage();

    const answer = await screen.findByDisplayValue("8");
    fireEvent.change(answer, { target: { value: "8.5" } });

    // Codex P1: submitting during the autosave window would admit the stale
    // server-side candidate and silently drop the visible edit.
    await waitFor(() => {
      expect(screen.getByTestId("variant-submit")).toBeDisabled();
    });
    // Release only once the debounced save is actually in flight, so the
    // captured resolver belongs to the real pending save.
    await waitFor(() => {
      expect(editProblemVariantCandidate).toHaveBeenCalled();
    });

    const current = readySession();
    resolveSave({
      session: {
        ...current,
        contentRevision: 2,
        variation: {
          ...current.variation,
          status: "needs-validation",
          validatedRevision: null,
          candidate: { ...current.variation?.candidate, correctAnswer: "8.5" },
        },
      } as ProblemVariantSession,
    });
    // Once saved, the session leaves ready and submit disappears entirely.
    await waitFor(() => {
      expect(screen.queryByTestId("variant-submit")).not.toBeInTheDocument();
    });
  });

  it("exposes candidate editing for a failed session with no candidate", async () => {
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({
      session: queuedSession({
        contentRevision: 1,
        variation: {
          ...queuedSession().variation,
          status: "failed",
          validation: {
            verdict: "fail",
            failures: [{ kind: "provider", evidence: "VLM unreachable" }],
            reports: [],
          },
        },
      }),
    });
    vi.mocked(editProblemVariantCandidate).mockImplementation(async (_p, _s, body) => {
      const current = queuedSession();
      return {
        session: {
          ...current,
          contentRevision: 2,
          variation: {
            ...current.variation,
            status: "needs-validation",
            candidate: {
              text: "hand fix",
              problemType: "short-answer",
              graphDsl: null,
              correctAnswer: (body as { correctAnswer?: string }).correctAnswer ?? "",
            },
          },
        } as ProblemVariantSession,
      };
    });

    renderPage();

    // Codex P2: candidate-from-nothing recovery needs the target chooser and
    // an editable candidate form even when no candidate is stored yet.
    await waitFor(() => {
      expect(screen.getByTestId("bulk-review-edit-candidate")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("bulk-review-edit-candidate"));

    const answer = await screen.findByTestId("bulk-review-answer");
    fireEvent.change(answer, { target: { value: "42" } });

    await waitFor(() => {
      expect(editProblemVariantCandidate).toHaveBeenCalled();
    });
    const body = vi.mocked(editProblemVariantCandidate).mock.calls[0][2] as Record<
      string,
      unknown
     >;
     expect(body["correctAnswer"]).toBe("42");
   });

  it("sends only tags for tag edits even when a ready candidate exists", async () => {
    // Codex R1 (P1): the hook routes tag saves through the read-only source
    // target with a full form; forwarding that form through the candidate
    // route overwrote a ready candidate with source content.
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({
      session: readySession(),
    });
    vi.mocked(editProblemVariantCandidate).mockResolvedValue({
      session: readySession(),
    });

    renderPage();

    const tagInput = await screen.findByTestId("bulk-review-tags-field");
    fireEvent.change(tagInput, { target: { value: "algebra" } });
    fireEvent.keyDown(tagInput, { key: "Enter", code: "Enter" });

    await waitFor(() => {
      expect(editProblemVariantCandidate).toHaveBeenCalledTimes(1);
    });
    expect(vi.mocked(editProblemVariantCandidate)).toHaveBeenCalledWith(
      "src-1",
      "sess-1",
      { expectedRevision: 1, tags: ["algebra"] },
    );
  });

  it("sends only tags for tag edits while the session is busy", async () => {
    // Busy states reject candidate content; the tags branch is
    // status-independent, so the payload must be tags-only here too.
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({
      session: queuedSession(),
    });
    vi.mocked(editProblemVariantCandidate).mockResolvedValue({
      session: queuedSession(),
    });

    renderPage();

    const tagInput = await screen.findByTestId("bulk-review-tags-field");
    fireEvent.change(tagInput, { target: { value: "algebra" } });
    fireEvent.keyDown(tagInput, { key: "Enter", code: "Enter" });

    await waitFor(() => {
      expect(editProblemVariantCandidate).toHaveBeenCalledTimes(1);
    });
    expect(vi.mocked(editProblemVariantCandidate)).toHaveBeenCalledWith(
      "src-1",
      "sess-1",
      { expectedRevision: 0, tags: ["algebra"] },
    );
  });

  it("keeps the provisional candidate buffer while the first save is in flight", async () => {
    // Codex R2 (P2): with no stored candidate, the reconciliation used to
    // drop the editing buffer the moment the first save started, reverting
    // the editor to the source mid-save.
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({
      session: queuedSession({
        contentRevision: 1,
        variation: {
          ...queuedSession().variation,
          status: "failed",
          validation: {
            verdict: "fail",
            failures: [{ kind: "provider", evidence: "VLM unreachable" }],
            reports: [],
          },
        },
      }),
    });
    let resolveSave: (value: ProblemVariantSessionResponse) => void =
      () => undefined;
    vi.mocked(editProblemVariantCandidate).mockImplementation(
      () =>
        new Promise<ProblemVariantSessionResponse>((resolve) => {
          resolveSave = resolve;
        }),
    );

    renderPage();

    fireEvent.click(await screen.findByTestId("bulk-review-edit-candidate"));
    const text = await screen.findByTestId("bulk-review-text");
    fireEvent.change(text, {
      target: { value: "Teacher's complete hand-built candidate" },
    });

    await waitFor(() => {
      expect(editProblemVariantCandidate).toHaveBeenCalled();
    });
    expect((screen.getByTestId("bulk-review-text") as HTMLInputElement).value).toBe(
      "Teacher's complete hand-built candidate",
    );

    resolveSave({
      session: queuedSession({
        contentRevision: 2,
        variation: {
          ...queuedSession().variation,
          status: "needs-validation",
          candidate: {
            text: "Teacher's complete hand-built candidate",
            problemType: "short-answer",
            graphDsl: null,
            correctAnswer: "4",
          },
        },
      }),
    });
    await waitFor(() => {
      expect(
        (screen.getByTestId("bulk-review-text") as HTMLInputElement).value,
      ).toBe("Teacher's complete hand-built candidate");
    });
  });

  it("keeps the provisional candidate buffer when the first save fails", async () => {
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({
      session: queuedSession({
        contentRevision: 1,
        variation: {
          ...queuedSession().variation,
          status: "failed",
          validation: {
            verdict: "fail",
            failures: [{ kind: "provider", evidence: "VLM unreachable" }],
            reports: [],
          },
        },
      }),
    });
    vi.mocked(editProblemVariantCandidate).mockRejectedValue(
      Object.assign(new Error("Save failed"), { status: 500 }),
    );

    renderPage();

    fireEvent.click(await screen.findByTestId("bulk-review-edit-candidate"));
    const text = await screen.findByTestId("bulk-review-text");
    fireEvent.change(text, {
      target: { value: "Teacher's complete hand-built candidate" },
    });

    await waitFor(() => {
      expect(
        (screen.getByTestId("bulk-review-text") as HTMLInputElement).value,
      ).toBe("Teacher's complete hand-built candidate");
    });
    // The failure is surfaced for retry, not silently dropped.
    await waitFor(() => {
      expect(screen.getByTestId("bulk-review-save-status")).toHaveTextContent(
        /retry|failed/i,
      );
    });
  });

  it("discards the session and returns to the source problem", async () => {
    vi.mocked(getActiveProblemVariantSession).mockResolvedValue({
      session: queuedSession(),
    });
    vi.mocked(discardProblemVariant).mockResolvedValue({
      session: queuedSession({ discardedAt: "2024-01-02T00:00:00Z" }),
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("variant-discard")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("variant-discard"));

    await waitFor(() => {
      expect(discardProblemVariant).toHaveBeenCalledWith("src-1", "sess-1");
    });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/problems/src-1");
    });
  });
});
