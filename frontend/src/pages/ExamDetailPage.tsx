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
    correct: { backgroundColor: "#d1fae5", color: "#065f46", padding: "0.25rem 0.5rem", borderRadius: "0.25rem", fontSize: "0.875rem", fontWeight: 500 },
    incorrect: { backgroundColor: "#fee2e2", color: "#991b1b", padding: "0.25rem 0.5rem", borderRadius: "0.25rem", fontSize: "0.875rem", fontWeight: 500 },
    "pending-review": { backgroundColor: "#fef3c7", color: "#92400e", padding: "0.25rem 0.5rem", borderRadius: "0.25rem", fontSize: "0.875rem", fontWeight: 500 },
    ungraded: { backgroundColor: "#f3f4f6", color: "#4b5563", padding: "0.25rem 0.5rem", borderRadius: "0.25rem", fontSize: "0.875rem", fontWeight: 500 },
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
}

function ExamItemReview({ item, onSelfReport, isSelfReporting, isAnswerRevealed, onRevealAnswer }: ExamItemReviewProps) {
  const isPendingReview = item.grading.status === "pending-review";

  return (
    <div style={{ backgroundColor: "#f9fafb", border: "1px solid #e5e7eb", borderRadius: "0.5rem", padding: "1.5rem", marginBottom: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1rem" }}>
        <span style={{ fontSize: "0.875rem", color: "#6b7280" }}>Question {item.order}</span>
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

      <div style={{ marginTop: "1rem", padding: "0.75rem", backgroundColor: "white", borderRadius: "0.25rem" }}>
        <div style={{ fontSize: "0.875rem", color: "#6b7280", marginBottom: "0.25rem" }}>Your Answer:</div>
        <div>{item.answer.raw || <em style={{ color: "#9ca3af" }}>No answer provided</em>}</div>
      </div>

      {item.problem.correctAnswer && (
        isAnswerRevealed ? (
          <div style={{ marginTop: "1rem", padding: "0.75rem", backgroundColor: "#ecfdf5", borderRadius: "0.25rem" }}>
            <div style={{ fontSize: "0.875rem", color: "#065f46", marginBottom: "0.25rem" }}>Correct Answer:</div>
            <div>{item.problem.correctAnswer.display}</div>
          </div>
        ) : (
          <button
            onClick={() => onRevealAnswer(item.itemId)}
            style={{
              marginTop: "1rem",
              padding: "0.5rem 1rem",
              backgroundColor: "#e0f2fe",
              border: "1px solid #bae6fd",
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
        <div style={{ marginTop: "1rem", padding: "0.75rem", backgroundColor: "#eff6ff", borderRadius: "0.25rem" }}>
          <div style={{ fontSize: "0.875rem", color: "#1e40af", marginBottom: "0.25rem" }}>Feedback:</div>
          <div>{item.grading.feedback}</div>
        </div>
      )}

      {isPendingReview && (
        <div style={{ marginTop: "1rem", padding: "1rem", backgroundColor: "#fffbeb", border: "1px dashed #f59e0b", borderRadius: "0.25rem" }}>
          <p style={{ margin: "0 0 0.75rem 0", fontSize: "0.875rem", color: "#92400e" }}>
            This answer needs your review. Did you answer correctly?
          </p>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button
              onClick={() => onSelfReport(item.itemId, true)}
              disabled={isSelfReporting}
              style={{
                padding: "0.5rem 1rem",
                backgroundColor: isSelfReporting ? "#6ee7b7" : "#10b981",
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
                backgroundColor: isSelfReporting ? "#fca5a5" : "#ef4444",
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
        <div style={{ color: "#dc2626", padding: "1rem", backgroundColor: "#fee2e2", borderRadius: "0.25rem" }}>
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
        <p style={{ color: "#6b7280", margin: 0 }}>
          Submitted on {exam.submittedAt ? formatDate(exam.submittedAt) : formatDate(exam.createdAt)}
        </p>
      </div>

      <div style={{ backgroundColor: "white", border: "1px solid #e5e7eb", borderRadius: "0.5rem", padding: "1.5rem", marginBottom: "1.5rem" }}>
        <h2 style={{ marginTop: 0, marginBottom: "1rem" }}>Summary</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "1rem" }}>
          <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "#f9fafb", borderRadius: "0.25rem" }}>
            <div style={{ fontSize: "2rem", fontWeight: 700, color: exam.summary.score === null ? "#f59e0b" : "#10b981" }}>
              {formatScore(exam.summary.score)}
            </div>
            <div style={{ fontSize: "0.875rem", color: "#6b7280" }}>Score</div>
          </div>
          <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "#f9fafb", borderRadius: "0.25rem" }}>
            <div style={{ fontSize: "2rem", fontWeight: 700 }}>{exam.summary.totalProblems}</div>
            <div style={{ fontSize: "0.875rem", color: "#6b7280" }}>Total</div>
          </div>
          <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "#ecfdf5", borderRadius: "0.25rem" }}>
            <div style={{ fontSize: "2rem", fontWeight: 700, color: "#065f46" }}>{exam.summary.correctProblems}</div>
            <div style={{ fontSize: "0.875rem", color: "#065f46" }}>Correct</div>
          </div>
          <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "#fee2e2", borderRadius: "0.25rem" }}>
            <div style={{ fontSize: "2rem", fontWeight: 700, color: "#991b1b" }}>{exam.summary.failedProblems}</div>
            <div style={{ fontSize: "0.875rem", color: "#991b1b" }}>Incorrect</div>
          </div>
          {exam.summary.pendingProblems > 0 && (
            <div style={{ textAlign: "center", padding: "1rem", backgroundColor: "#fffbeb", borderRadius: "0.25rem" }}>
              <div style={{ fontSize: "2rem", fontWeight: 700, color: "#92400e" }}>{exam.summary.pendingProblems}</div>
              <div style={{ fontSize: "0.875rem", color: "#92400e" }}>Pending Review</div>
            </div>
          )}
        </div>
      </div>

      {hasPendingReview && (
        <div style={{ backgroundColor: "#fffbeb", border: "1px solid #f59e0b", borderRadius: "0.5rem", padding: "1rem", marginBottom: "1.5rem" }}>
          <p style={{ margin: 0, color: "#92400e" }}>
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
