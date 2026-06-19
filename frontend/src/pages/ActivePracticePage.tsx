import { useState, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { CollapsibleImage } from "@/components/CollapsibleImage";
import { GraphSandbox } from "@/components/GraphSandbox";
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

  const pageCanvasStyle: React.CSSProperties = {
    minHeight: "calc(100vh - 60px)",
    backgroundColor: "var(--color-bg)",
    color: "var(--color-text)",
    padding: "2rem 1.5rem",
  };

  const contentWrapperStyle: React.CSSProperties = {
    maxWidth: "800px",
    margin: "0 auto",
  };

  if (!currentProblem) {
    return (
      <main style={pageCanvasStyle}>
        <div style={contentWrapperStyle}>
          <div style={{ color: "var(--color-text-muted)", fontWeight: 600 }}>No active problem.</div>
        </div>
      </main>
    );
  }

  const options = parseOptions(currentProblem.text);
  const isMutating = submitMutation.isPending || nextMutation.isPending;

  return (
    <main style={pageCanvasStyle}>
      <div style={contentWrapperStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "2rem", flexWrap: "wrap", gap: "1rem" }}>
        <h1 style={{ margin: 0, fontSize: "2rem", fontWeight: 800, letterSpacing: "-0.02em" }}>Practice</h1>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button
            type="button"
            onClick={handleNext}
            disabled={isMutating}
            data-testid="skip-button"
            className="btn btn-secondary"
            style={{
              padding: "0.5rem 1.25rem",
              fontSize: "0.875rem",
              fontWeight: 700,
            }}
          >
            Skip
          </button>
          <button
            type="button"
            onClick={handleQuit}
            data-testid="quit-button"
            className="btn btn-danger"
            style={{
              padding: "0.5rem 1.25rem",
              fontSize: "0.875rem",
              fontWeight: 700,
            }}
          >
            Quit
          </button>
        </div>
      </div>

      {statusMessage && (
        <div
          style={{
            padding: "1.25rem",
            backgroundColor: "var(--color-warning-bg)",
            border: "1px dashed var(--color-warning-border)",
            borderRadius: "var(--radius-md)",
            marginBottom: "1.5rem",
            fontSize: "0.95rem",
            color: "var(--color-warning-text)",
            fontWeight: 600
          }}
          data-testid="status-message"
        >
          {statusMessage}
        </div>
      )}

      <div
        className="card-premium"
        style={{
          backgroundColor: "var(--color-surface)",
          border: "1px solid var(--color-border)",
          padding: "2rem",
          marginBottom: "1.5rem",
          display: "flex",
          flexDirection: "column",
          gap: "1.5rem"
        }}
      >
        <LatexText
          text={currentProblem.text}
          style={{
            fontSize: "1.125rem",
            lineHeight: "1.6",
            whiteSpace: "pre-wrap",
            color: "var(--color-text)",
          }}
          data-testid="problem-text"
        />

        {currentProblem.graphDsl && (
          <div style={{ maxWidth: "400px" }}>
            <GraphSandbox dsl={currentProblem.graphDsl} height={250} />
          </div>
        )}

        {currentProblem.imageUrl && (
          <div style={{ display: "flex", backgroundColor: "var(--color-surface-muted)", padding: "1rem", borderRadius: "var(--radius-lg)", border: "1px solid var(--color-border)" }}>
            <CollapsibleImage
              src={currentProblem.imageUrl}
              alt="Problem"
              style={{ maxWidth: "100%", height: "auto", borderRadius: "var(--radius-md)" }}
            />
          </div>
        )}

        {phase === "showing" || phase === "grading" ? (
          <div style={{ borderTop: "1px solid var(--color-border)", paddingTop: "1.5rem", marginTop: "0.5rem" }}>
            <label 
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
              Your Answer:
            </label>
            <AnswerInput
              problemType={currentProblem.type}
              value={answer}
              onChange={setAnswer}
              options={options}
              disabled={phase === "grading"}
            />
            <div style={{ marginTop: "1.5rem" }}>
              <button
                type="button"
                onClick={handleSubmit}
                disabled={phase === "grading" || !answer.trim()}
                data-testid="submit-button"
                className="btn btn-primary"
                style={{
                  padding: "0.6rem 1.5rem",
                  fontSize: "0.875rem",
                  fontWeight: 700,
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
            padding: "1.25rem 1.5rem",
            borderRadius: "var(--radius-md)",
            marginBottom: "1.5rem",
            backgroundColor:
              gradingResult.gradingStatus === "correct"
                ? "var(--color-success-bg)"
                : gradingResult.gradingStatus === "incorrect"
                  ? "var(--color-danger-bg)"
                  : "var(--color-warning-bg)",
            border:
              gradingResult.gradingStatus === "correct"
                ? "1px solid var(--color-success-border)"
                : gradingResult.gradingStatus === "incorrect"
                  ? "1px solid var(--color-danger-border)"
                  : "1px solid var(--color-warning-border)",
            display: "flex",
            flexDirection: "column",
            gap: "0.25rem"
          }}
          data-testid="grading-feedback"
        >
          <div 
            style={{ 
              fontWeight: 800, 
              fontSize: "1.2rem",
              color: 
                gradingResult.gradingStatus === "correct"
                  ? "var(--color-success-text)"
                  : gradingResult.gradingStatus === "incorrect"
                    ? "var(--color-text-danger-secondary)"
                    : "var(--color-warning-text)"
            }}
          >
            {gradingResult.gradingStatus === "correct"
              ? "Correct!"
              : gradingResult.gradingStatus === "incorrect"
                ? "Incorrect"
                : "Pending Review"}
          </div>
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.8125rem", fontWeight: 500 }}>
            Graded via: {gradingResult.gradingMethod}
          </div>
        </div>
      )}

      {phase === "feedback" && explainInfoMessage && (
        <div
          className="badge badge-warning"
          style={{
            padding: "0.5rem 0.75rem",
            fontSize: "0.875rem",
            textTransform: "none",
            letterSpacing: "normal",
            fontWeight: 500,
            display: "block",
            marginBottom: "1.5rem",
          }}
          data-testid="explain-info-message"
        >
          {explainInfoMessage}
        </div>
      )}

      {phase === "feedback" && (
        <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", borderTop: "1px solid var(--color-border)", paddingTop: "1.5rem", marginTop: "1rem" }}>
          <button
            type="button"
            onClick={handleNext}
            disabled={nextMutation.isPending}
            data-testid="next-button"
            className="btn btn-primary"
            style={{
              padding: "0.6rem 1.5rem",
              fontSize: "0.875rem",
              fontWeight: 700,
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
                padding: "0.6rem 1.5rem",
                borderRadius: "var(--radius-md)",
                fontWeight: 700,
                fontSize: "0.875rem",
                transition: "all 0.2s ease-in-out",
                ...(solutionStatus === "failed"
                  ? {
                      background: "var(--color-surface-muted)",
                      color: "var(--color-disabled-text)",
                      border: "1px solid var(--color-border)",
                      cursor: "not-allowed",
                    }
                  : solutionStatus === "pending" || solutionStatus === "generating"
                    ? {
                        background: "var(--color-bg)",
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
          )}
          <button
            type="button"
            onClick={handleQuit}
            className="btn btn-secondary"
            style={{
              padding: "0.6rem 1.25rem",
              fontSize: "0.875rem",
              fontWeight: 700,
            }}
          >
            Done
          </button>
        </div>
      )}
      </div>
    </main>
  );
}
