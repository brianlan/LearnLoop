import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { ExamHistoryResponse, ExamHistoryItem, CreateExamRequest, CreateExamResponse } from "@/types/exam";

function formatDate(dateString?: string) {
  if (!dateString) {
    return "—";
  }

  return new Date(dateString).toLocaleString();
}

function formatScore(score: number | null) {
  if (score === null) {
    return "Pending";
  }

  return `${Math.round(score * 100)}%`;
}

function ExamHistoryCard({ exam, onOpen }: { exam: ExamHistoryItem; onOpen: () => void }) {
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
            backgroundColor: exam.state === "submitted" ? "#dcfce7" : "#fef3c7",
            color: exam.state === "submitted" ? "#166534" : "#92400e",
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
          <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>Submitted</div>
          <div>{formatDate(exam.submittedAt)}</div>
        </div>
        <div>
          <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>Score</div>
          <div>{formatScore(exam.summary.score)}</div>
        </div>
        <div>
          <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>Total</div>
          <div>{exam.summary.totalProblems}</div>
        </div>
        <div>
          <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>Correct</div>
          <div>{exam.summary.correctProblems}</div>
        </div>
        <div>
          <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>Incorrect</div>
          <div>{exam.summary.failedProblems}</div>
        </div>
      </div>
    </button>
  );
}

export function ExamsPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const pageSize = 10;

  const { data, isLoading, error } = useQuery<ExamHistoryResponse>({
    queryKey: ["exams", page, pageSize],
    queryFn: () => api.get<ExamHistoryResponse>(`/exams?page=${page}&pageSize=${pageSize}`),
  });

  const createExamMutation = useMutation({
    mutationFn: (req: CreateExamRequest) => api.post<CreateExamResponse>("/exams", req),
    onSuccess: () => navigate("/exams/active"),
  });

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
          onClick={() => createExamMutation.mutate({ maxProblemCount: 10 })}
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
        {createExamMutation.error && (
          <p style={{ color: "#dc2626", fontSize: "0.875rem", marginTop: "0.5rem" }}>
            {(createExamMutation.error as Error).message}
          </p>
        )}
      </div>

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

          {totalPages > 1 && (
            <div
              style={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                gap: "0.75rem",
                marginTop: "1.5rem",
              }}
            >
              <button type="button" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={page === 1}>
                Previous
              </button>
              <span>
                Page {page} of {totalPages}
              </span>
              <button
                type="button"
                onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                disabled={page === totalPages}
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </main>
  );
}
