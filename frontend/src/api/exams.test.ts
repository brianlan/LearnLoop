import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  getExamHistory,
  createExam,
  getActiveExam,
  getExam,
  saveExamAnswer,
  submitExam,
  discardExam,
  selfReportExamItem,
} from "./exams";
import type {
  CreateExamResponse,
  ExamHistoryResponse,
  ExamResponse,
  SaveAnswerResponse,
  SelfReportResponse,
} from "@/types/exam";

function mockOk(response: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(response),
  });
}

describe("exams API module", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("getExamHistory GETs /exams with page, pageSize, includeDiscarded", async () => {
    const response: ExamHistoryResponse = {
      items: [],
      page: 1,
      pageSize: 10,
      total: 0,
    };
    const mockFetch = mockOk(response);
    vi.stubGlobal("fetch", mockFetch);

    await getExamHistory(2, 20, true);

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/exams?page=2&pageSize=20&includeDiscarded=true",
      { credentials: "include" },
    );
  });

  it("createExam POSTs the request body to /exams", async () => {
    const response: CreateExamResponse = {
      exam: {
        id: "e1",
        state: "in-progress",
        configSnapshot: {
          maxProblemCount: 5,
          selectionPolicy: {
            cooldownDays: 0,
            lastWrongWeight: 0,
            failureRateWeight: 0,
            recencyWeight: 0,
            minProblemAgeDays: 0,
          },
          generatedAt: "2026-01-01T00:00:00Z",
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
        createdAt: "2026-01-01T00:00:00Z",
        updatedAt: "2026-01-01T00:00:00Z",
      },
    };
    const mockFetch = mockOk(response);
    vi.stubGlobal("fetch", mockFetch);

    await createExam({ maxProblemCount: 5 });

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/exams", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ maxProblemCount: 5 }),
    });
  });

  it("getActiveExam GETs /exams/active", async () => {
    const response: ExamResponse = {
      exam: {
        id: "e1",
        state: "in-progress",
        configSnapshot: {
          maxProblemCount: 5,
          selectionPolicy: {
            cooldownDays: 0,
            lastWrongWeight: 0,
            failureRateWeight: 0,
            recencyWeight: 0,
            minProblemAgeDays: 0,
          },
          generatedAt: "2026-01-01T00:00:00Z",
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
        createdAt: "2026-01-01T00:00:00Z",
        updatedAt: "2026-01-01T00:00:00Z",
      },
    };
    const mockFetch = mockOk(response);
    vi.stubGlobal("fetch", mockFetch);

    await getActiveExam();

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/exams/active", {
      credentials: "include",
    });
  });

  it("getExam GETs /exams/{examId}", async () => {
    const response: ExamResponse = {
      exam: {
        id: "e1",
        state: "submitted",
        configSnapshot: {
          maxProblemCount: 5,
          selectionPolicy: {
            cooldownDays: 0,
            lastWrongWeight: 0,
            failureRateWeight: 0,
            recencyWeight: 0,
            minProblemAgeDays: 0,
          },
          generatedAt: "2026-01-01T00:00:00Z",
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
        createdAt: "2026-01-01T00:00:00Z",
        updatedAt: "2026-01-01T00:00:00Z",
      },
    };
    const mockFetch = mockOk(response);
    vi.stubGlobal("fetch", mockFetch);

    await getExam("exam-123");

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/exams/exam-123", {
      credentials: "include",
    });
  });

  it("saveExamAnswer PATCHes the answer to /exams/{examId}/items/{itemId}/answer", async () => {
    const response: SaveAnswerResponse = {
      item: {
        itemId: "i1",
        order: 0,
        problemId: "p1",
        problem: {
          text: "Q",
          problemType: "single-choice",
        },
        answer: { raw: "A", savedAt: "2026-01-01T00:00:00Z" },
        grading: {
          status: "ungraded",
          retryCount: 0,
        },
      },
    };
    const mockFetch = mockOk(response);
    vi.stubGlobal("fetch", mockFetch);

    await saveExamAnswer("exam-1", "item-1", { answer: "A" });

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/exams/exam-1/items/item-1/answer",
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ answer: "A" }),
      },
    );
  });

  it("submitExam POSTs an empty body to /exams/{examId}/submit", async () => {
    const response: ExamResponse = {
      exam: {
        id: "e1",
        state: "submitted",
        configSnapshot: {
          maxProblemCount: 5,
          selectionPolicy: {
            cooldownDays: 0,
            lastWrongWeight: 0,
            failureRateWeight: 0,
            recencyWeight: 0,
            minProblemAgeDays: 0,
          },
          generatedAt: "2026-01-01T00:00:00Z",
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
        createdAt: "2026-01-01T00:00:00Z",
        updatedAt: "2026-01-01T00:00:00Z",
      },
    };
    const mockFetch = mockOk(response);
    vi.stubGlobal("fetch", mockFetch);

    await submitExam("exam-1");

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/exams/exam-1/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({}),
    });
  });

  it("discardExam POSTs an empty body to /exams/{examId}/discard", async () => {
    const response: ExamResponse = {
      exam: {
        id: "e1",
        state: "discarded",
        configSnapshot: {
          maxProblemCount: 5,
          selectionPolicy: {
            cooldownDays: 0,
            lastWrongWeight: 0,
            failureRateWeight: 0,
            recencyWeight: 0,
            minProblemAgeDays: 0,
          },
          generatedAt: "2026-01-01T00:00:00Z",
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
        createdAt: "2026-01-01T00:00:00Z",
        updatedAt: "2026-01-01T00:00:00Z",
      },
    };
    const mockFetch = mockOk(response);
    vi.stubGlobal("fetch", mockFetch);

    await discardExam("exam-1");

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith("/api/v1/exams/exam-1/discard", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({}),
    });
  });

  it("selfReportExamItem POSTs isCorrect to /exams/{examId}/items/{itemId}/self-report", async () => {
    const response: SelfReportResponse = {
      item: {
        itemId: "i1",
        order: 0,
        problemId: "p1",
        problem: {
          text: "Q",
          problemType: "short-answer",
        },
        answer: { raw: "A" },
        grading: {
          status: "correct",
          method: "self-report",
          isCorrect: true,
          score: 1,
          retryCount: 0,
          selfReportedCorrect: true,
        },
      },
      summary: {
        totalProblems: 1,
        answeredProblems: 1,
        gradedProblems: 1,
        pendingProblems: 0,
        correctProblems: 1,
        failedProblems: 0,
        score: 1,
      },
    };
    const mockFetch = mockOk(response);
    vi.stubGlobal("fetch", mockFetch);

    await selfReportExamItem("exam-1", "item-1", { isCorrect: true });

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/v1/exams/exam-1/items/item-1/self-report",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ isCorrect: true }),
      },
    );
  });
});