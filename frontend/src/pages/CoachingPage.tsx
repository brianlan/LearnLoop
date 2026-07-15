import { useState, useEffect, useRef } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { GraphSandbox } from "@/components/GraphSandbox";
import { CollapsibleImage } from "@/components/CollapsibleImage";
import { MarkdownText } from "@/components/MarkdownText";
import { LatexText } from "@/components/LatexText";
import type { CoachingConversation } from "@/types/coaching";
import type { ProblemDetail, ProblemResponse } from "@/types/problem";
import { getPracticeHistory, PRACTICE_HISTORY_KEY } from "@/api/practice";
import type { PracticeHistoryResponse } from "@/types/practice";
import { WhiteboardPanel } from "./WhiteboardPanel";

interface ExamItem {
  problemId: string;
  answer: {
    raw?: string;
  };
  grading: {
    status: string;
    isCorrect?: boolean;
  };
}

export interface ExamResponse {
  exam: {
    id: string;
    items: ExamItem[];
  };
}

export function CoachingPage() {
  const { problemId = "" } = useParams<{ problemId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Dynamic back navigation route
  const fromRoute = (location.state as { from?: string } | null)?.from ?? "/practice";

  // Form input state
  const [messageText, setMessageText] = useState("");
  const [whiteboardError, setWhiteboardError] = useState<string | null>(null);
  const [chatError, setChatError] = useState<string | null>(null);

  // Whiteboard collapse state
  const [isWhiteboardCollapsed, setIsWhiteboardCollapsed] = useState(false);

  // Active whiteboard page index
  const [currentPageIndex, setCurrentPageIndex] = useState(0);

  // 1. Fetch Problem Details
  const { data: problem, isLoading: isLoadingProblem } = useQuery({
    queryKey: ["problem", problemId],
    queryFn: async () => {
      const data = await api.get<ProblemResponse>(`/problems/${problemId}`);
      return data.problem;
    },
    enabled: !!problemId,
  });

  // 2. Fetch Practice History (if referrer is practice)
  const isFromPractice = fromRoute === "/practice" || fromRoute.startsWith("/practice");
  const { data: practiceHistory } = useQuery<PracticeHistoryResponse>({
    queryKey: PRACTICE_HISTORY_KEY,
    queryFn: getPracticeHistory,
    enabled: !!problemId && isFromPractice,
  });

  // 3. Fetch Exam Details (if referrer is an exam)
  const isFromExam = fromRoute.startsWith("/exams/");
  const examId = isFromExam ? fromRoute.split("/")[2] : "";
  const { data: examData } = useQuery({
    queryKey: ["exam", examId],
    queryFn: () => api.get<ExamResponse>(`/exams/${examId}`),
    enabled: !!problemId && !!examId,
  });

  // Extract student answer & judgement dynamically
  let studentAnswer = "";
  let gradingStatus = "";

  if (isFromPractice && practiceHistory) {
    const matchedProblem = practiceHistory.items?.find((item) => item.problemId === problemId);
    if (matchedProblem && matchedProblem.attempts?.length > 0) {
      studentAnswer = matchedProblem.attempts[0].submittedAnswer;
      gradingStatus = matchedProblem.attempts[0].gradingStatus;
    }
  } else if (isFromExam && examData) {
    const matchedItem = examData.exam?.items?.find((item) => item.problemId === problemId);
    if (matchedItem) {
      studentAnswer = matchedItem.answer.raw || "";
      gradingStatus = matchedItem.grading.status;
    }
  }

  // 4. Fetch Coaching Conversation
  const { data: conversation, isLoading: isLoadingConversation } = useQuery<CoachingConversation>({
    queryKey: ["coaching-conversation", problemId],
    queryFn: () => api.getCoachingConversation(problemId),
    enabled: !!problemId,
  });

  // Extract whiteboard pages (unique whiteboard_dsl codes in order)
  const dslPages = conversation?.messages
    ?.filter((m) => m.role === "coach" && m.whiteboard_dsl)
    ?.map((m) => m.whiteboard_dsl as string) || [];



  // Keep index within boundaries when pages change
  useEffect(() => {
    if (dslPages.length > 0) {
      setCurrentPageIndex(dslPages.length - 1);
      setWhiteboardError(null);
    } else {
      setCurrentPageIndex(0);
    }
  }, [dslPages.length]);

  // Scroll to bottom of chat when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [conversation?.messages?.length]);

  // Mutations
  const sendMessageMutation = useMutation({
    mutationFn: (text: string) => api.sendCoachingMessage(problemId, text),
    onSuccess: (updatedConversation) => {
      queryClient.setQueryData(["coaching-conversation", problemId], updatedConversation);
      setMessageText("");
      setChatError(null);
    },
    onError: (err) => {
      setChatError(err instanceof Error ? err.message : "Failed to send message");
    },
  });

  const clearConversationMutation = useMutation({
    mutationFn: () => api.clearCoachingConversation(problemId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["coaching-conversation", problemId] });
      setCurrentPageIndex(0);
      setWhiteboardError(null);
      setChatError(null);
    },
    onError: (err) => {
      setChatError(err instanceof Error ? err.message : "Failed to clear conversation");
    },
  });

  const handleSendMessage = (text: string) => {
    if (!text.trim() || sendMessageMutation.isPending) return;
    sendMessageMutation.mutate(text);
  };

  const handleShortcutClick = (shortcutType: "Explain" | "Hint" | "Steps" | "Draw") => {
    const predefinedMessages = {
      Explain: "Please explain this problem.",
      Hint: "Can you give me a hint?",
      Steps: "What are the steps to solve this?",
      Draw: "Can you show me a drawing or visualization?",
    };
    handleSendMessage(predefinedMessages[shortcutType]);
  };

  const pageCanvasStyle: React.CSSProperties = {
    minHeight: "calc(100vh - 60px)",
    backgroundColor: "var(--color-surface-muted)",
    color: "var(--color-text)",
    padding: "1.5rem",
  };

  if (isLoadingProblem || isLoadingConversation) {
    return (
      <main style={{ ...pageCanvasStyle, display: "flex", justifyContent: "center", alignItems: "center" }}>
        <div style={{ fontSize: "1.25rem", color: "var(--color-link)", fontWeight: 600, display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <div style={{ width: "24px", height: "24px", borderRadius: "50%", border: "3px solid var(--color-tag-bg)", borderTopColor: "var(--color-link)", animation: "spin 1s linear infinite" }} />
          Loading coaching page...
          <style>{`@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }`}</style>
        </div>
      </main>
    );
  }

  if (!problem) {
    return (
      <main style={pageCanvasStyle}>
        <div style={{ maxWidth: "600px", margin: "0 auto" }}>
          <div style={{ padding: "1.5rem", backgroundColor: "var(--color-danger-bg)", border: "1px solid var(--color-danger-border)", borderRadius: "0.5rem", textAlign: "center" }}>
            <div style={{ color: "var(--color-text-danger)", fontWeight: 600, fontSize: "1.125rem", marginBottom: "0.5rem" }}>Problem Not Found</div>
            <p style={{ color: "var(--color-text-danger-secondary)", margin: "0 0 1.5rem 0" }}>The problem you are trying to access does not exist or has been deleted.</p>
            <button onClick={() => navigate(fromRoute)} style={{ padding: "0.5rem 1.5rem", backgroundColor: "var(--color-danger)", color: "white", border: "none", borderRadius: "0.25rem", cursor: "pointer", fontWeight: 600 }}>
              Go Back
            </button>
          </div>
        </div>
      </main>
    );
  }

  const messageCount = conversation?.messages?.length || 0;
  const isCapped = messageCount >= 20;

  return (
    <main style={{ padding: "2rem 1.5rem", minHeight: "calc(100vh - 60px)", display: "flex", flexDirection: "column", gap: "1.5rem", backgroundColor: "var(--color-bg)", color: "var(--color-text)" }}>
      {/* Header controls */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "1.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <button
            onClick={() => navigate(fromRoute)}
            data-testid="back-button"
            className="btn btn-secondary"
            style={{
              padding: "0.5rem 1rem",
              fontSize: "0.875rem",
              fontWeight: 700,
            }}
          >
            ← Back to Review
          </button>
          <h1 style={{ margin: 0, fontSize: "1.75rem", color: "var(--color-text)", fontWeight: 800, letterSpacing: "-0.02em" }}>AI Coach</h1>
        </div>

        <button
          onClick={() => {
            if (window.confirm("Are you sure you want to clear this entire conversation history? This will also empty the whiteboard.")) {
              clearConversationMutation.mutate();
            }
          }}
          disabled={clearConversationMutation.isPending || messageCount === 0}
          data-testid="clear-button"
          className="btn btn-danger"
          style={{
            padding: "0.5rem 1rem",
            fontSize: "0.875rem",
            fontWeight: 700,
            backgroundColor: messageCount === 0 ? "var(--color-surface-muted)" : "var(--color-danger)",
            color: messageCount === 0 ? "var(--color-disabled-text)" : "white",
            border: messageCount === 0 ? "1px solid var(--color-border)" : "none",
            opacity: messageCount === 0 ? 0.6 : 1
          }}
        >
          {clearConversationMutation.isPending ? "Clearing..." : "Clear Conversation"}
        </button>
      </div>

      {/* Main split layout container */}
      <div style={{ display: "grid", gridTemplateColumns: isWhiteboardCollapsed ? "1fr 48px" : "1.2fr 1fr", gap: "1.5rem", flex: 1, minHeight: "650px", alignItems: "stretch" }}>
        
        {/* Left side: Context + Chat Area */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1.25rem" }}>
          
          {/* 1. Context Bar */}
          <div
            data-testid="context-bar"
            className="card-premium"
            style={{
              padding: "1.25rem",
              backgroundColor: "var(--color-surface)",
              borderRadius: "var(--radius-lg)",
              border: "1px solid var(--color-border)",
              display: "flex",
              flexDirection: "column",
              gap: "1rem",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid var(--color-border)", paddingBottom: "0.5rem" }}>
              <strong style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)" }}>Problem Snapshot</strong>
              {gradingStatus && (
                <span
                  className={`badge ${gradingStatus === "correct" ? "badge-success" : gradingStatus === "incorrect" ? "badge-danger" : "badge-warning"}`}
                  style={{
                    fontSize: "0.75rem",
                    fontWeight: 700,
                    padding: "0.2rem 0.6rem",
                    borderRadius: "var(--radius-full)",
                    textTransform: "capitalize",
                    letterSpacing: "normal"
                  }}
                >
                  {gradingStatus.charAt(0).toUpperCase() + gradingStatus.slice(1)}
                </span>
              )}
            </div>

            <div style={{ display: "flex", gap: "1.5rem", flexWrap: "wrap", alignItems: "flex-start" }}>
              <div style={{ flex: 1, minWidth: "200px" }}>
                <LatexText text={problem.text} style={{ fontSize: "1rem", color: "var(--color-text)", lineHeight: 1.5, whiteSpace: "pre-wrap" }} />
              </div>

              {/* Problem figure displayed in context bar only, NOT in whiteboard */}
              {problem.graphDsl && (
                <div style={{ width: "200px" }} data-testid="context-original-figure">
                  <GraphSandbox dsl={problem.graphDsl} height={140} />
                </div>
              )}
              {problem.imageUrl && !problem.graphDsl && (
                <div style={{ width: "150px" }}>
                  <CollapsibleImage src={problem.imageUrl} alt="Original Figure" style={{ maxWidth: "100%", maxHeight: "120px", borderRadius: "var(--radius-md)" }} />
                </div>
              )}
            </div>

            {studentAnswer && (
              <div style={{ padding: "0.6rem 0.8rem", backgroundColor: "var(--color-surface-muted)", borderRadius: "var(--radius-md)", borderLeft: "3px solid var(--color-primary)", fontSize: "0.875rem", display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <span style={{ color: "var(--color-text-muted)", fontWeight: 600 }}>Your Answer:</span>
                <strong data-testid="student-answer" style={{ color: "var(--color-text)", fontWeight: 700 }}>{studentAnswer}</strong>
              </div>
            )}
          </div>

          {/* 2. Chat Interface */}
          <div
            style={{
              flex: 1,
              backgroundColor: "var(--color-surface)",
              borderRadius: "var(--radius-lg)",
              border: "1px solid var(--color-border)",
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
            }}
          >
            {/* Scrollable messages log */}
            <div
              data-testid="chat-log"
              style={{
                flex: 1,
                padding: "1.5rem",
                overflowY: "auto",
                display: "flex",
                flexDirection: "column",
                gap: "1.25rem",
                maxHeight: "450px",
              }}
            >
              {conversation?.messages?.length === 0 ? (
                <div style={{ margin: "auto", textAlign: "center", color: "var(--color-text-muted)", padding: "3rem 2rem" }}>
                  <div style={{ fontSize: "3rem", marginBottom: "0.75rem" }}>💬</div>
                  <strong style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--color-text)" }}>No messages yet</strong>
                  <p style={{ margin: "0.375rem 0 0 0", fontSize: "0.875rem", fontWeight: 500 }}>Ask the coach to explain the problem or start with a shortcut below.</p>
                </div>
              ) : (
                conversation?.messages?.map((msg, index) => {
                  const isStudent = msg.role === "student";
                  const hasReasoning = !isStudent && msg.reasoning_content?.trim();
                  return (
                    <div
                      key={index}
                      style={{
                        display: "flex",
                        justifyContent: isStudent ? "flex-end" : "flex-start",
                        width: "100%",
                      }}
                    >
                      <div style={{ maxWidth: "85%", display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                        {hasReasoning && (
                          <details
                            data-testid={`reasoning-${index}`}
                            style={{
                              fontSize: "0.8125rem",
                              color: "var(--color-text-muted)",
                              backgroundColor: "var(--color-surface-muted)",
                              border: "1px solid var(--color-border)",
                              borderRadius: "var(--radius-md)",
                              padding: "0.5rem 0.75rem",
                              lineHeight: 1.4,
                            }}
                          >
                            <summary style={{ cursor: "pointer", fontWeight: 700, userSelect: "none", outline: "none" }}>
                              🧠 Reasoning
                            </summary>
                            <div style={{ marginTop: "0.5rem", whiteSpace: "pre-wrap", color: "var(--color-text)" }}>
                              <MarkdownText content={msg.reasoning_content!} />
                            </div>
                          </details>
                        )}
                        <div
                          style={{
                            padding: "0.8rem 1.1rem",
                            borderRadius: "var(--radius-lg)",
                            borderBottomRightRadius: isStudent ? "0px" : "var(--radius-lg)",
                            borderBottomLeftRadius: isStudent ? "var(--radius-lg)" : "0px",
                            backgroundColor: isStudent ? "var(--color-primary)" : "var(--color-surface-muted)",
                            color: isStudent ? "white" : "var(--color-text)",
                            border: isStudent ? "none" : "1px solid var(--color-border)",
                            fontSize: "0.95rem",
                            lineHeight: 1.5,
                            boxShadow: "0 2px 4px rgba(0,0,0,0.02)",
                          }}
                        >
                          <MarkdownText content={msg.content} />
                        </div>
                      </div>
                    </div>
                  );
                })
              )}

              {sendMessageMutation.isPending && (
                <div style={{ display: "flex", justifyContent: "flex-start" }}>
                  <div
                    style={{
                      padding: "0.8rem 1.1rem",
                      borderRadius: "var(--radius-lg)",
                      borderBottomLeftRadius: "0px",
                      backgroundColor: "var(--color-surface-muted)",
                      color: "var(--color-text-muted)",
                      border: "1px dashed var(--color-border)",
                      fontSize: "0.875rem",
                      display: "flex",
                      alignItems: "center",
                      gap: "0.6rem",
                    }}
                  >
                    <div style={{ display: "flex", gap: "4px" }}>
                      <div style={{ width: "6px", height: "6px", backgroundColor: "var(--color-text-muted)", borderRadius: "50%", animation: "bounce 1.4s infinite ease-in-out both" }} />
                      <div style={{ width: "6px", height: "6px", backgroundColor: "var(--color-text-muted)", borderRadius: "50%", animation: "bounce 1.4s infinite ease-in-out both 0.2s" }} />
                      <div style={{ width: "6px", height: "6px", backgroundColor: "var(--color-text-muted)", borderRadius: "50%", animation: "bounce 1.4s infinite ease-in-out both 0.4s" }} />
                    </div>
                    <span style={{ fontWeight: 600 }}>Coach is thinking...</span>
                    <style>{`
                      @keyframes bounce {
                        0%, 80%, 100% { transform: scale(0); }
                        40% { transform: scale(1.0); }
                      }
                    `}</style>
                  </div>
                </div>
              )}

              {chatError && (
                <div style={{ display: "flex", justifyContent: "flex-start" }} data-testid="chat-error">
                  <div
                    className="badge badge-danger"
                    style={{
                      padding: "0.75rem 1rem",
                      fontSize: "0.875rem",
                      textTransform: "none",
                      letterSpacing: "normal",
                      fontWeight: 500,
                      borderRadius: "var(--radius-lg)",
                      borderBottomLeftRadius: "0px",
                    }}
                  >
                    <strong>Error:</strong> {chatError}
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>

            {/* Chat bottom control panel */}
            <div style={{ padding: "1.25rem", borderTop: "1px solid var(--color-border)", backgroundColor: "var(--color-surface-muted)", display: "flex", flexDirection: "column", gap: "1rem" }}>

              {/* Shortcut buttons */}
              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }} data-testid="shortcuts-bar">
                {(["Explain", "Hint", "Steps", "Draw"] as const).map((shortcut) => (
                  <button
                    key={shortcut}
                    onClick={() => handleShortcutClick(shortcut)}
                    disabled={isCapped || sendMessageMutation.isPending}
                    data-testid={`shortcut-${shortcut.toLowerCase()}`}
                    className="btn btn-secondary"
                    style={{
                      padding: "0.4rem 0.8rem",
                      borderRadius: "var(--radius-full)",
                      fontSize: "0.75rem",
                      fontWeight: 700,
                      boxShadow: "0 1px 2px rgba(0, 0, 0, 0.02)",
                    }}
                  >
                    {shortcut === "Explain" && "💡 Explain"}
                    {shortcut === "Hint" && "🔑 Hint"}
                    {shortcut === "Steps" && "📋 Steps"}
                    {shortcut === "Draw" && "🎨 Draw"}
                  </button>
                ))}
              </div>

              {/* Chat Input form */}
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  handleSendMessage(messageText);
                }}
                style={{ display: "flex", gap: "0.5rem" }}
              >
                <input
                  type="text"
                  value={messageText}
                  onChange={(e) => setMessageText(e.target.value)}
                  disabled={isCapped || sendMessageMutation.isPending}
                  placeholder={
                    isCapped
                      ? "Message limit reached"
                      : "Type your question here (e.g. Can you explain step 2?)..."
                  }
                  data-testid="chat-input"
                  style={{
                    flex: 1,
                    padding: "0.6rem 0.8rem",
                    borderRadius: "var(--radius-md)",
                    border: "1px solid var(--color-border)",
                    fontSize: "0.9rem",
                    outline: "none",
                    backgroundColor: isCapped || sendMessageMutation.isPending ? "var(--color-bg)" : "var(--color-bg)",
                    color: "var(--color-text)",
                    cursor: isCapped || sendMessageMutation.isPending ? "not-allowed" : "text",
                  }}
                />
                <button
                  type="submit"
                  disabled={isCapped || !messageText.trim() || sendMessageMutation.isPending}
                  data-testid="send-button"
                  className="btn btn-primary"
                  style={{
                    padding: "0.6rem 1.25rem",
                    fontSize: "0.875rem",
                    fontWeight: 700,
                  }}
                >
                  Send
                </button>
              </form>

              {/* Message capacity warning */}
              {isCapped && (
                <div data-testid="cap-warning" style={{ fontSize: "0.75rem", color: "var(--color-text-danger)", fontWeight: 700, textAlign: "center", marginTop: "0.25rem" }}>
                  ⚠️ Message cap of 20 reached. Clear conversation to start over.
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right side: Interactive Whiteboard Area */}
        <WhiteboardPanel
          isWhiteboardCollapsed={isWhiteboardCollapsed}
          setIsWhiteboardCollapsed={setIsWhiteboardCollapsed}
          dslPages={dslPages}
          currentPageIndex={currentPageIndex}
          setCurrentPageIndex={setCurrentPageIndex}
          whiteboardError={whiteboardError}
          setWhiteboardError={setWhiteboardError}
        />
      </div>
    </main>
  );
}
