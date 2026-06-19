import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { formatDate, formatScore } from "@/utils/format";
import type { ExamHistoryResponse, ExamHistoryItem, CreateExamRequest, CreateExamResponse } from "@/types/exam";
import Pagination from "@/components/Pagination";
import { Modal } from "@/components/Modal";

const EXAM_PROBLEM_COUNT_MIN = 1;
const EXAM_PROBLEM_COUNT_MAX = 20;
const EXAM_PROBLEM_COUNT_DEFAULT = 5;

function getStateStyle(state: string) {
  switch (state) {
    case "submitted":
      return { backgroundColor: "var(--color-success-bg)", color: "var(--color-success-text)" };
    case "discarded":
      return { backgroundColor: "var(--color-danger-bg)", color: "var(--color-text-danger-secondary)" };
    default:
      return { backgroundColor: "var(--color-warning-bg)", color: "var(--color-warning-text)" };
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
        backgroundColor: "var(--color-surface)",
        border: "1px solid var(--color-border)",
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
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.875rem" }}>
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
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.75rem" }}>{completionLabel}</div>
          <div>{formatDate(completionDate)}</div>
        </div>
        <div>
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.75rem" }}>Score</div>
          <div>{exam.state === "discarded" ? "—" : formatScore(exam.summary.score)}</div>
        </div>
        <div>
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.75rem" }}>Total</div>
          <div>{exam.summary.totalProblems}</div>
        </div>
        <div>
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.75rem" }}>Correct</div>
          <div>{exam.state === "discarded" ? "—" : exam.summary.correctProblems}</div>
        </div>
        <div>
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.75rem" }}>Incorrect</div>
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
  const [showCreateExamModal, setShowCreateExamModal] = useState(false);
  const [problemCountInput, setProblemCountInput] = useState(String(EXAM_PROBLEM_COUNT_DEFAULT));
  const [showDiscarded, setShowDiscarded] = useState(false);
  const pageSize = 10;

  const { data, isLoading, error } = useQuery<ExamHistoryResponse>({
    queryKey: ["exams", page, pageSize, showDiscarded],
    queryFn: () =>
      api.get<ExamHistoryResponse>(
        `/exams?page=${page}&pageSize=${pageSize}&includeDiscarded=${showDiscarded}`,
      ),
  });

  const createExamMutation = useMutation({
    mutationFn: (req: CreateExamRequest) => api.post<CreateExamResponse>("/exams", req),
    onSuccess: () => {
      setShowCreateExamModal(false);
      queryClient.invalidateQueries({ queryKey: ["exams"] });
      navigate("/exams/active");
    },
  });

  const parsedProblemCount = /^\d+$/.test(problemCountInput) ? Number(problemCountInput) : null;
  const validProblemCount =
    parsedProblemCount !== null &&
    parsedProblemCount >= EXAM_PROBLEM_COUNT_MIN &&
    parsedProblemCount <= EXAM_PROBLEM_COUNT_MAX
      ? parsedProblemCount
      : null;
  const problemCountError =
    validProblemCount === null
      ? `Enter a whole number from ${EXAM_PROBLEM_COUNT_MIN} to ${EXAM_PROBLEM_COUNT_MAX}.`
      : null;

  const handleOpenCreateExamModal = () => {
    setProblemCountInput(String(EXAM_PROBLEM_COUNT_DEFAULT));
    setShowCreateExamModal(true);
  };

  const handleCloseCreateExamModal = () => {
    if (!createExamMutation.isPending) {
      setShowCreateExamModal(false);
    }
  };

  const handleCreateExam = async () => {
    if (validProblemCount === null) return;

    try {
      await createExamMutation.mutateAsync({ maxProblemCount: validProblemCount });
    } catch (err) {
      const code = (err as Error & { code?: string }).code;
      if (code === "ACTIVE_EXAM_EXISTS") {
        setShowCreateExamModal(false);
        setShowActiveExamPrompt(true);
      }
    }
  };

  const exams = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <main style={{ minHeight: "calc(100vh - 60px)", backgroundColor: "var(--color-surface-muted)", color: "var(--color-text)", padding: "1rem" }}>
      <div style={{ maxWidth: "960px", margin: "2rem auto" }}>
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
          <p style={{ color: "var(--color-text-muted)", margin: "0.5rem 0 0" }}>
            Review previous exams or start a new session.
          </p>
        </div>
        <button
          type="button"
          onClick={handleOpenCreateExamModal}
          style={{
            padding: "0.75rem 1rem",
            backgroundColor: "var(--color-primary)",
            color: "white",
            border: "none",
            borderRadius: "0.375rem",
            cursor: "pointer",
            fontWeight: 600,
          }}
        >
          Start New Exam
        </button>
      </div>



      <Modal
        isOpen={showCreateExamModal}
        onClose={handleCloseCreateExamModal}
        ariaLabelledby="create-exam-title"
      >
        <h2 id="create-exam-title" style={{ marginTop: 0 }}>Start New Exam</h2>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void handleCreateExam();
          }}
        >
          <p style={{ color: "var(--color-text-muted)", marginTop: 0 }}>
            Choose how many problems to include.
          </p>
          <label htmlFor="exam-problem-count" style={{ display: "block", fontWeight: 600, marginBottom: "0.5rem" }}>
            Problem count
          </label>
          <input
            id="exam-problem-count"
            type="number"
            min={EXAM_PROBLEM_COUNT_MIN}
            max={EXAM_PROBLEM_COUNT_MAX}
            step={1}
            value={problemCountInput}
            onChange={(event) => setProblemCountInput(event.target.value)}
            aria-invalid={problemCountError ? "true" : "false"}
            aria-describedby={problemCountError ? "exam-problem-count-error" : undefined}
            style={{
              width: "100%",
              padding: "0.5rem",
              border: `1px solid ${problemCountError ? "var(--color-danger-border)" : "var(--color-border)"}`,
              borderRadius: "0.375rem",
              marginBottom: "0.75rem",
            }}
          />
          <label htmlFor="exam-problem-count-slider" style={{ display: "block", fontWeight: 600, marginBottom: "0.5rem" }}>
            Problem count slider
          </label>
          <input
            id="exam-problem-count-slider"
            type="range"
            min={EXAM_PROBLEM_COUNT_MIN}
            max={EXAM_PROBLEM_COUNT_MAX}
            step={1}
            value={validProblemCount ?? EXAM_PROBLEM_COUNT_DEFAULT}
            onChange={(event) => setProblemCountInput(event.target.value)}
            style={{ width: "100%" }}
          />
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              color: "var(--color-text-muted)",
              fontSize: "0.875rem",
              marginBottom: "0.75rem",
            }}
          >
            <span>{EXAM_PROBLEM_COUNT_MIN}</span>
            <span>{EXAM_PROBLEM_COUNT_MAX}</span>
          </div>
          {problemCountError && (
            <div
              id="exam-problem-count-error"
              role="alert"
              style={{ color: "var(--color-text-danger)", marginBottom: "0.75rem" }}
            >
              {problemCountError}
            </div>
          )}
          <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem" }}>
            <button
              type="button"
              onClick={handleCloseCreateExamModal}
              disabled={createExamMutation.isPending}
              style={{
                padding: "0.5rem 1rem",
                backgroundColor: "var(--color-disabled-bg)",
                border: "1px solid var(--color-border-muted)",
                borderRadius: "0.375rem",
                cursor: createExamMutation.isPending ? "not-allowed" : "pointer",
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={validProblemCount === null || createExamMutation.isPending}
              style={{
                padding: "0.5rem 1rem",
                backgroundColor:
                  validProblemCount === null || createExamMutation.isPending
                    ? "var(--color-primary-disabled)"
                    : "var(--color-primary)",
                color: "white",
                border: "none",
                borderRadius: "0.375rem",
                cursor: validProblemCount === null || createExamMutation.isPending ? "not-allowed" : "pointer",
                fontWeight: 600,
              }}
            >
              {createExamMutation.isPending ? "Creating..." : "Create Exam"}
            </button>
          </div>
        </form>
      </Modal>

      {showActiveExamPrompt && (
        <div
          style={{
            padding: "1rem",
            backgroundColor: "var(--color-warning-bg)",
            border: "1px solid var(--color-warning-border)",
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
                backgroundColor: "var(--color-primary)",
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
                backgroundColor: "var(--color-disabled-bg)",
                border: "1px solid var(--color-border-muted)",
                borderRadius: "0.375rem",
                cursor: "pointer",
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {!isLoading && !error && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "0.75rem" }}>
          <label
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "0.5rem",
              cursor: "pointer",
              fontSize: "0.875rem",
              color: "var(--color-text-muted)",
            }}
          >
            <input
              type="checkbox"
              checked={showDiscarded}
              onChange={() => {
                setShowDiscarded((prev) => !prev);
                setPage(1);
              }}
            />
            Show discarded
          </label>
        </div>
      )}

      {isLoading ? (
        <div>Loading exams...</div>
      ) : error ? (
        <div style={{ color: "var(--color-text-danger)" }}>Error loading exams: {(error as Error).message}</div>
      ) : exams.length === 0 ? (
        <div
          style={{
            padding: "2rem",
            textAlign: "center",
            backgroundColor: "var(--color-surface-muted)",
            border: "1px solid var(--color-border)",
            borderRadius: "0.5rem",
          }}
        >
          {showDiscarded ? "No exams yet" : "No submitted exams yet"}
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
      </div>
    </main>
  );
}
