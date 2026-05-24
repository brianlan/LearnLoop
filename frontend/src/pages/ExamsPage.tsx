import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { formatDate, formatScore } from "@/utils/format";
import type { ExamHistoryResponse, ExamHistoryItem, CreateExamRequest, CreateExamResponse } from "@/types/exam";
import Pagination from "@/components/Pagination";

function getStateStyle(state: string) {
  switch (state) {
    case "submitted":
      return { backgroundColor: "#dcfce7", color: "#166534" };
    case "discarded":
      return { backgroundColor: "#fee2e2", color: "#991b1b" };
    default:
      return { backgroundColor: "#fef3c7", color: "#92400e" };
  }
}

function ExamHistoryCard({ exam, onOpen }: { exam: ExamHistoryItem; onOpen: () => void }) {
  const stateStyle = getStateStyle(exam.state);
  const completionDate = exam.state === "discarded" ? exam.discardedAt : exam.submittedAt;
  const completionLabel = exam.state === "discarded" ? "Discarded" : "Submitted";

  return (
    <button
      type="button"
      onClick={onOpen}
      style={{
        width: "100%",
        textAlign: "left",
        backgroundColor: "white",
        border: "1px solid #e5e7eb",
        borderRadius: "0.5rem",
        padding: "1rem",
        cursor: "pointer",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: "1rem",
          flexWrap: "wrap",
          marginBottom: "0.75rem",
        }}
      >
        <div>
          <div style={{ fontWeight: 600 }}>Exam {exam.id}</div>
          <div style={{ color: "#6b7280", fontSize: "0.875rem" }}>
            Created {formatDate(exam.createdAt)}
          </div>
        </div>
        <div
          style={{
            padding: "0.25rem 0.75rem",
            borderRadius: "9999px",
            ...stateStyle,
            alignSelf: "flex-start",
            fontSize: "0.875rem",
            fontWeight: 600,
            textTransform: "capitalize",
          }}
        >
          {exam.state}
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: "0.75rem",
          marginBottom: "0.75rem",
        }}
      >
        <div>
          <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>{completionLabel}</div>
          <div>{formatDate(completionDate)}</div>
        </div>
        <div>
          <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>Score</div>
          <div>{exam.state === "discarded" ? "—" : formatScore(exam.summary.score)}</div>
        </div>
        <div>
          <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>Total</div>
          <div>{exam.summary.totalProblems}</div>
        </div>
        <div>
          <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>Correct</div>
          <div>{exam.state === "discarded" ? "—" : exam.summary.correctProblems}</div>
        </div>
        <div>
          <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>Incorrect</div>
          <div>{exam.state === "discarded" ? "—" : exam.summary.failedProblems}</div>
        </div>
      </div>
    </button>
  );
}

export function ExamsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [showActiveExamPrompt, setShowActiveExamPrompt] = useState(false);
  const pageSize = 10;

  const { data, isLoading, error } = useQuery<ExamHistoryResponse>({
    queryKey: ["exams", page, pageSize],
    queryFn: () => api.get<ExamHistoryResponse>(`/exams?page=${page}&pageSize=${pageSize}`),
  });

  const createExamMutation = useMutation({
    mutationFn: (req: CreateExamRequest) => api.post<CreateExamResponse>("/exams", req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["exams"] });
      navigate("/exams/active");
    },
  });

  const handleCreateExam = async () => {
    try {
      await createExamMutation.mutateAsync({ maxProblemCount: 10 });
    } catch (err) {
      const code = (err as Error & { code?: string }).code;
      if (code === "ACTIVE_EXAM_EXISTS") {
        setShowActiveExamPrompt(true);
      }
    }
  };

  const exams = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <main style={{ maxWidth: "960px", margin: "2rem auto", padding: "1rem" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "1rem",
          flexWrap: "wrap",
          marginBottom: "1.5rem",
        }}
      >
        <div>
          <h1 style={{ margin: 0 }}>Exam History</h1>
          <p style={{ color: "#6b7280", margin: "0.5rem 0 0" }}>
            Review previous exams or start a new session.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void handleCreateExam()}
          disabled={createExamMutation.isPending}
          style={{
            padding: "0.75rem 1rem",
            backgroundColor: createExamMutation.isPending ? "#93c5fd" : "#2563eb",
            color: "white",
            border: "none",
            borderRadius: "0.375rem",
            cursor: createExamMutation.isPending ? "not-allowed" : "pointer",
            fontWeight: 600,
          }}
        >
          {createExamMutation.isPending ? "Creating..." : "Start New Exam"}
        </button>
      </div>

      {showActiveExamPrompt && (
        <div
          style={{
            padding: "1rem",
            backgroundColor: "#fef3c7",
            border: "1px solid #fcd34d",
            borderRadius: "0.5rem",
            marginBottom: "1rem",
          }}
        >
          <p style={{ margin: "0 0 0.75rem" }}>
            An active exam already exists. Would you like to continue it?
          </p>
          <div style={{ display: "flex", gap: "0.75rem" }}>
            <button
              type="button"
              onClick={() => navigate("/exams/active")}
              style={{
                padding: "0.5rem 1rem",
                backgroundColor: "#2563eb",
                color: "white",
                border: "none",
                borderRadius: "0.375rem",
                cursor: "pointer",
                fontWeight: 600,
              }}
            >
              Continue Exam
            </button>
            <button
              type="button"
              onClick={() => setShowActiveExamPrompt(false)}
              style={{
                padding: "0.5rem 1rem",
                backgroundColor: "#f3f4f6",
                border: "1px solid #d1d5db",
                borderRadius: "0.375rem",
                cursor: "pointer",
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {isLoading ? (
        <div>Loading exams...</div>
      ) : error ? (
        <div style={{ color: "#dc2626" }}>Error loading exams: {(error as Error).message}</div>
      ) : exams.length === 0 ? (
        <div
          style={{
            padding: "2rem",
            textAlign: "center",
            backgroundColor: "#f9fafb",
            border: "1px solid #e5e7eb",
            borderRadius: "0.5rem",
          }}
        >
          No exams yet
        </div>
      ) : (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            {exams.map((exam) => (
              <ExamHistoryCard
                key={exam.id}
                exam={exam}
                onOpen={() => navigate(`/exams/${exam.id}`)}
              />
            ))}
          </div>

          <Pagination
            page={page}
            totalPages={totalPages}
            onPageChange={setPage}
            style={{ gap: "0.75rem", marginTop: "1.5rem" }}
          />
        </>
      )}
    </main>
  );
}
