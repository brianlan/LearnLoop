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
  const styles: Record<string, React.CSSProperties> = {
    correct: { backgroundColor: "var(--color-success-bg)", color: "var(--color-success-text)", padding: "0.25rem 0.5rem", borderRadius: "0.25rem", fontSize: "0.875rem", fontWeight: 500 },
    incorrect: { backgroundColor: "var(--color-danger-bg)", color: "var(--color-text-danger-secondary)", padding: "0.25rem 0.5rem", borderRadius: "0.25rem", fontSize: "0.875rem", fontWeight: 500 },
    "pending-review": { backgroundColor: "var(--color-warning-bg)", color: "var(--color-warning-text)", padding: "0.25rem 0.5rem", borderRadius: "0.25rem", fontSize: "0.875rem", fontWeight: 500 },
    ungraded: { backgroundColor: "var(--color-ungraded-bg)", color: "var(--color-ungraded-text)", padding: "0.25rem 0.5rem", borderRadius: "0.25rem", fontSize: "0.875rem", fontWeight: 500 },
  };

  const labels: Record<string, string> = {
    correct: "Correct",
    incorrect: "Incorrect",
    "pending-review": "Pending Review",
    ungraded: "Ungraded",
  };

  return <span style={styles[status] || styles.ungraded}>{labels[status] || status}</span>;
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
    <div style={{ backgroundColor: "var(--color-surface-muted)", border: "1px solid var(--color-border)", borderRadius: "0.5rem", padding: "1.5rem", marginBottom: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1rem" }}>
        <span style={{ fontSize: "0.875rem", color: "var(--color-text-muted)" }}>Question {item.order}</span>
        <GradingStatusBadge status={item.grading.status} />
      </div>

      <LatexText
        text={item.problem.text}
        style={{ fontSize: "1rem", lineHeight: "1.75", marginBottom: "1rem", whiteSpace: "pre-wrap" }}
      />

      {item.problem.graphDsl && (
        <div style={{ marginBottom: "1rem" }}>
          <GraphSandbox dsl={item.problem.graphDsl} height={250} />
        </div>
      )}

      {item.problem.imageUrl && (
        <CollapsibleImage
          src={item.problem.imageUrl}
          alt="Problem"
          style={{ maxWidth: "100%", height: "auto", borderRadius: "0.25rem" }}
        />
      )}

      <div style={{ marginTop: "1rem", padding: "0.75rem", backgroundColor: "var(--color-surface)", borderRadius: "0.25rem" }}>
        <div style={{ fontSize: "0.875rem", color: "var(--color-text-muted)", marginBottom: "0.25rem" }}>Your Answer:</div>
        <div>{item.answer.raw || <em style={{ color: "var(--color-disabled-text)" }}>No answer provided</em>}</div>
      </div>

      {item.problem.correctAnswer && (
        isAnswerRevealed ? (
          <div style={{ marginTop: "1rem", padding: "0.75rem", backgroundColor: "var(--color-success-bg)", borderRadius: "0.25rem" }}>
            <div style={{ fontSize: "0.875rem", color: "var(--color-success-text)", marginBottom: "0.25rem" }}>Correct Answer:</div>
            <div>{item.problem.correctAnswer.display}</div>
          </div>
        ) : (
          <button
            onClick={() => onRevealAnswer(item.itemId)}
            style={{
              marginTop: "1rem",
              padding: "0.5rem 1rem",
              backgroundColor: "var(--color-info-bg)",
              border: "1px solid var(--color-info-border)",
              borderRadius: "0.25rem",
              cursor: "pointer",
            }}
            data-testid={`reveal-answer-${item.itemId}`}
          >
            Reveal Answer
          </button>
        )
      )}

      {item.grading.feedback && (
        <div style={{ marginTop: "1rem", padding: "0.75rem", backgroundColor: "var(--color-primary-bg)", borderRadius: "0.25rem" }}>
          <div style={{ fontSize: "0.875rem", color: "var(--color-primary-text)", marginBottom: "0.25rem" }}>Feedback:</div>
          <div>{item.grading.feedback}</div>
        </div>
      )}

      {isPendingReview && (
        <div style={{ marginTop: "1rem", padding: "1rem", backgroundColor: "var(--color-warning-bg)", border: "1px dashed var(--color-warning)", borderRadius: "0.25rem" }}>
          <p style={{ margin: "0 0 0.75rem 0", fontSize: "0.875rem", color: "var(--color-warning-text)" }}>
            This answer needs your review. Did you answer correctly?
          </p>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button
              onClick={() => onSelfReport(item.itemId, true)}
              disabled={isSelfReporting}
              style={{
                padding: "0.5rem 1rem",
                backgroundColor: isSelfReporting ? "var(--color-primary-disabled)" : "var(--color-success)",
                color: "white",
                border: "none",
                borderRadius: "0.25rem",
                cursor: isSelfReporting ? "not-allowed" : "pointer",
              }}
            >
              I was correct
            </button>
            <button
              onClick={() => onSelfReport(item.itemId, false)}
              disabled={isSelfReporting}
              style={{
                padding: "0.5rem 1rem",
                backgroundColor: isSelfReporting ? "var(--color-danger-border)" : "var(--color-danger)",
                color: "white",
                border: "none",
                borderRadius: "0.25rem",
                cursor: isSelfReporting ? "not-allowed" : "pointer",
              }}
            >
              I was incorrect
            </button>
          </div>
        </div>
      )}

      {examState !== "in-progress" && solutionStatus && solutionStatus !== "none" && (
        <div style={{ marginTop: "1.25rem", borderTop: "1px solid var(--color-ungraded-bg)", paddingTop: "1rem", display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          {explainInfoMessage && (
            <div
              style={{
                padding: "0.5rem 0.75rem",
                backgroundColor: "var(--color-warning-bg)",
                border: "1px solid var(--color-warning-border)",
                borderRadius: "0.25rem",
                color: "var(--color-warning-text)",
                fontSize: "0.875rem",
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
                borderRadius: "0.25rem",
                fontWeight: 600,
                fontSize: "0.875rem",
                transition: "all 0.2s ease-in-out",
                ...(solutionStatus === "failed"
                  ? {
                      background: "var(--color-ungraded-bg)",
                      color: "var(--color-disabled-text)",
                      border: "1px solid var(--color-border)",
                      cursor: "not-allowed",
                    }
                  : solutionStatus === "pending" || solutionStatus === "generating"
                    ? {
                        background: "var(--color-surface)",
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

  if (isLoading) {
    return (
      <main style={{ maxWidth: "800px", margin: "2rem auto", padding: "1rem", textAlign: "center" }}>
        <div>Loading exam details...</div>
      </main>
    );
  }

  if (error) {
    return (
      <main style={{ maxWidth: "800px", margin: "2rem auto", padding: "1rem" }}>
        <div style={{ color: "var(--color-text-danger)", padding: "1rem", backgroundColor: "var(--color-danger-bg)", borderRadius: "0.25rem" }}>
          Error loading exam: {(error as Error).message}
        </div>
        <button
          onClick={() => navigate("/exams")}
          style={{ marginTop: "1rem", padding: "0.5rem 1rem" }}
        >
          Back to Exams
        </button>
      </main>
    );
  }

  const exam = examData?.exam;
  if (!exam) {
    return (
      <main style={{ maxWidth: "800px", margin: "2rem auto", padding: "1rem" }}>
        <div>Exam not found.</div>
        <button
          onClick={() => navigate("/exams")}
          style={{ marginTop: "1rem", padding: "0.5rem 1rem" }}
        >
          Back to Exams
        </button>
      </main>
    );
  }

  const hasPendingReview = exam.items.some((item) => item.grading.status === "pending-review");

  return (
    <main style={{ maxWidth: "800px", margin: "2rem auto", padding: "1rem" }}>
      <div style={{ marginBottom: "1.5rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
          <h1 style={{ margin: 0 }}>Exam Results</h1>
          <button
            onClick={() => navigate("/exams")}
            style={{ padding: "0.5rem 1rem" }}
          >
            Back to History
          </button>
        </div>
        <p style={{ color: "var(--color-text-muted)", margin: 0 }}>
          Submitted on {exam.submittedAt ? formatDate(exam.submittedAt) : formatDate(exam.createdAt)}
        </p>
      </div>

      <div style={{ backgroundColor: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "0.5rem", padding: "1.5rem", marginBottom: "1.5rem" }}>
        <h2 style={{ marginTop: 0, marginBottom: "1rem" }}>Summary</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "1rem" }}>
          <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "var(--color-surface-muted)", borderRadius: "0.25rem" }}>
            <div style={{ fontSize: "2rem", fontWeight: 700, color: exam.summary.score === null ? "var(--color-warning)" : "var(--color-success)" }}>
              {formatScore(exam.summary.score)}
            </div>
            <div style={{ fontSize: "0.875rem", color: "var(--color-text-muted)" }}>Score</div>
          </div>
          <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "var(--color-surface-muted)", borderRadius: "0.25rem" }}>
            <div style={{ fontSize: "2rem", fontWeight: 700 }}>{exam.summary.totalProblems}</div>
            <div style={{ fontSize: "0.875rem", color: "var(--color-text-muted)" }}>Total</div>
          </div>
          <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "var(--color-success-bg)", borderRadius: "0.25rem" }}>
            <div style={{ fontSize: "2rem", fontWeight: 700, color: "var(--color-success-text)" }}>{exam.summary.correctProblems}</div>
            <div style={{ fontSize: "0.875rem", color: "var(--color-success-text)" }}>Correct</div>
          </div>
          <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "var(--color-danger-bg)", borderRadius: "0.25rem" }}>
            <div style={{ fontSize: "2rem", fontWeight: 700, color: "var(--color-text-danger-secondary)" }}>{exam.summary.failedProblems}</div>
            <div style={{ fontSize: "0.875rem", color: "var(--color-text-danger-secondary)" }}>Incorrect</div>
          </div>
          {exam.summary.pendingProblems > 0 && (
            <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "var(--color-warning-bg)", borderRadius: "0.25rem" }}>
              <div style={{ fontSize: "2rem", fontWeight: 700, color: "var(--color-warning-text)" }}>{exam.summary.pendingProblems}</div>
              <div style={{ fontSize: "0.875rem", color: "var(--color-warning-text)" }}>Pending Review</div>
            </div>
          )}
        </div>
      </div>

      {hasPendingReview && (
        <div style={{ backgroundColor: "var(--color-warning-bg)", border: "1px solid var(--color-warning)", borderRadius: "0.5rem", padding: "1rem", marginBottom: "1.5rem" }}>
          <p style={{ margin: 0, color: "var(--color-warning-text)" }}>
            <strong>Pending Review:</strong> {exam.summary.pendingProblems} question(s) need your review. 
            Please review the marked items below and self-report whether your answers were correct.
          </p>
        </div>
      )}

      <div>
        <h2 style={{ marginBottom: "1rem" }}>Questions</h2>
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
    </main>
  );
}
