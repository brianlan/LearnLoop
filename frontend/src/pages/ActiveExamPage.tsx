import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import { GraphSandbox } from "@/components/GraphSandbox";
import { CollapsibleImage } from "@/components/CollapsibleImage";
import { LatexText } from "@/components/LatexText";
import { AnswerInput, parseOptions } from "@/components/AnswerInput";
import { Modal } from "@/components/Modal";
import type {
  ExamItem,
  CreateExamRequest,
  CreateExamResponse,
  ExamResponse,
  SaveAnswerRequest,
  SaveAnswerResponse,
} from "@/types/exam";

async function fetchActiveExam(): Promise<ExamResponse> {
  return api.get<ExamResponse>("/exams/active");
}

async function createExam(request: CreateExamRequest): Promise<CreateExamResponse> {
  return api.post<CreateExamResponse>("/exams", request);
}

async function saveAnswer(
  examId: string,
  itemId: string,
  request: SaveAnswerRequest,
): Promise<SaveAnswerResponse> {
  return api.patch<SaveAnswerResponse>(`/exams/${examId}/items/${itemId}/answer`, request);
}

async function submitExam(examId: string): Promise<ExamResponse> {
  return api.post<ExamResponse>(`/exams/${examId}/submit`, {});
}

async function discardExam(examId: string): Promise<ExamResponse> {
  return api.post<ExamResponse>(`/exams/${examId}/discard`, {});
}

export function ActiveExamPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [currentItemIndex, setCurrentItemIndex] = useState(0);
  const [localAnswer, setLocalAnswer] = useState("");
  const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved">("idle");
  const [showSubmitConfirm, setShowSubmitConfirm] = useState(false);
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);
  const [showPrintPreview, setShowPrintPreview] = useState(false);

  const {
    data: examData,
    isLoading: isLoadingExam,
    error: examError,
    refetch: refetchExam,
  } = useQuery<ExamResponse>({
    queryKey: ["active-exam"],
    queryFn: fetchActiveExam,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const exam = examData?.exam;
  const items = exam?.items || [];
  const currentItem: ExamItem | undefined = items[currentItemIndex];

  const createExamMutation = useMutation({
    mutationFn: createExam,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["active-exam"] });
      refetchExam();
    },
  });

  const saveAnswerMutation = useMutation({
    mutationFn: ({ examId, itemId, request }: { examId: string; itemId: string; request: SaveAnswerRequest }) =>
      saveAnswer(examId, itemId, request),
    onSuccess: () => {
      setSaveStatus("saved");
      queryClient.invalidateQueries({ queryKey: ["active-exam"] });
    },
  });

  const submitExamMutation = useMutation({
    mutationFn: (examId: string) => submitExam(examId),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["exams"] });
      navigate(`/exams/${data.exam.id}`);
    },
  });

  const discardExamMutation = useMutation({
    mutationFn: (examId: string) => discardExam(examId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["exams"] });
      navigate("/exams");
    },
    onError: (err) => {
      const code = (err as Error & { code?: string }).code;
      if (code === "INVALID_EXAM_STATE") {
        queryClient.invalidateQueries({ queryKey: ["exams"] });
        navigate("/exams");
      }
    },
  });

  const isMutating = submitExamMutation.isPending || discardExamMutation.isPending;

  useEffect(() => {
    if (currentItem) {
      setLocalAnswer(currentItem.answer.raw || "");
      setSaveStatus("idle");
    }
  }, [currentItem?.itemId]);

  const handleSaveAnswer = useCallback(async () => {
    if (!exam || !currentItem) return;
    if (localAnswer === (currentItem.answer.raw || "")) {
      setSaveStatus("idle");
      return;
    }
    setSaveStatus("saving");
    await saveAnswerMutation.mutateAsync({
      examId: exam.id,
      itemId: currentItem.itemId,
      request: { answer: localAnswer || null },
    });
  }, [exam, currentItem, localAnswer, saveAnswerMutation]);

  const handleBlur = useCallback(() => {
    void handleSaveAnswer();
  }, [handleSaveAnswer]);

  const handlePrevious = useCallback(async () => {
    await handleSaveAnswer();
    setCurrentItemIndex((prev) => Math.max(0, prev - 1));
  }, [handleSaveAnswer]);

  const handleNext = useCallback(async () => {
    await handleSaveAnswer();
    setCurrentItemIndex((prev) => Math.min(items.length - 1, prev + 1));
  }, [handleSaveAnswer, items.length]);

  const handleSubmit = useCallback(() => {
    if (!exam) return;
    setShowSubmitConfirm(true);
  }, [exam]);

  const confirmSubmit = useCallback(async () => {
    if (!exam) return;
    await handleSaveAnswer();
    submitExamMutation.mutate(exam.id);
  }, [exam, handleSaveAnswer, submitExamMutation]);

  const handleDiscard = useCallback(() => {
    setShowDiscardConfirm(true);
  }, []);

  const confirmDiscard = useCallback(() => {
    if (!exam) return;
    discardExamMutation.mutate(exam.id);
  }, [exam, discardExamMutation]);

  const handleCreateExam = useCallback(() => {
    createExamMutation.mutate({ maxProblemCount: 10 });
  }, [createExamMutation]);

  const handleOpenPrintPreview = useCallback(() => {
    setShowPrintPreview(true);
  }, []);

  const handleClosePrintPreview = useCallback(() => {
    setShowPrintPreview(false);
  }, []);

  const handlePrint = useCallback(() => {
    window.print();
  }, []);

  const pageCanvasStyle: React.CSSProperties = {
    minHeight: "calc(100vh - 60px)",
    backgroundColor: "var(--color-surface-muted)",
    color: "var(--color-text)",
    padding: "1rem",
  };

  const contentWrapperStyle: React.CSSProperties = {
    maxWidth: "800px",
    margin: "0 auto",
  };

  if (isLoadingExam) {
    return (
      <main style={pageCanvasStyle}>
        <div style={{ ...contentWrapperStyle, textAlign: "center" }}>
          <div>Loading...</div>
        </div>
      </main>
    );
  }

  const isNotFoundError = examError instanceof ApiError && examError.status === 404;
  if (isNotFoundError || !exam) {
    return (
      <main style={pageCanvasStyle}>
        <div style={contentWrapperStyle}>
          <h1>Active Exam</h1>
          <div style={{ textAlign: "center", padding: "3rem", backgroundColor: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "0.5rem" }}>
            <p>No active exam found.</p>
            <button
              onClick={handleCreateExam}
              disabled={createExamMutation.isPending}
              style={{
                padding: "0.75rem 1.5rem",
                backgroundColor: createExamMutation.isPending ? "var(--color-primary-disabled)" : "var(--color-primary)",
                color: "white",
                border: "none",
                borderRadius: "0.25rem",
                cursor: createExamMutation.isPending ? "not-allowed" : "pointer",
                fontSize: "1rem",
                marginTop: "1rem",
              }}
            >
              {createExamMutation.isPending ? "Creating..." : "Start New Exam"}
            </button>
            {createExamMutation.error && (
              <p style={{ color: "var(--color-text-danger)", marginTop: "1rem" }}>
                {(createExamMutation.error as Error).message}
              </p>
            )}
          </div>
        </div>
      </main>
    );
  }

  if (examError) {
    return (
      <main style={pageCanvasStyle}>
        <div style={contentWrapperStyle}>
          <div style={{ color: "var(--color-text-danger)", padding: "1rem", backgroundColor: "var(--color-danger-bg)", borderRadius: "0.25rem" }}>
            Error loading exam: {(examError as Error).message}
          </div>
        </div>
      </main>
    );
  }

  if (!currentItem) {
    return (
      <main style={pageCanvasStyle}>
        <div style={contentWrapperStyle}>
          <div>No items in this exam.</div>
        </div>
      </main>
    );
  }

  const options = parseOptions(currentItem.problem.text);
  const isFirstItem = currentItemIndex === 0;
  const isLastItem = currentItemIndex === items.length - 1;

  return (
    <main style={pageCanvasStyle}>
      <div style={contentWrapperStyle}>
      <div style={{ marginBottom: "1.5rem" }}>
        <h1>Active Exam</h1>
        <p style={{ color: "var(--color-text-muted)" }}>
          Question {currentItemIndex + 1} of {items.length} | Answered: {exam.summary.answeredProblems} of {exam.summary.totalProblems}
        </p>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", padding: "0.5rem 0", borderBottom: "1px solid var(--color-border)" }}>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button
            onClick={handlePrevious}
            disabled={isFirstItem || isMutating}
            style={{
              padding: "0.5rem 1rem",
              cursor: isFirstItem || isMutating ? "not-allowed" : "pointer",
              opacity: isFirstItem ? 0.5 : 1,
            }}
          >
            Previous
          </button>
          <button
            onClick={handleNext}
            disabled={isLastItem || isMutating}
            style={{
              padding: "0.5rem 1rem",
              cursor: isLastItem || isMutating ? "not-allowed" : "pointer",
              opacity: isLastItem ? 0.5 : 1,
            }}
          >
            Next
          </button>
        </div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button
            onClick={handleOpenPrintPreview}
            disabled={isMutating}
            style={{
              padding: "0.5rem 1rem",
              cursor: isMutating ? "not-allowed" : "pointer",
            }}
          >
            Print
          </button>
          <button
            onClick={handleDiscard}
            disabled={isMutating}
            style={{
              padding: "0.5rem 1rem",
              backgroundColor: "var(--color-danger-bg)",
              color: "var(--color-text-danger)",
              border: "1px solid var(--color-danger-border)",
              borderRadius: "0.25rem",
              cursor: isMutating ? "not-allowed" : "pointer",
            }}
          >
            Discard
          </button>
          <button
            onClick={handleSubmit}
            disabled={isMutating}
            style={{
              padding: "0.5rem 1.5rem",
              backgroundColor: submitExamMutation.isPending ? "var(--color-primary-disabled)" : "var(--color-success)",
              color: "white",
              border: "none",
              borderRadius: "0.25rem",
              cursor: isMutating ? "not-allowed" : "pointer",
            }}
          >
            {submitExamMutation.isPending ? "Submitting..." : "Submit Exam"}
          </button>
        </div>
      </div>

      <div style={{ backgroundColor: "var(--color-surface-muted)", border: "1px solid var(--color-border)", borderRadius: "0.5rem", padding: "1.5rem", marginBottom: "1rem" }}>
        <LatexText
          text={currentItem.problem.text}
          style={{ fontSize: "1.125rem", lineHeight: "1.75", marginBottom: "1.5rem", whiteSpace: "pre-wrap" }}
        />

        {currentItem.problem.graphDsl && (
          <div style={{ marginBottom: "1.5rem" }}>
            <GraphSandbox dsl={currentItem.problem.graphDsl} height={250} />
          </div>
        )}

        {currentItem.problem.imageUrl && (
          <CollapsibleImage
            src={currentItem.problem.imageUrl}
            alt="Problem"
            style={{ maxWidth: "100%", height: "auto", borderRadius: "0.25rem" }}
          />
        )}

        <div style={{ marginTop: "1.5rem" }}>
          <label style={{ display: "block", fontWeight: "600", marginBottom: "0.5rem" }}>
            Your Answer:
          </label>
          <AnswerInput
            problemType={currentItem.problem.problemType}
            value={localAnswer}
            onChange={setLocalAnswer}
            onBlur={handleBlur}
            options={options}
            disabled={isMutating}
          />
          <div
            style={{ marginTop: "0.5rem", fontSize: "0.875rem", color:
              saveStatus === "saving" ? "var(--color-warning)" :
              saveStatus === "saved" ? "var(--color-success)" :
              "var(--color-text-muted)"
            }}
          >
            {saveStatus === "saving" ? "Saving..." : saveStatus === "saved" ? "Saved" : ""}
          </div>
        </div>
      </div>

      {showSubmitConfirm && (
        <Modal
          isOpen={true}
          onClose={() => setShowSubmitConfirm(false)}
          cardStyle={{ padding: "2rem", borderRadius: "0.5rem" }}
        >
          <h2 style={{ marginTop: 0 }}>Submit Exam?</h2>
          <p>
            You have answered {exam.summary.answeredProblems} of {exam.summary.totalProblems} questions.
            Are you sure you want to submit?
          </p>
          <div style={{ display: "flex", gap: "1rem", marginTop: "1.5rem", justifyContent: "flex-end" }}>
            <button
              onClick={() => setShowSubmitConfirm(false)}
              disabled={submitExamMutation.isPending}
              style={{ padding: "0.5rem 1rem" }}
            >
              Cancel
            </button>
            <button
              onClick={() => void confirmSubmit()}
              disabled={submitExamMutation.isPending}
              style={{
                padding: "0.5rem 1rem",
                backgroundColor: submitExamMutation.isPending ? "var(--color-primary-disabled)" : "var(--color-success)",
                color: "white",
                border: "none",
                borderRadius: "0.25rem",
                cursor: submitExamMutation.isPending ? "not-allowed" : "pointer",
              }}
            >
              {submitExamMutation.isPending ? "Submitting..." : "Submit"}
            </button>
          </div>
        </Modal>
      )}

      {showDiscardConfirm && (
        <Modal
          isOpen={true}
          onClose={() => setShowDiscardConfirm(false)}
          cardStyle={{ padding: "2rem", borderRadius: "0.5rem" }}
        >
          <h2 style={{ marginTop: 0 }}>Discard Exam?</h2>
          <p>
            This exam will be closed and marked as discarded. It will appear in your exam history but will not affect your statistics.
          </p>
          <div style={{ display: "flex", gap: "1rem", marginTop: "1.5rem", justifyContent: "flex-end" }}>
            <button
              onClick={() => setShowDiscardConfirm(false)}
              disabled={discardExamMutation.isPending}
              style={{ padding: "0.5rem 1rem" }}
            >
              Cancel
            </button>
            <button
              onClick={() => void confirmDiscard()}
              disabled={discardExamMutation.isPending}
              style={{
                padding: "0.5rem 1rem",
                backgroundColor: discardExamMutation.isPending ? "var(--color-danger-border)" : "var(--color-danger)",
                color: "white",
                border: "none",
                borderRadius: "0.25rem",
                cursor: discardExamMutation.isPending ? "not-allowed" : "pointer",
              }}
            >
              {discardExamMutation.isPending ? "Discarding..." : "Discard Exam"}
            </button>
          </div>
        </Modal>
      )}

      {showPrintPreview && (
        <Modal
          isOpen={true}
          onClose={handleClosePrintPreview}
          overlayTestId="print-preview-overlay"
          cardStyle={{
            padding: "1.5rem",
            borderRadius: "0.5rem",
            maxWidth: "900px",
            width: "95vw",
            maxHeight: "90vh",
            overflow: "auto",
          }}
        >
          <div className="print-preview-modal">
            <div
              className="print-preview-controls"
              style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem", marginBottom: "1rem" }}
            >
              <button
                onClick={handleClosePrintPreview}
                style={{ padding: "0.5rem 1rem" }}
              >
                Cancel
              </button>
              <button
                data-testid="print-preview-print-button"
                onClick={handlePrint}
                style={{
                  padding: "0.5rem 1rem",
                  backgroundColor: "var(--color-primary)",
                  color: "white",
                  border: "none",
                  borderRadius: "0.25rem",
                  cursor: "pointer",
                }}
              >
                Print
              </button>
            </div>
            <div className="print-preview-content">
              <h2 style={{ marginTop: 0, textAlign: "center" }}>Exam Paper</h2>
              {items.map((item, index) => (
                <div
                  key={item.itemId}
                  className="print-preview-item"
                  data-testid="print-preview-item"
                  style={{ marginBottom: "1.5rem", breakInside: "avoid" }}
                >
                  <div style={{ fontWeight: 600, marginBottom: "0.5rem" }}>
                    Question {index + 1}
                  </div>
                  {item.problem.graphDsl ? (
                    <div style={{ display: "flex", gap: "1rem" }}>
                      <div style={{ flex: 1 }}>
                        <LatexText text={item.problem.text} />
                      </div>
                      <div style={{ flex: 1 }} data-testid="print-preview-graph">
                        <GraphSandbox dsl={item.problem.graphDsl} height={250} />
                      </div>
                    </div>
                  ) : (
                    <div data-testid="print-preview-text-full">
                      <LatexText text={item.problem.text} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </Modal>
      )}
      </div>
    </main>
  );
}
