import { useState, useEffect, useRef } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { GraphSandbox } from "@/components/GraphSandbox";
import { CollapsibleImage } from "@/components/CollapsibleImage";
import { MarkdownText } from "@/components/MarkdownText";
import { LatexText } from "@/components/LatexText";
import type { CoachingConversation } from "@/types/coaching";

interface CorrectAnswer {
  display: string;
  normalizedText: string;
  normalizedSet: string[];
  format: string;
}

interface Problem {
  id: string;
  problemType: string;
  text: string;
  tags: string[];
  graphDsl?: string;
  imageUrl?: string;
  correctAnswer?: CorrectAnswer;
  isDeleted: boolean;
  createdAt: string;
  updatedAt: string;
}

interface ProblemResponse {
  problem: Problem;
}

interface PracticeAttemptDetail {
  submittedAnswer: string;
  gradingStatus: string;
  gradingMethod: string;
  createdAt: string;
}

interface PracticeHistoryItem {
  problemId: string;
  attempts: PracticeAttemptDetail[];
}

export interface PracticeHistoryResponse {
  items: PracticeHistoryItem[];
}

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
  const { data: practiceHistory } = useQuery({
    queryKey: ["practice-history"],
    queryFn: () => api.get<PracticeHistoryResponse>("/practice/history"),
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

  if (isLoadingProblem || isLoadingConversation) {
    return (
      <main style={{ padding: "2rem", display: "flex", justifyContent: "center", alignItems: "center", minHeight: "80vh" }}>
        <div style={{ fontSize: "1.25rem", color: "#4f46e5", fontWeight: 600, display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <div style={{ width: "24px", height: "24px", borderRadius: "50%", border: "3px solid #e0e7ff", borderTopColor: "#4f46e5", animation: "spin 1s linear infinite" }} />
          Loading coaching page...
          <style>{`@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }`}</style>
        </div>
      </main>
    );
  }

  if (!problem) {
    return (
      <main style={{ padding: "2rem", maxWidth: "600px", margin: "2rem auto" }}>
        <div style={{ padding: "1.5rem", backgroundColor: "#fef2f2", border: "1px solid #fecaca", borderRadius: "0.5rem", textAlign: "center" }}>
          <div style={{ color: "#dc2626", fontWeight: 600, fontSize: "1.125rem", marginBottom: "0.5rem" }}>Problem Not Found</div>
          <p style={{ color: "#7f1d1d", margin: "0 0 1.5rem 0" }}>The problem you are trying to access does not exist or has been deleted.</p>
          <button onClick={() => navigate(fromRoute)} style={{ padding: "0.5rem 1.5rem", backgroundColor: "#dc2626", color: "white", border: "none", borderRadius: "0.25rem", cursor: "pointer", fontWeight: 600 }}>
            Go Back
          </button>
        </div>
      </main>
    );
  }

  const messageCount = conversation?.messages?.length || 0;
  const isCapped = messageCount >= 20;

  return (
    <main style={{ padding: "1.5rem", minHeight: "calc(100vh - 60px)", display: "flex", flexDirection: "column", gap: "1rem", backgroundColor: "#f8fafc" }}>
      {/* Header controls */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "1rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <button
            onClick={() => navigate(fromRoute)}
            data-testid="back-button"
            style={{
              padding: "0.5rem 1rem",
              borderRadius: "0.375rem",
              border: "1px solid #e2e8f0",
              backgroundColor: "white",
              color: "#334155",
              cursor: "pointer",
              fontWeight: 600,
              fontSize: "0.875rem",
              display: "flex",
              alignItems: "center",
              gap: "0.25rem",
              boxShadow: "0 1px 2px rgba(0, 0, 0, 0.05)",
            }}
          >
            ← Back to Review
          </button>
          <h1 style={{ margin: 0, fontSize: "1.5rem", color: "#0f172a", fontWeight: 700 }}>AI Coach</h1>
        </div>

        <button
          onClick={() => {
            if (window.confirm("Are you sure you want to clear this entire conversation history? This will also empty the whiteboard.")) {
              clearConversationMutation.mutate();
            }
          }}
          disabled={clearConversationMutation.isPending || messageCount === 0}
          data-testid="clear-button"
          style={{
            padding: "0.5rem 1rem",
            borderRadius: "0.375rem",
            backgroundColor: "#fff1f2",
            color: messageCount === 0 ? "#cbd5e1" : "#e11d48",
            border: "1px solid #ffe4e6",
            cursor: messageCount === 0 || clearConversationMutation.isPending ? "not-allowed" : "pointer",
            fontWeight: 600,
            fontSize: "0.875rem",
            boxShadow: "0 1px 2px rgba(0, 0, 0, 0.05)",
            transition: "all 0.15s ease",
          }}
        >
          {clearConversationMutation.isPending ? "Clearing..." : "Clear Conversation"}
        </button>
      </div>

      {/* Main split layout container */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.2fr", gap: "1.5rem", flex: 1, minHeight: "650px", alignItems: "stretch" }}>
        
        {/* Left side: Context + Chat Area */}
        <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
          
          {/* 1. Context Bar */}
          <div
            data-testid="context-bar"
            style={{
              padding: "1rem 1.25rem",
              backgroundColor: "white",
              borderRadius: "0.75rem",
              border: "1px solid #e2e8f0",
              boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)",
              display: "flex",
              flexDirection: "column",
              gap: "0.75rem",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #f1f5f9", paddingBottom: "0.5rem" }}>
              <strong style={{ fontSize: "0.875rem", color: "#475569" }}>Problem Snapshot</strong>
              {gradingStatus && (
                <span
                  style={{
                    fontSize: "0.75rem",
                    fontWeight: 600,
                    padding: "0.25rem 0.5rem",
                    borderRadius: "0.25rem",
                    backgroundColor: gradingStatus === "correct" ? "#dcfce7" : gradingStatus === "incorrect" ? "#fee2e2" : "#fef3c7",
                    color: gradingStatus === "correct" ? "#166534" : gradingStatus === "incorrect" ? "#991b1b" : "#854d0e",
                  }}
                >
                  {gradingStatus.charAt(0).toUpperCase() + gradingStatus.slice(1)}
                </span>
              )}
            </div>

            <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap", alignItems: "flex-start" }}>
              <div style={{ flex: 1, minWidth: "200px" }}>
                <LatexText text={problem.text} style={{ fontSize: "0.925rem", color: "#1e293b", lineHeight: 1.5, whiteSpace: "pre-wrap" }} />
              </div>
              
              {/* Problem figure displayed in context bar only, NOT in whiteboard (FR-32) */}
              {problem.graphDsl && (
                <div style={{ width: "200px" }} data-testid="context-original-figure">
                  <GraphSandbox dsl={problem.graphDsl} height={140} />
                </div>
              )}
              {problem.imageUrl && !problem.graphDsl && (
                <div style={{ width: "150px" }}>
                  <CollapsibleImage src={problem.imageUrl} alt="Original Figure" style={{ maxWidth: "100%", maxHeight: "120px", borderRadius: "0.375rem" }} />
                </div>
              )}
            </div>

            {studentAnswer && (
              <div style={{ padding: "0.5rem 0.75rem", backgroundColor: "#f8fafc", borderRadius: "0.375rem", borderLeft: "3px solid #cbd5e1", fontSize: "0.875rem" }}>
                <span style={{ color: "#64748b", fontWeight: 500, marginRight: "0.5rem" }}>Your Answer:</span>
                <strong data-testid="student-answer" style={{ color: "#334155" }}>{studentAnswer}</strong>
              </div>
            )}
          </div>

          {/* 2. Chat Interface */}
          <div
            style={{
              flex: 1,
              backgroundColor: "white",
              borderRadius: "0.75rem",
              border: "1px solid #e2e8f0",
              boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)",
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
                padding: "1.25rem",
                overflowY: "auto",
                display: "flex",
                flexDirection: "column",
                gap: "1rem",
                maxHeight: "450px",
              }}
            >
              {conversation?.messages?.length === 0 ? (
                <div style={{ margin: "auto", textAlign: "center", color: "#94a3b8", padding: "2rem" }}>
                  <div style={{ fontSize: "2.5rem", marginBottom: "0.5rem" }}>💬</div>
                  <strong>No messages yet</strong>
                  <p style={{ margin: "0.25rem 0 0 0", fontSize: "0.875rem" }}>Ask the coach to explain the problem or start with a shortcut below.</p>
                </div>
              ) : (
                conversation?.messages?.map((msg, index) => {
                  const isStudent = msg.role === "student";
                  return (
                    <div
                      key={index}
                      style={{
                        display: "flex",
                        justifyContent: isStudent ? "flex-end" : "flex-start",
                        width: "100%",
                      }}
                    >
                      <div
                        style={{
                          maxWidth: "85%",
                          padding: "0.75rem 1rem",
                          borderRadius: "0.75rem",
                          borderBottomRightRadius: isStudent ? "0px" : "0.75rem",
                          borderBottomLeftRadius: isStudent ? "0.75rem" : "0px",
                          backgroundColor: isStudent ? "#e0e7ff" : "#f1f5f9",
                          color: isStudent ? "#312e81" : "#1e293b",
                          border: isStudent ? "1px solid #c7d2fe" : "1px solid #e2e8f0",
                          fontSize: "0.925rem",
                          lineHeight: 1.5,
                        }}
                      >
                        <MarkdownText content={msg.content} />
                      </div>
                    </div>
                  );
                })
              )}

              {sendMessageMutation.isPending && (
                <div style={{ display: "flex", justifyContent: "flex-start" }}>
                  <div
                    style={{
                      padding: "0.75rem 1rem",
                      borderRadius: "0.75rem",
                      borderBottomLeftRadius: "0px",
                      backgroundColor: "#f8fafc",
                      color: "#64748b",
                      border: "1px dashed #cbd5e1",
                      fontSize: "0.875rem",
                      display: "flex",
                      alignItems: "center",
                      gap: "0.5rem",
                    }}
                  >
                    <div style={{ display: "flex", gap: "3px" }}>
                      <div style={{ width: "6px", height: "6px", backgroundColor: "#94a3b8", borderRadius: "50%", animation: "bounce 1.4s infinite ease-in-out both" }} />
                      <div style={{ width: "6px", height: "6px", backgroundColor: "#94a3b8", borderRadius: "50%", animation: "bounce 1.4s infinite ease-in-out both 0.2s" }} />
                      <div style={{ width: "6px", height: "6px", backgroundColor: "#94a3b8", borderRadius: "50%", animation: "bounce 1.4s infinite ease-in-out both 0.4s" }} />
                    </div>
                    Coach is thinking...
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
                    style={{
                      padding: "0.75rem 1rem",
                      borderRadius: "0.75rem",
                      borderBottomLeftRadius: "0px",
                      backgroundColor: "#fff1f2",
                      color: "#b91c1c",
                      border: "1px solid #fecaca",
                      fontSize: "0.875rem",
                    }}
                  >
                    <strong>Error:</strong> {chatError}
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>

            {/* Chat bottom control panel */}
            <div style={{ padding: "1rem", borderTop: "1px solid #e2e8f0", backgroundColor: "#f8fafc", display: "flex", flexDirection: "column", gap: "0.75rem" }}>
              
              {/* Shortcut buttons */}
              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }} data-testid="shortcuts-bar">
                {(["Explain", "Hint", "Steps", "Draw"] as const).map((shortcut) => (
                  <button
                    key={shortcut}
                    onClick={() => handleShortcutClick(shortcut)}
                    disabled={isCapped || sendMessageMutation.isPending}
                    data-testid={`shortcut-${shortcut.toLowerCase()}`}
                    style={{
                      padding: "0.375rem 0.75rem",
                      borderRadius: "0.25rem",
                      border: "1px solid #cbd5e1",
                      backgroundColor: "white",
                      color: "#475569",
                      cursor: isCapped || sendMessageMutation.isPending ? "not-allowed" : "pointer",
                      fontSize: "0.75rem",
                      fontWeight: 600,
                      boxShadow: "0 1px 2px rgba(0, 0, 0, 0.02)",
                      transition: "all 0.15s ease",
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
                    padding: "0.625rem 0.875rem",
                    borderRadius: "0.375rem",
                    border: "1px solid #cbd5e1",
                    fontSize: "0.875rem",
                    outline: "none",
                    boxShadow: "inset 0 1px 2px rgba(0, 0, 0, 0.05)",
                    backgroundColor: isCapped || sendMessageMutation.isPending ? "#f1f5f9" : "white",
                    cursor: isCapped || sendMessageMutation.isPending ? "not-allowed" : "text",
                  }}
                />
                <button
                  type="submit"
                  disabled={isCapped || !messageText.trim() || sendMessageMutation.isPending}
                  data-testid="send-button"
                  style={{
                    padding: "0.625rem 1.25rem",
                    borderRadius: "0.375rem",
                    backgroundColor: isCapped || !messageText.trim() || sendMessageMutation.isPending ? "#a5b4fc" : "#4f46e5",
                    color: "white",
                    border: "none",
                    cursor: isCapped || !messageText.trim() || sendMessageMutation.isPending ? "not-allowed" : "pointer",
                    fontWeight: 600,
                    fontSize: "0.875rem",
                    boxShadow: "0 1px 2px rgba(79, 70, 229, 0.2)",
                  }}
                >
                  Send
                </button>
              </form>

              {/* Message capacity warning */}
              {isCapped && (
                <div data-testid="cap-warning" style={{ fontSize: "0.75rem", color: "#e11d48", fontWeight: 500, textAlign: "center", marginTop: "0.25rem" }}>
                  ⚠️ Message cap of 20 reached. Clear conversation to start over.
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Right side: Interactive Whiteboard Area */}
        <div
          data-testid="whiteboard"
          style={{
            backgroundColor: "white",
            borderRadius: "0.75rem",
            border: "1px solid #e2e8f0",
            boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)",
            padding: "1.25rem",
            display: "flex",
            flexDirection: "column",
            gap: "1rem",
            alignItems: "stretch",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #f1f5f9", paddingBottom: "0.75rem" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <span style={{ fontSize: "1.25rem" }}>🎨</span>
              <strong style={{ fontSize: "1rem", color: "#0f172a", fontWeight: 700 }}>Interactive Whiteboard</strong>
            </div>
            
            {/* Whiteboard pagination controls */}
            {dslPages.length > 0 && (
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                <button
                  onClick={() => {
                    setCurrentPageIndex((prev) => Math.max(0, prev - 1));
                    setWhiteboardError(null);
                  }}
                  disabled={currentPageIndex === 0}
                  data-testid="whiteboard-prev"
                  style={{
                    padding: "0.25rem 0.5rem",
                    borderRadius: "0.25rem",
                    border: "1px solid #e2e8f0",
                    backgroundColor: "white",
                    cursor: currentPageIndex === 0 ? "not-allowed" : "pointer",
                    fontSize: "0.875rem",
                    fontWeight: 600,
                  }}
                >
                  ◀
                </button>
                
                <span data-testid="whiteboard-page-indicator" style={{ fontSize: "0.875rem", fontWeight: 600, color: "#475569" }}>
                  {currentPageIndex + 1} / {dslPages.length}
                </span>

                <button
                  onClick={() => {
                    setCurrentPageIndex((prev) => Math.min(dslPages.length - 1, prev + 1));
                    setWhiteboardError(null);
                  }}
                  disabled={currentPageIndex === dslPages.length - 1}
                  data-testid="whiteboard-next"
                  style={{
                    padding: "0.25rem 0.5rem",
                    borderRadius: "0.25rem",
                    border: "1px solid #e2e8f0",
                    backgroundColor: "white",
                    cursor: currentPageIndex === dslPages.length - 1 ? "not-allowed" : "pointer",
                    fontSize: "0.875rem",
                    fontWeight: 600,
                  }}
                >
                  ▶
                </button>
              </div>
            )}
          </div>

          {/* Canvas area wrapper */}
          <div style={{ flex: 1, position: "relative", minHeight: "400px" }}>
            {dslPages.length === 0 ? (
              
              /* Whiteboard empty state (FR-30) */
              <div
                data-testid="whiteboard-empty"
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  right: 0,
                  bottom: 0,
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  backgroundColor: "#f8fafc",
                  borderRadius: "0.5rem",
                  border: "2px dashed #cbd5e1",
                  color: "#94a3b8",
                  padding: "2rem",
                  textAlign: "center",
                }}
              >
                <div style={{ fontSize: "3.5rem", marginBottom: "1rem" }}>✏️</div>
                <strong style={{ fontSize: "1.125rem", color: "#64748b" }}>Whiteboard Canvas Empty</strong>
                <p style={{ margin: "0.5rem 0 0 0", fontSize: "0.875rem", maxWidth: "320px", lineHeight: 1.4 }}>
                  No whiteboard drawings have been loaded yet. Ask the coach to generate a drawing or click a shortcut like "Draw"!
                </p>
              </div>
            ) : (
              
              /* Renders DSL pages with JSXGraph GraphSandbox */
              <div style={{ width: "100%", height: "100%" }}>
                {whiteboardError && (
                  
                  /* Custom error indicator shown when JSXGraph fails (FR-31) */
                  <div
                    data-testid="whiteboard-error"
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      right: 0,
                      zIndex: 20,
                      padding: "1rem",
                      backgroundColor: "#fef2f2",
                      border: "1px solid #fecaca",
                      borderRadius: "0.375rem",
                      color: "#b91c1c",
                      fontSize: "0.875rem",
                      boxShadow: "0 2px 4px rgba(0, 0, 0, 0.05)",
                    }}
                  >
                    <strong>Whiteboard Rendering Error:</strong> {whiteboardError}
                  </div>
                )}
                
                <GraphSandbox
                  key={`whiteboard-canvas-${currentPageIndex}`}
                  dsl={dslPages[currentPageIndex]}
                  onError={(error) => setWhiteboardError(error)}
                  onRender={() => setWhiteboardError(null)}
                  height="100%"
                />
              </div>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
