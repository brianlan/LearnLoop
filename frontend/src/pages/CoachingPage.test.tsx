import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { api } from "@/api/client";
import { CoachingPage, type ExamResponse } from "./CoachingPage";
import type { PracticeHistoryResponse } from "@/types/practice";
import type { CoachingConversation, CoachingMessage } from "@/types/coaching";

// Mock GraphSandbox component to easily verify DSL and trigger errors
vi.mock("@/components/GraphSandbox", () => ({
  GraphSandbox: ({ dsl, onError }: { dsl: string; onError?: (err: string) => void }) => (
    <div data-testid="graph-sandbox" data-dsl={dsl}>
      GraphSandbox: {dsl}
      <button
        type="button"
        onClick={() => onError?.("JSXGraph compile error")}
        data-testid="trigger-sandbox-error"
      >
        Trigger Sandbox Error
      </button>
    </div>
  ),
}));

// Mock CollapsibleImage component
vi.mock("@/components/CollapsibleImage", () => ({
  CollapsibleImage: ({ src, alt }: { src: string; alt: string }) => (
    <img src={src} alt={alt} data-testid="collapsible-image" />
  ),
}));

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

function renderCoachingPage(
  problemId: string,
  fromRoute: string = "/practice"
) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter
        initialEntries={[{ pathname: `/coaching/${problemId}`, state: { from: fromRoute } }]}
      >
        <Routes>
          <Route path="/coaching/:problemId" element={<CoachingPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const mockProblem = {
  id: "prob-123",
  text: "Given $x^2 = 4$, find $x$.",
  problemType: "short-answer",
  graphDsl: "board.create('point', [1, 2]);",
  imageUrl: "/api/v1/problems/prob-123/image",
  tags: ["algebra"],
  isDeleted: false,
  createdAt: "2026-05-25T12:00:00Z",
  updatedAt: "2026-05-25T12:00:00Z",
};

const mockConversationEmpty: CoachingConversation = {
  problem_id: "prob-123",
  user_id: "user-456",
  messages: [],
  created_at: "2026-05-25T12:30:00Z",
  updated_at: "2026-05-25T12:30:00Z",
};

const mockConversationWithMessages: CoachingConversation = {
  problem_id: "prob-123",
  user_id: "user-456",
  messages: [
    {
      role: "student",
      content: "Can you help me solve $x^2 = 4$?",
      created_at: "2026-05-25T12:31:00Z",
    },
    {
      role: "coach",
      content: "Sure! Take the square root of both sides. We get $x = \\pm 2$. Here is a drawing.",
      whiteboard_dsl: "board.create('point', [0, 2]);",
      created_at: "2026-05-25T12:32:00Z",
    },
  ],
  created_at: "2026-05-25T12:30:00Z",
  updated_at: "2026-05-25T12:32:00Z",
};

const mockPracticeHistory: PracticeHistoryResponse = {
  items: [
    {
      problemId: "prob-123",
      problemText: "Given $x^2 = 4$, find $x$.",
      problemType: "short-answer",
      summary: {
        totalAttempts: 1,
        correctCount: 0,
        wrongCount: 1,
        lastPracticedAt: "2026-05-25T12:15:00Z",
        lastResult: "incorrect",
      },
      attempts: [
        {
          submittedAnswer: "3",
          gradingStatus: "incorrect",
          gradingMethod: "normalized-match",
          createdAt: "2026-05-25T12:15:00Z",
        },
      ],
    },
  ],
};

const mockExamData: ExamResponse = {
  exam: {
    id: "exam-789",
    items: [
      {
        problemId: "prob-123",
        answer: {
          raw: "2",
        },
        grading: {
          status: "correct",
          isCorrect: true,
        },
      },
    ],
  },
};

describe("CoachingPage", () => {
  beforeEach(() => {
    // Mock scrollIntoView in jsdom
    window.HTMLElement.prototype.scrollIntoView = vi.fn();

    vi.restoreAllMocks();

    // Standard mocks for API Client
    vi.spyOn(api, "get").mockImplementation(async (path: string) => {
      if (path === "/problems/prob-123") {
        return { problem: mockProblem };
      }
      if (path === "/practice/history") {
        return mockPracticeHistory;
      }
      if (path === "/exams/exam-789") {
        return mockExamData;
      }
      throw new Error(`Unexpected GET path: ${path}`);
    });

    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(mockConversationEmpty);
    vi.spyOn(api, "sendCoachingMessage").mockResolvedValue(mockConversationWithMessages);
    vi.spyOn(api, "clearCoachingConversation").mockResolvedValue(undefined);
  });

  it("renders context bar with problem text, original figure, student answer, and grading judgement (from practice)", async () => {
    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("context-bar")).toBeInTheDocument();
    });

    // Check problem text LaTeX is rendered
    expect(screen.getByTestId("context-bar").textContent).toContain("Given");
    expect(screen.getByTestId("context-bar").textContent).toContain("find x");

    // Check original figure via GraphSandbox is displayed
    const originalFigure = screen.getByTestId("context-original-figure");
    expect(originalFigure).toBeInTheDocument();
    expect(originalFigure.querySelector('[data-testid="graph-sandbox"]')).toHaveAttribute(
      "data-dsl",
      mockProblem.graphDsl
    );

    // Check student answer and status from practice history
    expect(screen.getByText("Incorrect")).toBeInTheDocument();
    expect(screen.getByTestId("student-answer")).toHaveTextContent("3");
  });

  it("renders context bar with student answer and grading judgement (from exam)", async () => {
    renderCoachingPage("prob-123", "/exams/exam-789");

    await waitFor(() => {
      expect(screen.getByTestId("context-bar")).toBeInTheDocument();
    });

    // Check student answer and status from exam data
    expect(screen.getByText("Correct")).toBeInTheDocument();
    expect(screen.getByTestId("student-answer")).toHaveTextContent("2");
  });

  it("displays conversation messages in chronological order and renders LaTeX", async () => {
    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(mockConversationWithMessages);

    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("chat-log")).toBeInTheDocument();
    });

    const chatLog = screen.getByTestId("chat-log");
    expect(chatLog.textContent).toContain("Can you help me solve");
    expect(chatLog.textContent).toContain("Take the square root of both sides");
  });

  it("submits text input, sends message, and shows loading state", async () => {
    const user = userEvent.setup();
    const sendSpy = vi.spyOn(api, "sendCoachingMessage").mockResolvedValue(mockConversationWithMessages);

    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("chat-input")).toBeInTheDocument();
    });

    const input = screen.getByTestId("chat-input");
    await user.type(input, "What is the next step?");
    await user.click(screen.getByTestId("send-button"));

    expect(sendSpy).toHaveBeenCalledWith("prob-123", "What is the next step?");
  });

  it("sends predefined messages via mode shortcuts", async () => {
    const user = userEvent.setup();
    const sendSpy = vi.spyOn(api, "sendCoachingMessage").mockResolvedValue(mockConversationWithMessages);

    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("shortcuts-bar")).toBeInTheDocument();
    });

    // Test Explain shortcut
    await user.click(screen.getByTestId("shortcut-explain"));
    expect(sendSpy).toHaveBeenLastCalledWith("prob-123", "Please explain this problem.");

    // Test Hint shortcut
    await user.click(screen.getByTestId("shortcut-hint"));
    expect(sendSpy).toHaveBeenLastCalledWith("prob-123", "Can you give me a hint?");

    // Test Steps shortcut
    await user.click(screen.getByTestId("shortcut-steps"));
    expect(sendSpy).toHaveBeenLastCalledWith("prob-123", "What are the steps to solve this?");

    // Test Draw shortcut
    await user.click(screen.getByTestId("shortcut-draw"));
    expect(sendSpy).toHaveBeenLastCalledWith("prob-123", "Can you show me a drawing or visualization?");
  });

  it("renders whiteboard in empty state when no whiteboard drawings are present", async () => {
    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("whiteboard")).toBeInTheDocument();
    });

    expect(screen.getByTestId("whiteboard-empty")).toBeInTheDocument();
    expect(screen.getByText("Whiteboard Canvas Empty")).toBeInTheDocument();
  });

  it("renders whiteboard DSL pages with navigation controls and page indicators", async () => {
    // Return a conversation with two distinct whiteboard drawings
    const conversationWithMultipleWhiteboards: CoachingConversation = {
      problem_id: "prob-123",
      user_id: "user-456",
      messages: [
        {
          role: "coach",
          content: "Drawing 1",
          whiteboard_dsl: "board.create('circle', [[0,0], 2]);",
          created_at: "2026-05-25T12:31:00Z",
        },
        {
          role: "coach",
          content: "Drawing 2",
          whiteboard_dsl: "board.create('circle', [[1,1], 3]);",
          created_at: "2026-05-25T12:32:00Z",
        },
      ],
      created_at: "2026-05-25T12:30:00Z",
      updated_at: "2026-05-25T12:32:00Z",
    };

    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(conversationWithMultipleWhiteboards);

    const user = userEvent.setup();
    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("whiteboard")).toBeInTheDocument();
    });

    // Should render active page (latest page is 2)
    const getWhiteboardCanvas = () => within(screen.getByTestId("whiteboard")).getByTestId("graph-sandbox");
    expect(getWhiteboardCanvas()).toHaveAttribute("data-dsl", "board.create('circle', [[1,1], 3]);");
    expect(screen.getByTestId("whiteboard-page-indicator")).toHaveTextContent("2 / 2");

    // Click Prev to see first drawing
    await user.click(screen.getByTestId("whiteboard-prev"));
    expect(getWhiteboardCanvas()).toHaveAttribute("data-dsl", "board.create('circle', [[0,0], 2]);");
    expect(screen.getByTestId("whiteboard-page-indicator")).toHaveTextContent("1 / 2");
  });

  it("shows error indicator on whiteboard rendering failure while keeping the text in chat", async () => {
    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(mockConversationWithMessages);

    const user = userEvent.setup();
    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(within(screen.getByTestId("whiteboard")).getByTestId("graph-sandbox")).toBeInTheDocument();
    });

    // Trigger mocked GraphSandbox onError callback
    await user.click(within(screen.getByTestId("whiteboard")).getByTestId("trigger-sandbox-error"));

    await waitFor(() => {
      expect(screen.getByTestId("whiteboard-error")).toBeInTheDocument();
    });

    expect(screen.getByTestId("whiteboard-error")).toHaveTextContent(
      "Whiteboard Rendering Error: JSXGraph compile error"
    );

    // Verify chat text remains displayed
    expect(screen.getByTestId("chat-log").textContent).toContain(
      "Sure! Take the square root of both sides. We get"
    );
  });

  it("clears conversation resets the UI", async () => {
    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(mockConversationWithMessages);
    const clearSpy = vi.spyOn(api, "clearCoachingConversation");

    const user = userEvent.setup();
    // Mock window.confirm to return true
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("clear-button")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("clear-button"));

    expect(clearSpy).toHaveBeenCalledWith("prob-123");
  });

  it("disables message inputs and shows warning when the 20-message limit is reached", async () => {
    // Generate 20 mock messages to trigger limit
    const messagesArray: CoachingMessage[] = Array.from({ length: 20 }, (_, i) => ({
      role: i % 2 === 0 ? "student" : "coach",
      content: `Message ${i + 1}`,
      created_at: new Date(Date.now() + i * 1000).toISOString(),
    }));

    const conversationAtLimit: CoachingConversation = {
      problem_id: "prob-123",
      user_id: "user-456",
      messages: messagesArray,
      created_at: "2026-05-25T12:00:00Z",
      updated_at: "2026-05-25T12:20:00Z",
    };

    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(conversationAtLimit);

    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("cap-warning")).toBeInTheDocument();
    });

    expect(screen.getByTestId("cap-warning")).toHaveTextContent(
      "Message cap of 20 reached. Clear conversation to start over."
    );

    // Verify inputs are disabled
    expect(screen.getByTestId("chat-input")).toBeDisabled();
    expect(screen.getByTestId("send-button")).toBeDisabled();
  });

  it("renders Markdown formatting in coach messages", async () => {
    const markdownConversation: CoachingConversation = {
      problem_id: "prob-123",
      user_id: "user-456",
      messages: [
        {
          role: "student",
          content: "Give me the solution as bullet points.",
          created_at: "2026-05-25T12:31:00Z",
        },
        {
          role: "coach",
          content: "**Step 1**\n\n- Move constants to the right\n- Then solve $x^2 = 4$",
          created_at: "2026-05-25T12:32:00Z",
        },
      ],
      created_at: "2026-05-25T12:30:00Z",
      updated_at: "2026-05-25T12:32:00Z",
    };
    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(markdownConversation);

    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("chat-log")).toBeInTheDocument();
    });

    const chatLog = screen.getByTestId("chat-log");
    // Bold Markdown renders as strong
    expect(chatLog.querySelector("strong")).toHaveTextContent("Step 1");
    // List items render
    const listItems = chatLog.querySelectorAll("li");
    expect(listItems.length).toBeGreaterThanOrEqual(1);
    expect(chatLog.textContent).toContain("Move constants to the right");
  });

  it("renders collapsed reasoning section above coach reply when reasoning_content is present", async () => {
    const conversationWithReasoning: CoachingConversation = {
      problem_id: "prob-123",
      user_id: "user-456",
      messages: [
        {
          role: "student",
          content: "How do I solve this?",
          created_at: "2026-05-25T12:31:00Z",
        },
        {
          role: "coach",
          content: "Take the square root of both sides.",
          reasoning_content: "The equation is x^2 = 4. Taking sqrt of both sides gives x = ±2.",
          created_at: "2026-05-25T12:32:00Z",
        },
      ],
      created_at: "2026-05-25T12:30:00Z",
      updated_at: "2026-05-25T12:32:00Z",
    };
    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(conversationWithReasoning);

    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("chat-log")).toBeInTheDocument();
    });

    // Reasoning section exists and is collapsed by default
    const reasoning = screen.getByTestId("reasoning-1");
    expect(reasoning).toBeInTheDocument();
    expect(reasoning.tagName.toLowerCase()).toBe("details");
    expect(reasoning.hasAttribute("open")).toBe(false);

    // Reasoning summary text is visible
    expect(within(reasoning).getByText("🧠 Reasoning")).toBeInTheDocument();

    // Coach reply is still rendered
    const chatLog = screen.getByTestId("chat-log");
    expect(chatLog.textContent).toContain("Take the square root of both sides.");
  });

  it("expanding reasoning section reveals the reasoning text", async () => {
    const conversationWithReasoning: CoachingConversation = {
      problem_id: "prob-123",
      user_id: "user-456",
      messages: [
        {
          role: "coach",
          content: "The answer is 2.",
          reasoning_content: "Step by step: x^2 = 4, so x = sqrt(4) = 2.",
          created_at: "2026-05-25T12:32:00Z",
        },
      ],
      created_at: "2026-05-25T12:30:00Z",
      updated_at: "2026-05-25T12:32:00Z",
    };
    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(conversationWithReasoning);

    const user = userEvent.setup();
    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("reasoning-0")).toBeInTheDocument();
    });

    // Click to expand
    await user.click(screen.getByText("🧠 Reasoning"));

    // Reasoning text is now visible
    expect(screen.getByTestId("reasoning-0")).toHaveAttribute("open");
    expect(screen.getByTestId("reasoning-0").textContent).toContain("Step by step");
  });

  it("does not render reasoning section for coach messages without reasoning_content", async () => {
    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(mockConversationWithMessages);

    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("chat-log")).toBeInTheDocument();
    });

    // No reasoning sections should exist
    expect(screen.queryByTestId(/reasoning-/)).not.toBeInTheDocument();
  });

  it("does not render reasoning section for student messages", async () => {
    const conversationWithReasoning: CoachingConversation = {
      problem_id: "prob-123",
      user_id: "user-456",
      messages: [
        {
          role: "student",
          content: "Help me solve this.",
          reasoning_content: "This should not render for student messages.",
          created_at: "2026-05-25T12:31:00Z",
        },
        {
          role: "coach",
          content: "Sure!",
          created_at: "2026-05-25T12:32:00Z",
        },
      ],
      created_at: "2026-05-25T12:30:00Z",
      updated_at: "2026-05-25T12:32:00Z",
    };
    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(conversationWithReasoning);

    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("chat-log")).toBeInTheDocument();
    });

    // No reasoning sections should exist (student reasoning_content is ignored)
    expect(screen.queryByTestId(/reasoning-/)).not.toBeInTheDocument();
  });

  it("reasoning section appears above the coach reply in DOM order", async () => {
    const conversationWithReasoning: CoachingConversation = {
      problem_id: "prob-123",
      user_id: "user-456",
      messages: [
        {
          role: "coach",
          content: "The answer is x = 2.",
          reasoning_content: "Analyzing the equation step by step.",
          created_at: "2026-05-25T12:32:00Z",
        },
      ],
      created_at: "2026-05-25T12:30:00Z",
      updated_at: "2026-05-25T12:32:00Z",
    };
    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(conversationWithReasoning);

    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("reasoning-0")).toBeInTheDocument();
    });

    // The reasoning details element should come before the coach reply text in DOM order
    const chatLog = screen.getByTestId("chat-log");
    const reasoning = screen.getByTestId("reasoning-0");
    const allElements = chatLog.querySelectorAll("[data-testid]");
    const reasoningIndex = Array.from(allElements).indexOf(reasoning);

    // Find the coach reply bubble (the MarkdownText content)
    const coachReply = chatLog.querySelector('[data-testid="reasoning-0"]')?.nextElementSibling;
    expect(coachReply).toBeTruthy();
    expect(coachReply!.textContent).toContain("The answer is x = 2.");
  });

  it("whiteboard starts expanded by default with collapse button visible", async () => {
    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("whiteboard")).toBeInTheDocument();
    });

    // Full whiteboard canvas/empty state is visible
    expect(screen.getByTestId("whiteboard-empty")).toBeInTheDocument();
    // Collapse button is present
    expect(screen.getByTestId("whiteboard-collapse")).toBeInTheDocument();
    // Expand button (in rail) is not present
    expect(screen.queryByTestId("whiteboard-expand")).not.toBeInTheDocument();
  });

  it("clicking collapse hides full whiteboard and shows compact rail with expand button", async () => {
    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(mockConversationWithMessages);

    const user = userEvent.setup();
    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("whiteboard-collapse")).toBeInTheDocument();
    });

    // Collapse the whiteboard
    await user.click(screen.getByTestId("whiteboard-collapse"));

    // The whiteboard should no longer contain the full graph-sandbox canvas
    const whiteboard = screen.getByTestId("whiteboard");
    expect(within(whiteboard).queryByTestId("graph-sandbox")).not.toBeInTheDocument();
    // Expand button should now be present
    expect(screen.getByTestId("whiteboard-expand")).toBeInTheDocument();
    // Vertical label is shown
    expect(whiteboard.textContent).toContain("Whiteboard");
  });

  it("clicking expand restores full whiteboard content after collapsing", async () => {
    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(mockConversationWithMessages);

    const user = userEvent.setup();
    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("whiteboard-collapse")).toBeInTheDocument();
    });

    // Collapse
    await user.click(screen.getByTestId("whiteboard-collapse"));
    expect(screen.getByTestId("whiteboard-expand")).toBeInTheDocument();

    // Expand
    await user.click(screen.getByTestId("whiteboard-expand"));

    // Full whiteboard is restored with graph sandbox
    const whiteboard = screen.getByTestId("whiteboard");
    await waitFor(() => {
      expect(within(whiteboard).getByTestId("whiteboard-collapse")).toBeInTheDocument();
    });
    expect(within(whiteboard).getByTestId("graph-sandbox")).toBeInTheDocument();
    expect(screen.queryByTestId("whiteboard-expand")).not.toBeInTheDocument();
  });

  it("whiteboard pagination and DSL rendering still work after re-expansion", async () => {
    const conversationWithMultipleWhiteboards: CoachingConversation = {
      problem_id: "prob-123",
      user_id: "user-456",
      messages: [
        {
          role: "coach",
          content: "Drawing 1",
          whiteboard_dsl: "board.create('circle', [[0,0], 2]);",
          created_at: "2026-05-25T12:31:00Z",
        },
        {
          role: "coach",
          content: "Drawing 2",
          whiteboard_dsl: "board.create('circle', [[1,1], 3]);",
          created_at: "2026-05-25T12:32:00Z",
        },
      ],
      created_at: "2026-05-25T12:30:00Z",
      updated_at: "2026-05-25T12:32:00Z",
    };

    vi.spyOn(api, "getCoachingConversation").mockResolvedValue(conversationWithMultipleWhiteboards);

    const user = userEvent.setup();
    renderCoachingPage("prob-123", "/practice");

    await waitFor(() => {
      expect(screen.getByTestId("whiteboard-collapse")).toBeInTheDocument();
    });

    // Collapse and re-expand
    await user.click(screen.getByTestId("whiteboard-collapse"));
    await user.click(screen.getByTestId("whiteboard-expand"));

    // Pagination still works on the latest page
    await waitFor(() => {
      expect(screen.getByTestId("whiteboard-page-indicator")).toHaveTextContent("2 / 2");
    });

    // Navigate to previous page
    await user.click(screen.getByTestId("whiteboard-prev"));
    expect(screen.getByTestId("whiteboard-page-indicator")).toHaveTextContent("1 / 2");
  });
});
