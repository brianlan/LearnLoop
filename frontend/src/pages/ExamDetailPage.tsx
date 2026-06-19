import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { formatDate, formatScore } from "@/utils/format";
import { GraphSandbox } from "@/components/GraphSandbox";
import { CollapsibleImage } from "@/components/CollapsibleImage";
import { LatexText } from "@/components/LatexText";
import { TeacherPasswordModal } from "@/components/TeacherPasswordModal";
import type { ExamItem, ExamResponse, SelfReportRequest, SelfReportResponse } from "@/types/exam";

async function fetchExam(examId: string): Promise<ExamResponse> {
  return api.get<ExamResponse>(`/exams/${examId}`);
}

async function selfReportItem(
  examId: string,
  itemId: string,
  request: SelfReportRequest,
): Promise<SelfReportResponse> {
  return api.post<SelfReportResponse>(`/exams/${examId}/items/${itemId}/self-report`, request);
}

function GradingStatusBadge({ status }: { status: string }) {
  const classes: Record<string, string> = {
    correct: "badge-success",
    incorrect: "badge-danger",
    "pending-review": "badge-warning",
    ungraded: "badge-muted",
  };

  const labels: Record<string, string> = {
    correct: "Correct",
    incorrect: "Incorrect",
    "pending-review": "Pending Review",
    ungraded: "Ungraded",
  };

  return (
    <span
      className={`badge ${classes[status] || "badge-muted"}`}
      style={{
        padding: "0.2rem 0.6rem",
        borderRadius: "var(--radius-full)",
        fontSize: "0.75rem",
        fontWeight: 600,
        textTransform: "none",
        letterSpacing: "normal"
      }}
    >
      {labels[status] || status}
    </span>
  );
}

interface ExamItemReviewProps {
  item: ExamItem;
  onSelfReport: (itemId: string, isCorrect: boolean) => void;
  isSelfReporting: boolean;
  isAnswerRevealed: boolean;
  onRevealAnswer: (itemId: string) => void;
  examId: string;
  examState: string;
}

function ExamItemReview({
  item,
  onSelfReport,
  isSelfReporting,
  isAnswerRevealed,
  onRevealAnswer,
  examId,
  examState,
}: ExamItemReviewProps) {
  const navigate = useNavigate();
  const isPendingReview = item.grading.status === "pending-review";
  const [explainInfoMessage, setExplainInfoMessage] = useState<string | null>(null);
  const [isExplainHovered, setIsExplainHovered] = useState(false);

  const { data: solutionStatusData } = useQuery({
    queryKey: ["solution-status", item.problemId],
    queryFn: () => api.getSolutionStatus(item.problemId),
    enabled: !!item.problemId && examState !== "in-progress",
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return (status === "pending" || status === "generating") ? 2000 : false;
    },
  });

  const solutionStatus = solutionStatusData?.status;

  const handleExplainClick = () => {
    if (solutionStatus === "ready") {
      navigate(`/coaching/${item.problemId}`, { state: { from: `/exams/${examId}` } });
    } else if (solutionStatus === "pending" || solutionStatus === "generating") {
      setExplainInfoMessage("Solution is being generated, please try again shortly");
    }
  };

  return (
    <div
      className="card-premium"
      style={{
        marginBottom: "1.25rem",
        border: "1px solid var(--color-border)",
        background: "var(--color-surface)",
        padding: "1.5rem",
        display: "flex",
        flexDirection: "column",
        gap: "1.25rem"
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: "0.8125rem", fontWeight: 700, color: "var(--color-text-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Question {item.order}</span>
        <GradingStatusBadge status={item.grading.status} />
      </div>

      <LatexText
        text={item.problem.text}
        style={{ fontSize: "1.05rem", lineHeight: "1.6", whiteSpace: "pre-wrap", color: "var(--color-text)" }}
      />

      {item.problem.graphDsl && (
        <div style={{ maxWidth: "400px" }}>
          <GraphSandbox dsl={item.problem.graphDsl} height={250} />
        </div>
      )}

      {item.problem.imageUrl && (
        <div style={{ display: "flex", backgroundColor: "var(--color-surface-muted)", padding: "1rem", borderRadius: "var(--radius-lg)", border: "1px solid var(--color-border)" }}>
          <CollapsibleImage
            src={item.problem.imageUrl}
            alt="Problem"
            style={{ maxWidth: "100%", height: "auto", borderRadius: "var(--radius-md)" }}
          />
        </div>
      )}

      <div style={{ padding: "0.75rem 1rem", backgroundColor: "var(--color-surface-muted)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border)" }}>
        <div style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", marginBottom: "0.25rem" }}>Your Answer:</div>
        <div style={{ fontSize: "0.95rem", fontWeight: 600 }}>{item.answer.raw || <em style={{ color: "var(--color-disabled-text)", fontWeight: 400 }}>No answer provided</em>}</div>
      </div>

      {item.problem.correctAnswer && (
        isAnswerRevealed ? (
          <div style={{ padding: "0.75rem 1rem", backgroundColor: "var(--color-success-bg)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-success-border)" }}>
            <div style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-success-text)", marginBottom: "0.25rem" }}>Correct Answer:</div>
            <div style={{ fontSize: "0.95rem", fontWeight: 600, color: "var(--color-success-text)" }}>{item.problem.correctAnswer.display}</div>
          </div>
        ) : (
          <div>
            <button
              onClick={() => onRevealAnswer(item.itemId)}
              className="btn btn-secondary"
              style={{
                padding: "0.4rem 0.875rem",
                fontSize: "0.8125rem",
                borderRadius: "var(--radius-md)",
              }}
              data-testid={`reveal-answer-${item.itemId}`}
            >
              Reveal Answer
            </button>
          </div>
        )
      )}

      {item.grading.feedback && (
        <div style={{ padding: "0.75rem 1rem", backgroundColor: "var(--color-primary-bg)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-primary-border)" }}>
          <div style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-primary-text)", marginBottom: "0.25rem" }}>Feedback:</div>
          <div style={{ fontSize: "0.95rem", fontWeight: 500 }}>{item.grading.feedback}</div>
        </div>
      )}

      {isPendingReview && (
        <div 
          style={{ 
            padding: "1.25rem", 
            backgroundColor: "var(--color-warning-bg)", 
            border: "1px dashed var(--color-warning-border)", 
            borderRadius: "var(--radius-md)",
            display: "flex",
            flexDirection: "column",
            gap: "0.75rem"
          }}
        >
          <p style={{ margin: 0, fontSize: "0.875rem", color: "var(--color-warning-text)", fontWeight: 600 }}>
            This answer needs your review. Did you answer correctly?
          </p>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button
              onClick={() => onSelfReport(item.itemId, true)}
              disabled={isSelfReporting}
              className="btn btn-primary"
              style={{
                padding: "0.4rem 1rem",
                fontSize: "0.8125rem",
                backgroundColor: "var(--color-success)",
                color: "white",
              }}
            >
              I was correct
            </button>
            <button
              onClick={() => onSelfReport(item.itemId, false)}
              disabled={isSelfReporting}
              className="btn btn-danger"
              style={{
                padding: "0.4rem 1rem",
                fontSize: "0.8125rem",
                backgroundColor: "var(--color-danger)",
                color: "white",
              }}
            >
              I was incorrect
            </button>
          </div>
        </div>
      )}

      {examState !== "in-progress" && solutionStatus && solutionStatus !== "none" && (
        <div style={{ borderTop: "1px solid var(--color-border)", paddingTop: "1rem", display: "flex", flexDirection: "column", gap: "0.75rem" }}>
          {explainInfoMessage && (
            <div
              className="badge badge-warning"
              style={{
                padding: "0.5rem 0.75rem",
                fontSize: "0.875rem",
                textTransform: "none",
                letterSpacing: "normal",
                fontWeight: 500,
                display: "block",
              }}
              data-testid={`explain-info-message-${item.itemId}`}
            >
              {explainInfoMessage}
            </div>
          )}
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button
              onClick={handleExplainClick}
              disabled={solutionStatus === "failed"}
              onMouseEnter={() => setIsExplainHovered(true)}
              onMouseLeave={() => setIsExplainHovered(false)}
              data-testid={`explain-button-${item.itemId}`}
              style={{
                padding: "0.5rem 1rem",
                borderRadius: "var(--radius-md)",
                fontWeight: 700,
                fontSize: "0.8125rem",
                transition: "all 0.2s ease-in-out",
                ...(solutionStatus === "failed"
                  ? {
                      background: "var(--color-surface-muted)",
                      color: "var(--color-disabled-text)",
                      border: "1px solid var(--color-border)",
                      cursor: "not-allowed",
                    }
                  : solutionStatus === "pending" || solutionStatus === "generating"
                    ? {
                        background: "var(--color-bg)",
                        color: "var(--color-link)",
                        border: "2px dashed var(--color-link)",
                        cursor: "pointer",
                        boxShadow: "0 0 8px rgba(99, 102, 241, 0.1)",
                      }
                    : {
                        background: isExplainHovered
                          ? "linear-gradient(135deg, var(--color-primary), var(--color-link))"
                          : "linear-gradient(135deg, var(--color-link), var(--color-primary))",
                        color: "white",
                        border: "none",
                        cursor: "pointer",
                        boxShadow: isExplainHovered
                          ? "0 10px 15px -3px rgba(99, 102, 241, 0.4), 0 4px 6px -4px rgba(99, 102, 241, 0.4)"
                          : "0 4px 6px -1px rgba(99, 102, 241, 0.2), 0 2px 4px -1px rgba(99, 102, 241, 0.1)",
                        transform: isExplainHovered ? "translateY(-1px)" : "translateY(0)",
                      }),
              }}
            >
              {solutionStatus === "pending" || solutionStatus === "generating"
                ? "AI Explain (Generating...)"
                : solutionStatus === "failed"
                  ? "AI Explain (Unavailable)"
                  : "AI Explain"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function ExamDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [revealedAnswers, setRevealedAnswers] = useState<Set<string>>(new Set());
  const [pendingRevealItemId, setPendingRevealItemId] = useState<string | null>(null);
  const [showPasswordModal, setShowPasswordModal] = useState(false);

  const {
    data: examData,
    isLoading,
    error,
  } = useQuery<ExamResponse>({
    queryKey: ["exam", id],
    queryFn: () => fetchExam(id!),
    enabled: !!id,
  });

  const selfReportMutation = useMutation({
    mutationFn: ({ itemId, request }: { itemId: string; request: SelfReportRequest }) =>
      selfReportItem(id!, itemId, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["exam", id] });
    },
  });

  const handleSelfReport = (itemId: string, isCorrect: boolean) => {
    selfReportMutation.mutate({ itemId, request: { isCorrect } });
  };

  const pageCanvasStyle: React.CSSProperties = {
    minHeight: "calc(100vh - 60px)",
    backgroundColor: "var(--color-bg)",
    color: "var(--color-text)",
    padding: "2rem 1.5rem",
  };

  const contentWrapperStyle: React.CSSProperties = {
    maxWidth: "800px",
    margin: "0 auto",
  };

  if (isLoading) {
    return (
      <main style={pageCanvasStyle}>
        <div style={{ ...contentWrapperStyle, textAlign: "center", color: "var(--color-text-muted)", fontWeight: 600, padding: "3rem 0" }}>
          <div>Loading exam details...</div>
        </div>
      </main>
    );
  }

  if (error) {
    return (
      <main style={pageCanvasStyle}>
        <div style={contentWrapperStyle}>
          <div 
            className="badge badge-danger"
            style={{ 
              display: "block",
              padding: "1rem",
              fontSize: "0.9rem",
              textTransform: "none",
              letterSpacing: "normal",
              fontWeight: 500
            }}
          >
            Error loading exam: {(error as Error).message}
          </div>
          <button
            onClick={() => navigate("/exams")}
            className="btn btn-secondary"
            style={{ marginTop: "1rem", padding: "0.5rem 1.25rem", fontSize: "0.875rem", fontWeight: 700 }}
          >
            Back to Exams
          </button>
        </div>
      </main>
    );
  }

  const exam = examData?.exam;
  if (!exam) {
    return (
      <main style={pageCanvasStyle}>
        <div style={contentWrapperStyle}>
          <div style={{ color: "var(--color-text-muted)", marginBottom: "1rem", fontWeight: 600 }}>Exam not found.</div>
          <button
            onClick={() => navigate("/exams")}
            className="btn btn-secondary"
            style={{ padding: "0.5rem 1.25rem", fontSize: "0.875rem", fontWeight: 700 }}
          >
            Back to Exams
          </button>
        </div>
      </main>
    );
  }

  const hasPendingReview = exam.items.some((item) => item.grading.status === "pending-review");

  return (
    <main style={pageCanvasStyle}>
      <div style={contentWrapperStyle}>
      <div style={{ marginBottom: "2rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem", flexWrap: "wrap", gap: "1rem" }}>
          <h1 style={{ margin: 0, fontSize: "2rem", fontWeight: 800, letterSpacing: "-0.02em" }}>Exam Results</h1>
          <button
            onClick={() => navigate("/exams")}
            className="btn btn-secondary"
            style={{ padding: "0.5rem 1.25rem", fontSize: "0.875rem", fontWeight: 700 }}
          >
            Back to History
          </button>
        </div>
        <p style={{ color: "var(--color-text-muted)", margin: 0, fontSize: "0.9rem", fontWeight: 500 }}>
          Submitted on {exam.submittedAt ? formatDate(exam.submittedAt) : formatDate(exam.createdAt)}
        </p>
      </div>

      <div 
        className="card-premium"
        style={{ 
          border: "1px solid var(--color-border)", 
          backgroundColor: "var(--color-surface)", 
          padding: "1.5rem", 
          marginBottom: "1.5rem" 
        }}
      >
        <h2 style={{ marginTop: 0, marginBottom: "1.25rem", fontSize: "1.25rem", fontWeight: 800, letterSpacing: "-0.01em" }}>Summary</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: "1rem" }}>
          <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "var(--color-surface-muted)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border)" }}>
            <div style={{ fontSize: "2rem", fontWeight: 800, color: exam.summary.score === null ? "var(--color-text-warning)" : "var(--color-primary-text)" }}>
              {formatScore(exam.summary.score)}
            </div>
            <div style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", marginTop: "0.25rem" }}>Score</div>
          </div>
          <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "var(--color-surface-muted)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border)" }}>
            <div style={{ fontSize: "2rem", fontWeight: 800 }}>{exam.summary.totalProblems}</div>
            <div style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", marginTop: "0.25rem" }}>Total</div>
          </div>
          <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "var(--color-success-bg)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-success-border)" }}>
            <div style={{ fontSize: "2rem", fontWeight: 800, color: "var(--color-success-text)" }}>{exam.summary.correctProblems}</div>
            <div style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-success-text)", marginTop: "0.25rem" }}>Correct</div>
          </div>
          <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "var(--color-danger-bg)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-danger-border)" }}>
            <div style={{ fontSize: "2rem", fontWeight: 800, color: "var(--color-text-danger-secondary)" }}>{exam.summary.failedProblems}</div>
            <div style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-danger-secondary)", marginTop: "0.25rem" }}>Incorrect</div>
          </div>
          {exam.summary.pendingProblems > 0 && (
            <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "var(--color-warning-bg)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-warning-border)" }}>
              <div style={{ fontSize: "2rem", fontWeight: 800, color: "var(--color-warning-text)" }}>{exam.summary.pendingProblems}</div>
              <div style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-warning-text)", marginTop: "0.25rem" }}>Pending</div>
            </div>
          )}
        </div>
      </div>

      {hasPendingReview && (
        <div 
          style={{ 
            backgroundColor: "var(--color-warning-bg)", 
            border: "1px dashed var(--color-warning-border)", 
            borderRadius: "var(--radius-md)", 
            padding: "1.25rem", 
            marginBottom: "1.5rem" 
          }}
        >
          <p style={{ margin: 0, color: "var(--color-warning-text)", fontSize: "0.9rem", lineHeight: "1.5", fontWeight: 500 }}>
            <strong style={{ fontWeight: 700 }}>Pending Review:</strong> {exam.summary.pendingProblems} question(s) need your review. 
            Please review the marked items below and self-report whether your answers were correct.
          </p>
        </div>
      )}

      <div>
        <h2 style={{ marginBottom: "1.25rem", fontSize: "1.25rem", fontWeight: 800, letterSpacing: "-0.01em" }}>Questions</h2>
        {exam.items.map((item) => (
          <ExamItemReview
            key={item.itemId}
            item={item}
            onSelfReport={handleSelfReport}
            isSelfReporting={selfReportMutation.isPending}
            isAnswerRevealed={revealedAnswers.has(item.itemId)}
            onRevealAnswer={(itemId) => {
              setPendingRevealItemId(itemId);
              setShowPasswordModal(true);
            }}
            examId={exam.id}
            examState={exam.state}
          />
        ))}
      </div>

      <TeacherPasswordModal
        isOpen={showPasswordModal}
        onClose={() => setShowPasswordModal(false)}
        onVerified={() => {
          if (pendingRevealItemId) {
            setRevealedAnswers((prev) => new Set(prev).add(pendingRevealItemId));
          }
          setShowPasswordModal(false);
          setPendingRevealItemId(null);
        }}
      />
      </div>
    </main>
  );
}
