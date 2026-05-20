import { useState, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/api/client";
import { CollapsibleImage } from "@/components/CollapsibleImage";
import type { PracticeProblem, PracticeNextResponse, PracticeAttemptResult } from "@/types/practice";

type PracticePhase = "showing" | "grading" | "feedback";

function extractOptionKey(option: string): string {
  const match = option.trim().match(/^([A-Za-z]|\d+)\s*[.):\-]?(?:\s|$)/);
  return (match?.[1] ?? option).trim();
}

function parseOptions(text: string): string[] {
  const lines = text.split("\n");
  const options: string[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (/^[A-Z][.):\s]/.test(trimmed) || /^\d+[.):\s]/.test(trimmed)) {
      options.push(trimmed);
    }
  }
  return options;
}

function SingleChoiceInput({ value, onChange, options, disabled }: {
  value: string; onChange: (v: string) => void; options: string[]; disabled?: boolean;
}) {
  if (options.length === 0) {
    return (
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        style={{ width: "100%", padding: "0.5rem", fontSize: "1rem", border: "1px solid #d1d5db", borderRadius: "0.25rem" }}
      />
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
      {options.map((option) => {
        const optionValue = extractOptionKey(option);
        return (
          <label key={option} style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.5rem", cursor: disabled ? "default" : "pointer", borderRadius: "0.25rem" }}>
            <input type="radio" name="practice-single-choice" value={optionValue} checked={value === optionValue || value === option} onChange={() => onChange(optionValue)} disabled={disabled} />
            <span>{option}</span>
          </label>
        );
      })}
    </div>
  );
}

function MultiChoiceInput({ value, onChange, options, disabled }: {
  value: string; onChange: (v: string) => void; options: string[]; disabled?: boolean;
}) {
  const selectedValues = value ? value.split(",").map((v) => v.trim()).filter(Boolean) : [];
  const handleToggle = (optionValue: string) => {
    const newValues = selectedValues.includes(optionValue)
      ? selectedValues.filter((v) => v !== optionValue)
      : [...selectedValues, optionValue];
    onChange(newValues.join(", "));
  };
  if (options.length === 0) {
    return (
      <input type="text" value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} placeholder="Enter options separated by commas" style={{ width: "100%", padding: "0.5rem", fontSize: "1rem", border: "1px solid #d1d5db", borderRadius: "0.25rem" }} />
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
      {options.map((option) => {
        const optionValue = extractOptionKey(option);
        return (
          <label key={option} style={{ display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.5rem", cursor: disabled ? "default" : "pointer", borderRadius: "0.25rem" }}>
            <input type="checkbox" checked={selectedValues.includes(optionValue) || selectedValues.includes(option)} onChange={() => handleToggle(optionValue)} disabled={disabled} />
            <span>{option}</span>
          </label>
        );
      })}
    </div>
  );
}

function AnswerInput({ problemType, value, onChange, options, disabled }: {
  problemType: string; value: string; onChange: (v: string) => void; options: string[]; disabled?: boolean;
}) {
  switch (problemType) {
    case "single-choice":
      return <SingleChoiceInput value={value} onChange={onChange} options={options} disabled={disabled} />;
    case "multi-choice":
      return <MultiChoiceInput value={value} onChange={onChange} options={options} disabled={disabled} />;
    case "short-answer":
      return (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          style={{ width: "100%", padding: "0.5rem", fontSize: "1rem", border: "1px solid #d1d5db", borderRadius: "0.25rem", minHeight: "120px", resize: "vertical" }}
        />
      );
    case "fill-in-the-blank":
    default:
      return (
        <input type="text" value={value} onChange={(e) => onChange(e.target.value)} disabled={disabled} style={{ width: "100%", padding: "0.5rem", fontSize: "1rem", border: "1px solid #d1d5db", borderRadius: "0.25rem" }} />
      );
  }
}

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

  const handleSkip = () => {
    nextMutation.mutate();
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
            onClick={handleSkip}
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
        <div
          style={{
            fontSize: "1.125rem",
            lineHeight: "1.75",
            marginBottom: "1.5rem",
            whiteSpace: "pre-wrap",
          }}
          data-testid="problem-text"
        >
          {currentProblem.text}
        </div>

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

      {phase === "feedback" && (
        <div style={{ display: "flex", gap: "0.5rem" }}>
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