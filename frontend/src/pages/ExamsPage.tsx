import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { formatDate, formatScore } from "@/utils/format";
import type { ExamHistoryResponse, ExamHistoryItem, CreateExamRequest, CreateExamResponse } from "@/types/exam";
import Pagination from "@/components/Pagination";
import { Modal } from "@/components/Modal";

const EXAM_PROBLEM_COUNT_MIN = 1;
const EXAM_PROBLEM_COUNT_MAX = 30;
const EXAM_PROBLEM_COUNT_DEFAULT = 5;

function getStateStyleClass(state: string) {
  switch (state) {
    case "submitted":
      return "badge-success";
    case "discarded":
      return "badge-danger";
    default:
      return "badge-warning";
  }
}

function ExamHistoryCard({ exam, onOpen }: { exam: ExamHistoryItem; onOpen: () => void }) {
  const stateClass = getStateStyleClass(exam.state);
  const completionDate = exam.state === "discarded" ? exam.discardedAt : exam.submittedAt;
  const completionLabel = exam.state === "discarded" ? "Discarded" : "Submitted";

  return (
    <button
      type="button"
      onClick={onOpen}
      className="card-premium card-hover"
      style={{
        width: "100%",
        textAlign: "left",
        cursor: "pointer",
        display: "block",
        padding: "1.25rem",
        border: "1px solid var(--color-border)",
        background: "var(--color-surface)"
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: "1rem",
          flexWrap: "wrap",
          marginBottom: "1rem",
          alignItems: "center"
        }}
      >
        <div>
          <div style={{ fontWeight: 800, fontSize: "1.1rem", letterSpacing: "-0.01em" }}>Exam {exam.id}</div>
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.8125rem", marginTop: "0.15rem", fontWeight: 500 }}>
            Created {formatDate(exam.createdAt)}
          </div>
        </div>
        <div
          className={`badge ${stateClass}`}
          style={{
            alignSelf: "flex-start",
            fontSize: "0.75rem",
            fontWeight: 700,
            padding: "0.2rem 0.6rem",
            borderRadius: "var(--radius-full)",
            textTransform: "capitalize",
            letterSpacing: "normal"
          }}
        >
          {exam.state}
        </div>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: "1rem",
          marginTop: "0.5rem"
        }}
      >
        <div>
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "0.25rem" }}>{completionLabel}</div>
          <div style={{ fontSize: "0.9rem", fontWeight: 600 }}>{formatDate(completionDate)}</div>
        </div>
        <div>
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "0.25rem" }}>Score</div>
          <div style={{ fontSize: "1.1rem", fontWeight: 800, color: exam.state === "discarded" ? "var(--color-text-muted)" : "var(--color-primary-text)" }}>{exam.state === "discarded" ? "—" : formatScore(exam.summary.score)}</div>
        </div>
        <div>
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "0.25rem" }}>Total</div>
          <div style={{ fontSize: "1.1rem", fontWeight: 800 }}>{exam.summary.totalProblems}</div>
        </div>
        <div>
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "0.25rem" }}>Correct</div>
          <div style={{ fontSize: "1.1rem", fontWeight: 800, color: exam.state === "discarded" ? "var(--color-text-muted)" : "var(--color-success)" }}>{exam.state === "discarded" ? "—" : exam.summary.correctProblems}</div>
        </div>
        <div>
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "0.25rem" }}>Incorrect</div>
          <div style={{ fontSize: "1.1rem", fontWeight: 800, color: exam.state === "discarded" ? "var(--color-text-muted)" : "var(--color-text-danger)" }}>{exam.state === "discarded" ? "—" : exam.summary.failedProblems}</div>
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
    <main style={{ minHeight: "calc(100vh - 60px)", backgroundColor: "var(--color-bg)", color: "var(--color-text)", padding: "2rem 1.5rem" }}>
      <div style={{ maxWidth: "960px", margin: "0 auto" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "1.5rem",
            flexWrap: "wrap",
            marginBottom: "2rem",
          }}
        >
          <div>
            <h1 style={{ margin: 0, fontSize: "2rem", fontWeight: 800, letterSpacing: "-0.02em" }}>Exam History</h1>
            <p style={{ color: "var(--color-text-muted)", margin: "0.375rem 0 0", fontSize: "0.95rem", fontWeight: 500 }}>
              Review previous exams or start a new session.
            </p>
          </div>
          <button
            type="button"
            onClick={handleOpenCreateExamModal}
            className="btn btn-primary"
            style={{
              padding: "0.6rem 1.25rem",
              fontSize: "0.875rem",
              fontWeight: 700,
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
          <div style={{ padding: "0.5rem" }}>
            <h2 id="create-exam-title" style={{ marginTop: 0, fontWeight: 800, fontSize: "1.35rem", letterSpacing: "-0.01em" }}>Start New Exam</h2>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void handleCreateExam();
              }}
              style={{ display: "flex", flexDirection: "column", gap: "1.25rem", marginTop: "1rem" }}
            >
              <p style={{ color: "var(--color-text-muted)", margin: 0, fontSize: "0.9rem", fontWeight: 500 }}>
                Choose how many problems to include.
              </p>
              <div>
                <label 
                  htmlFor="exam-problem-count" 
                  style={{ 
                    fontSize: "0.75rem",
                    fontWeight: 700,
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                    color: "var(--color-text-muted)",
                    display: "block",
                    marginBottom: "0.5rem"
                  }}
                >
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
                    padding: "0.6rem 0.8rem",
                    border: `1px solid ${problemCountError ? "var(--color-danger-border)" : "var(--color-border)"}`,
                    borderRadius: "var(--radius-md)",
                    backgroundColor: "var(--color-bg)",
                    color: "var(--color-text)",
                    outline: "none",
                    fontSize: "0.9rem",
                    boxSizing: "border-box"
                  }}
                />
              </div>
              <div>
                <label 
                  htmlFor="exam-problem-count-slider" 
                  style={{ 
                    fontSize: "0.75rem",
                    fontWeight: 700,
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                    color: "var(--color-text-muted)",
                    display: "block",
                    marginBottom: "0.5rem"
                  }}
                >
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
                  style={{ width: "100%", accentColor: "var(--color-primary)" }}
                />
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    color: "var(--color-text-muted)",
                    fontSize: "0.75rem",
                    fontWeight: 600,
                    marginTop: "0.25rem",
                  }}
                >
                  <span>{EXAM_PROBLEM_COUNT_MIN}</span>
                  <span>{EXAM_PROBLEM_COUNT_MAX}</span>
                </div>
              </div>
              {problemCountError && (
                <div
                  id="exam-problem-count-error"
                  role="alert"
                  className="badge badge-danger"
                  style={{ 
                    padding: "0.5rem 0.75rem",
                    fontSize: "0.8125rem",
                    textTransform: "none",
                    letterSpacing: "normal",
                    fontWeight: 500,
                    display: "block",
                  }}
                >
                  {problemCountError}
                </div>
              )}
              <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem", borderTop: "1px solid var(--color-border)", paddingTop: "1rem", marginTop: "0.5rem" }}>
                <button
                  type="button"
                  onClick={handleCloseCreateExamModal}
                  disabled={createExamMutation.isPending}
                  className="btn btn-secondary"
                  style={{
                    padding: "0.5rem 1.25rem",
                    fontSize: "0.875rem",
                    fontWeight: 700,
                  }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={validProblemCount === null || createExamMutation.isPending}
                  className="btn btn-primary"
                  style={{
                    padding: "0.5rem 1.25rem",
                    fontSize: "0.875rem",
                    fontWeight: 700,
                  }}
                >
                  {createExamMutation.isPending ? "Creating..." : "Create Exam"}
                </button>
              </div>
            </form>
          </div>
        </Modal>

        {showActiveExamPrompt && (
          <div
            style={{
              padding: "1.25rem",
              backgroundColor: "var(--color-warning-bg)",
              border: "1px dashed var(--color-warning-border)",
              borderRadius: "var(--radius-md)",
              marginBottom: "1.5rem",
            }}
          >
            <p style={{ margin: "0 0 1rem", fontSize: "0.95rem", color: "var(--color-warning-text)", fontWeight: 600 }}>
              An active exam already exists. Would you like to continue it?
            </p>
            <div style={{ display: "flex", gap: "0.75rem" }}>
              <button
                type="button"
                onClick={() => navigate("/exams/active")}
                className="btn btn-primary"
                style={{
                  padding: "0.5rem 1.25rem",
                  fontSize: "0.875rem",
                  fontWeight: 700,
                }}
              >
                Continue Exam
              </button>
              <button
                type="button"
                onClick={() => setShowActiveExamPrompt(false)}
                className="btn btn-secondary"
                style={{
                  padding: "0.5rem 1.25rem",
                  fontSize: "0.875rem",
                  fontWeight: 700,
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {!isLoading && !error && (
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "1rem" }}>
            <label
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "0.5rem",
                cursor: "pointer",
                fontSize: "0.875rem",
                color: "var(--color-text-muted)",
                fontWeight: 600,
                userSelect: "none"
              }}
            >
              <input
                type="checkbox"
                checked={showDiscarded}
                onChange={() => {
                  setShowDiscarded((prev) => !prev);
                  setPage(1);
                }}
                style={{ accentColor: "var(--color-primary)" }}
              />
              Show discarded
            </label>
          </div>
        )}

        {isLoading ? (
          <div style={{ textAlign: "center", color: "var(--color-text-muted)", padding: "3rem 0", fontWeight: 600 }}>Loading exams...</div>
        ) : error ? (
          <div className="badge badge-danger" style={{ display: "block", padding: "1rem", fontSize: "0.9rem", textTransform: "none", letterSpacing: "normal", fontWeight: 500 }}>
            Error loading exams: {(error as Error).message}
          </div>
        ) : exams.length === 0 ? (
          <div
            style={{
              padding: "4rem 2rem",
              textAlign: "center",
              backgroundColor: "var(--color-surface-muted)",
              border: "1px solid var(--color-border)",
              borderRadius: "var(--radius-lg)",
              color: "var(--color-text-muted)",
              fontWeight: 500,
              fontSize: "0.95rem"
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
              style={{ gap: "0.5rem", marginTop: "2rem" }}
            />
          </>
        )}
      </div>
    </main>
  );
}
