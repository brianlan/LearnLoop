import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
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

  if (isLoadingExam) {
    return (
      <main style={{ maxWidth: "800px", margin: "2rem auto", padding: "1rem", textAlign: "center" }}>
        <div>Loading...</div>
      </main>
    );
  }

  const isNotFoundError = examError instanceof Error && examError.message.includes("404");
  if (isNotFoundError || !exam) {
    return (
      <main style={{ maxWidth: "800px", margin: "2rem auto", padding: "1rem" }}>
        <h1>Active Exam</h1>
        <div style={{ textAlign: "center", padding: "3rem", backgroundColor: "#f9fafb", border: "1px solid #e5e7eb", borderRadius: "0.5rem" }}>
          <p>No active exam found.</p>
          <button
            onClick={handleCreateExam}
            disabled={createExamMutation.isPending}
            style={{
              padding: "0.75rem 1.5rem",
              backgroundColor: createExamMutation.isPending ? "#93c5fd" : "#3b82f6",
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
            <p style={{ color: "#dc2626", marginTop: "1rem" }}>
              {(createExamMutation.error as Error).message}
            </p>
          )}
        </div>
      </main>
    );
  }

  if (examError) {
    return (
      <main style={{ maxWidth: "800px", margin: "2rem auto", padding: "1rem" }}>
        <div style={{ color: "#dc2626", padding: "1rem", backgroundColor: "#fee2e2", borderRadius: "0.25rem" }}>
          Error loading exam: {(examError as Error).message}
        </div>
      </main>
    );
  }

  if (!currentItem) {
    return (
      <main style={{ maxWidth: "800px", margin: "2rem auto", padding: "1rem" }}>
        <div>No items in this exam.</div>
      </main>
    );
  }

  const options = parseOptions(currentItem.problem.text);
  const isFirstItem = currentItemIndex === 0;
  const isLastItem = currentItemIndex === items.length - 1;

  return (
    <main style={{ maxWidth: "800px", margin: "2rem auto", padding: "1rem" }}>
      <div style={{ marginBottom: "1.5rem" }}>
        <h1>Active Exam</h1>
        <p style={{ color: "#6b7280" }}>
          Question {currentItemIndex + 1} of {items.length} | Answered: {exam.summary.answeredProblems} of {exam.summary.totalProblems}
        </p>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", padding: "0.5rem 0", borderBottom: "1px solid #e5e7eb" }}>
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
            onClick={handleDiscard}
            disabled={isMutating}
            style={{
              padding: "0.5rem 1rem",
              backgroundColor: "#fee2e2",
              color: "#dc2626",
              border: "1px solid #fecaca",
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
              backgroundColor: submitExamMutation.isPending ? "#6ee7b7" : "#10b981",
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

      <div style={{ backgroundColor: "#f9fafb", border: "1px solid #e5e7eb", borderRadius: "0.5rem", padding: "1.5rem", marginBottom: "1rem" }}>
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
              saveStatus === "saving" ? "#f59e0b" :
              saveStatus === "saved" ? "#10b981" :
              "#6b7280"
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
                backgroundColor: submitExamMutation.isPending ? "#6ee7b7" : "#10b981",
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
                backgroundColor: discardExamMutation.isPending ? "#fca5a5" : "#dc2626",
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
    </main>
  );
}
