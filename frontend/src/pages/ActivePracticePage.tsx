import { useState, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { CollapsibleImage } from "@/components/CollapsibleImage";
import { LatexText } from "@/components/LatexText";
import { AnswerInput, parseOptions } from "@/components/AnswerInput";
import type { PracticeProblem, PracticeNextResponse, PracticeAttemptResult } from "@/types/practice";

type PracticePhase = "showing" | "grading" | "feedback";

export function ActivePracticePage() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();

  const problemFromNav = (location.state as { problem?: PracticeProblem } | null)?.problem ?? null;
  const [currentProblem, setCurrentProblem] = useState<PracticeProblem | null>(problemFromNav);
  const [phase, setPhase] = useState<PracticePhase>("showing");
  const [answer, setAnswer] = useState("");
  const [gradingResult, setGradingResult] = useState<PracticeAttemptResult | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [explainInfoMessage, setExplainInfoMessage] = useState<string | null>(null);
  const [isExplainHovered, setIsExplainHovered] = useState(false);

  const { data: solutionStatusData } = useQuery({
    queryKey: ["solution-status", currentProblem?.id],
    queryFn: () => api.getSolutionStatus(currentProblem!.id),
    enabled: !!currentProblem?.id && phase === "feedback",
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return (status === "pending" || status === "generating") ? 2000 : false;
    },
  });

  const solutionStatus = solutionStatusData?.status;

  useEffect(() => {
    if (!currentProblem) {
      navigate("/practice", { replace: true });
    }
  }, [currentProblem, navigate]);

  const submitMutation = useMutation({
    mutationFn: ({ problemId, submittedAnswer }: { problemId: string; submittedAnswer: string }) =>
      api.post<PracticeAttemptResult>("/practice/attempts", { problemId, submittedAnswer }),
    onSuccess: (result) => {
      setGradingResult(result);
      setPhase("feedback");
    },
  });

  const nextMutation = useMutation({
    mutationFn: () => api.post<PracticeNextResponse>("/practice/next", {}),
    onSuccess: (response) => {
      if (response.status === "ok" && response.problem) {
        setCurrentProblem(response.problem);
        setAnswer("");
        setGradingResult(null);
        setPhase("showing");
        setStatusMessage(null);
        setExplainInfoMessage(null);
      } else if (response.status === "no_eligible") {
        setStatusMessage("No problems available for practice right now. Try again later.");
      } else if (response.status === "no_problems") {
        setStatusMessage("Add some problems first to start practicing.");
      }
      queryClient.invalidateQueries({ queryKey: ["practice-history"] });
    },
  });

  const handleSubmit = () => {
    if (!currentProblem || !answer.trim()) return;
    setPhase("grading");
    submitMutation.mutate({ problemId: currentProblem.id, submittedAnswer: answer });
  };

  const handleNext = () => {
    nextMutation.mutate();
  };

  const handleExplainClick = () => {
    const status = solutionStatusData?.status;
    if (status === "ready") {
      navigate(`/coaching/${currentProblem!.id}`, { state: { from: "/practice" } });
    } else if (status === "pending" || status === "generating") {
      setExplainInfoMessage("Solution is being generated, please try again shortly");
    }
  };

  const handleQuit = () => {
    queryClient.invalidateQueries({ queryKey: ["practice-history"] });
    navigate("/practice");
  };

  if (!currentProblem) {
    return null;
  }

  const options = parseOptions(currentProblem.text);
  const isMutating = submitMutation.isPending || nextMutation.isPending;

  return (
    <main style={{ maxWidth: "800px", margin: "2rem auto", padding: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.5rem", flexWrap: "wrap", gap: "1rem" }}>
        <h1 style={{ margin: 0 }}>Practice</h1>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button
            type="button"
            onClick={handleNext}
            disabled={isMutating}
            data-testid="skip-button"
            style={{
              padding: "0.5rem 1rem",
              border: "1px solid #d1d5db",
              borderRadius: "0.25rem",
              backgroundColor: "#f3f4f6",
              cursor: isMutating ? "not-allowed" : "pointer",
            }}
          >
            Skip
          </button>
          <button
            type="button"
            onClick={handleQuit}
            data-testid="quit-button"
            style={{
              padding: "0.5rem 1rem",
              backgroundColor: "#fee2e2",
              color: "#dc2626",
              border: "1px solid #fecaca",
              borderRadius: "0.25rem",
              cursor: "pointer",
            }}
          >
            Quit
          </button>
        </div>
      </div>

      {statusMessage && (
        <div
          style={{
            padding: "1rem",
            backgroundColor: "#fef3c7",
            border: "1px solid #fcd34d",
            borderRadius: "0.5rem",
            marginBottom: "1rem",
          }}
          data-testid="status-message"
        >
          {statusMessage}
        </div>
      )}

      <div
        style={{
          backgroundColor: "#f9fafb",
          border: "1px solid #e5e7eb",
          borderRadius: "0.5rem",
          padding: "1.5rem",
          marginBottom: "1rem",
        }}
      >
        <LatexText
          text={currentProblem.text}
          style={{
            fontSize: "1.125rem",
            lineHeight: "1.75",
            marginBottom: "1.5rem",
            whiteSpace: "pre-wrap",
          }}
          data-testid="problem-text"
        />

        {currentProblem.imageUrl && (
          <CollapsibleImage
            src={currentProblem.imageUrl}
            alt="Problem"
            style={{ maxWidth: "100%", height: "auto", borderRadius: "0.25rem" }}
          />
        )}

        {phase === "showing" || phase === "grading" ? (
          <div style={{ marginTop: "1.5rem" }}>
            <label style={{ display: "block", fontWeight: 600, marginBottom: "0.5rem" }}>
              Your Answer:
            </label>
            <AnswerInput
              problemType={currentProblem.type}
              value={answer}
              onChange={setAnswer}
              options={options}
              disabled={phase === "grading"}
            />
            <div style={{ marginTop: "1rem" }}>
              <button
                type="button"
                onClick={handleSubmit}
                disabled={phase === "grading" || !answer.trim()}
                data-testid="submit-button"
                style={{
                  padding: "0.75rem 1.5rem",
                  backgroundColor: phase === "grading" || !answer.trim() ? "#93c5fd" : "#2563eb",
                  color: "white",
                  border: "none",
                  borderRadius: "0.375rem",
                  cursor: phase === "grading" || !answer.trim() ? "not-allowed" : "pointer",
                  fontWeight: 600,
                }}
              >
                {phase === "grading" ? "Grading..." : "Submit"}
              </button>
            </div>
          </div>
        ) : null}
      </div>

      {phase === "feedback" && gradingResult && (
        <div
          style={{
            padding: "1rem 1.5rem",
            borderRadius: "0.5rem",
            marginBottom: "1rem",
            backgroundColor:
              gradingResult.gradingStatus === "correct"
                ? "#dcfce7"
                : gradingResult.gradingStatus === "incorrect"
                  ? "#fee2e2"
                  : "#fef3c7",
            border:
              gradingResult.gradingStatus === "correct"
                ? "1px solid #86efac"
                : gradingResult.gradingStatus === "incorrect"
                  ? "1px solid #fca5a5"
                  : "1px solid #fcd34d",
          }}
          data-testid="grading-feedback"
        >
          <div style={{ fontWeight: 600, fontSize: "1.125rem" }}>
            {gradingResult.gradingStatus === "correct"
              ? "Correct!"
              : gradingResult.gradingStatus === "incorrect"
                ? "Incorrect"
                : "Pending Review"}
          </div>
          <div style={{ color: "#4b5563", fontSize: "0.875rem", marginTop: "0.25rem" }}>
            Graded via: {gradingResult.gradingMethod}
          </div>
        </div>
      )}

      {phase === "feedback" && explainInfoMessage && (
        <div
          style={{
            padding: "0.75rem 1rem",
            backgroundColor: "#fffbeb",
            border: "1px solid #fcd34d",
            borderRadius: "0.375rem",
            color: "#92400e",
            fontSize: "0.875rem",
            marginBottom: "1rem",
          }}
          data-testid="explain-info-message"
        >
          {explainInfoMessage}
        </div>
      )}

      {phase === "feedback" && (
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button
            type="button"
            onClick={handleNext}
            disabled={nextMutation.isPending}
            data-testid="next-button"
            style={{
              padding: "0.75rem 1.5rem",
              backgroundColor: nextMutation.isPending ? "#93c5fd" : "#2563eb",
              color: "white",
              border: "none",
              borderRadius: "0.375rem",
              cursor: nextMutation.isPending ? "not-allowed" : "pointer",
              fontWeight: 600,
            }}
          >
            {nextMutation.isPending ? "Loading..." : "Next Problem"}
          </button>
          {solutionStatus && solutionStatus !== "none" && (
            <button
              type="button"
              onClick={handleExplainClick}
              disabled={solutionStatus === "failed"}
              onMouseEnter={() => setIsExplainHovered(true)}
              onMouseLeave={() => setIsExplainHovered(false)}
              data-testid="explain-button"
              style={{
                padding: "0.75rem 1.5rem",
                borderRadius: "0.375rem",
                fontWeight: 600,
                transition: "all 0.2s ease-in-out",
                ...(solutionStatus === "failed"
                  ? {
                      background: "#f3f4f6",
                      color: "#9ca3af",
                      border: "1px solid #e5e7eb",
                      cursor: "not-allowed",
                    }
                  : solutionStatus === "pending" || solutionStatus === "generating"
                    ? {
                        background: "#f8fafc",
                        color: "#6366f1",
                        border: "2px dashed #6366f1",
                        cursor: "pointer",
                        boxShadow: "0 0 8px rgba(99, 102, 241, 0.1)",
                      }
                    : {
                        background: isExplainHovered
                          ? "linear-gradient(135deg, #4f46e5, #4338ca)"
                          : "linear-gradient(135deg, #6366f1, #4f46e5)",
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
          )}
          <button
            type="button"
            onClick={handleQuit}
            style={{
              padding: "0.75rem 1rem",
              border: "1px solid #d1d5db",
              borderRadius: "0.375rem",
              backgroundColor: "#ffffff",
              cursor: "pointer",
            }}
          >
            Done
          </button>
        </div>
      )}
    </main>
  );
}
