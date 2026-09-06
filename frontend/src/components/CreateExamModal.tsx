import { useEffect, useState } from "react";

import { Modal } from "@/components/Modal";

const EXAM_PROBLEM_COUNT_MIN = 1;
const EXAM_PROBLEM_COUNT_MAX = 30;
const EXAM_PROBLEM_COUNT_DEFAULT = 5;

interface CreateExamModalProps {
  isOpen: boolean;
  isCreating: boolean;
  onClose: () => void;
  onCreate: (maxProblemCount: number) => void;
}

export function CreateExamModal({ isOpen, isCreating, onClose, onCreate }: CreateExamModalProps) {
  const [problemCountInput, setProblemCountInput] = useState(String(EXAM_PROBLEM_COUNT_DEFAULT));

  useEffect(() => {
    if (isOpen) {
      setProblemCountInput(String(EXAM_PROBLEM_COUNT_DEFAULT));
    }
  }, [isOpen]);

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

  const handleClose = () => {
    if (!isCreating) {
      onClose();
    }
  };

  const handleCreate = () => {
    if (validProblemCount === null) return;
    onCreate(validProblemCount);
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      ariaLabelledby="create-exam-title"
    >
      <div style={{ padding: "0.5rem" }}>
        <h2 id="create-exam-title" style={{ marginTop: 0, fontWeight: 800, fontSize: "1.35rem", letterSpacing: "-0.01em" }}>Start New Exam</h2>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            handleCreate();
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
              onClick={handleClose}
              disabled={isCreating}
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
              disabled={validProblemCount === null || isCreating}
              className="btn btn-primary"
              style={{
                padding: "0.5rem 1.25rem",
                fontSize: "0.875rem",
                fontWeight: 700,
              }}
            >
              {isCreating ? "Creating..." : "Create Exam"}
            </button>
          </div>
        </form>
      </div>
    </Modal>
  );
}
