import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation } from "@tanstack/react-query";

import { api } from "@/api/client";
import { formatDate } from "@/utils/format";
import { LatexText } from "@/components/LatexText";
import type { PracticeHistoryItem, PracticeNextResponse } from "@/types/practice";

interface PracticeHistoryResponse {
  items: PracticeHistoryItem[];
}

interface PracticeStatsResponse {
  practiceableCount: number;
}

function getResultStyle(result: string) {
  switch (result) {
    case "correct":
      return { backgroundColor: "var(--color-success-bg)", color: "var(--color-success-text)" };
    case "incorrect":
      return { backgroundColor: "var(--color-danger-bg)", color: "var(--color-text-danger-secondary)" };
    default:
      return { backgroundColor: "var(--color-warning-bg)", color: "var(--color-warning-text)" };
  }
}

function HistoryRow({
  item,
  isExpanded,
  onToggle,
}: {
  item: PracticeHistoryItem;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const navigate = useNavigate();
  const [explainInfoMessage, setExplainInfoMessage] = useState<string | null>(null);
  const resultStyle = getResultStyle(item.summary.lastResult || "");
  const { data: solutionStatusData } = useQuery({
    queryKey: ["solution-status", item.problemId],
    queryFn: () => api.getSolutionStatus(item.problemId),
    enabled: isExpanded,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "pending" || status === "generating" ? 2000 : false;
    },
  });

  const solutionStatus = solutionStatusData?.status;

  const handleExplainClick = () => {
    if (solutionStatus === "ready") {
      navigate(`/coaching/${item.problemId}`, { state: { from: "/practice" } });
    } else if (solutionStatus === "pending" || solutionStatus === "generating") {
      setExplainInfoMessage("Solution is being generated, please try again shortly");
    }
  };

  const renderExplainButton = () => {
    if (!solutionStatus || solutionStatus === "none") {
      return null;
    }

    return (
      <button
        type="button"
        onClick={handleExplainClick}
        disabled={solutionStatus === "failed"}
        data-testid={`explain-button-${item.problemId}`}
        style={{
          padding: "0.375rem 0.625rem",
          borderRadius: "0.375rem",
          fontSize: "0.75rem",
          fontWeight: 600,
          whiteSpace: "nowrap",
          ...(solutionStatus === "failed"
            ? {
                background: "var(--color-disabled-bg)",
                color: "var(--color-disabled-text)",
                border: "1px solid var(--color-border)",
                cursor: "not-allowed",
              }
            : solutionStatus === "pending" || solutionStatus === "generating"
              ? {
                  background: "var(--color-surface)",
                  color: "var(--color-link)",
                  border: "2px dashed var(--color-link)",
                  cursor: "pointer",
                }
              : {
                  background: "linear-gradient(135deg, var(--color-link), var(--color-primary))",
                  color: "white",
                  border: "none",
                  cursor: "pointer",
                }),
        }}
      >
        {solutionStatus === "pending" || solutionStatus === "generating"
          ? "AI Explain (Generating...)"
          : solutionStatus === "failed"
            ? "AI Explain (Unavailable)"
            : "AI Explain"}
      </button>
    );
  };

  return (
    <div
      style={{
        border: "1px solid var(--color-border)",
        borderRadius: "0.5rem",
        marginBottom: "0.5rem",
        overflow: "hidden",
      }}
    >
      <button
        type="button"
        onClick={onToggle}
        data-testid={`history-row-${item.problemId}`}
        style={{
          width: "100%",
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))",
          alignItems: "flex-start",
          gap: "1rem",
          textAlign: "left",
          backgroundColor: isExpanded ? "var(--color-surface-muted)" : "var(--color-surface)",
          border: "none",
          padding: "1rem",
          cursor: "pointer",
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              fontWeight: 600,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            <LatexText text={item.problemText} />
          </div>
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.875rem" }}>
            {item.problemType}
          </div>
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(64px, 1fr))",
            gap: "0.75rem",
            minWidth: 0,
          }}
        >
          <div>
            <div style={{ color: "var(--color-text-muted)", fontSize: "0.75rem" }}>Total</div>
            <div>{item.summary.totalAttempts}</div>
          </div>
          <div>
            <div style={{ color: "var(--color-text-muted)", fontSize: "0.75rem" }}>Correct</div>
            <div style={{ color: "var(--color-success)" }}>{item.summary.correctCount}</div>
          </div>
          <div>
            <div style={{ color: "var(--color-text-muted)", fontSize: "0.75rem" }}>Wrong</div>
            <div style={{ color: "var(--color-text-danger)" }}>{item.summary.wrongCount}</div>
          </div>
          <div>
            <div style={{ color: "var(--color-text-muted)", fontSize: "0.75rem" }}>Last</div>
            <div>{formatDate(item.summary.lastPracticedAt)}</div>
          </div>
          <div>
            <div style={{ color: "var(--color-text-muted)", fontSize: "0.75rem" }}>Result</div>
            <span
              style={{
                padding: "0.125rem 0.5rem",
                borderRadius: "9999px",
                fontSize: "0.75rem",
                fontWeight: 600,
                ...resultStyle,
              }}
            >
              {item.summary.lastResult || "—"}
            </span>
          </div>
        </div>
      </button>
      {isExpanded && (
        <div
          style={{
            padding: "1rem",
            borderTop: "1px solid var(--color-border)",
            backgroundColor: "var(--color-surface-muted)",
          }}
          data-testid={`attempts-${item.problemId}`}
        >
          {explainInfoMessage && (
            <div
              style={{
                padding: "0.75rem 1rem",
                backgroundColor: "var(--color-warning-bg)",
                border: "1px solid var(--color-warning-border)",
                borderRadius: "0.375rem",
                color: "var(--color-warning-text)",
                fontSize: "0.875rem",
                marginBottom: "0.75rem",
              }}
              data-testid={`explain-info-message-${item.problemId}`}
            >
              {explainInfoMessage}
            </div>
          )}
          {item.attempts.length === 0 ? (
            <div style={{ color: "var(--color-text-muted)" }}>No attempts recorded</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
              {item.attempts.map((attempt, idx) => (
                <div
                  key={idx}
                  style={{
                    padding: "0.75rem",
                    backgroundColor: "var(--color-surface)",
                    border: "1px solid var(--color-border)",
                    borderRadius: "0.375rem",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: "0.75rem",
                      marginBottom: "0.25rem",
                      flexWrap: "wrap",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                      <span style={{ fontWeight: 500 }}>{attempt.gradingStatus}</span>
                      {idx === 0 && renderExplainButton()}
                    </div>
                    <span style={{ color: "var(--color-text-muted)", fontSize: "0.875rem" }}>
                      {formatDate(attempt.createdAt)}
                    </span>
                  </div>
                  <div style={{ color: "var(--color-text)" }}>
                    Answer: {attempt.submittedAnswer}
                  </div>
                  {attempt.feedback && (
                    <div style={{ marginTop: "0.5rem", padding: "0.75rem", backgroundColor: "var(--color-primary-bg)", borderRadius: "0.25rem" }}>
                      <div style={{ fontSize: "0.875rem", color: "var(--color-primary-text)", marginBottom: "0.25rem" }}>Feedback:</div>
                      <div>{attempt.feedback}</div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function PracticePage() {
  const navigate = useNavigate();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery<PracticeHistoryResponse>({
    queryKey: ["practice-history"],
    queryFn: () => api.get<PracticeHistoryResponse>("/practice/history"),
  });

  const { data: statsData } = useQuery<PracticeStatsResponse>({
    queryKey: ["practice-stats"],
    queryFn: () => api.get<PracticeStatsResponse>("/practice/stats"),
  });

  const startPracticeMutation = useMutation({
    mutationFn: () => api.post<PracticeNextResponse>("/practice/next", {}),
    onSuccess: (response) => {
      if (response.status === "ok" && response.problem) {
        navigate("/practice/active", { state: { problem: response.problem } });
      } else if (response.status === "no_eligible") {
        setStatusMessage("No problems available for practice right now. Try again later.");
      } else if (response.status === "no_problems") {
        setStatusMessage("Add some problems first to start practicing.");
      }
    },
  });

  const handleStartPractice = () => {
    setStatusMessage(null);
    startPracticeMutation.mutate();
  };

  const items = data?.items ?? [];

  return (
    <main style={{ padding: "1rem", maxWidth: "800px", margin: "0 auto" }}>
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
          <h1 style={{ margin: 0 }}>Practice</h1>
          <p style={{ color: "var(--color-text-muted)", margin: "0.5rem 0 0" }}>
            {statsData?.practiceableCount !== undefined
              ? `${statsData.practiceableCount} problems available for practice.`
              : "Review your practice history or start a new session."}
          </p>
        </div>
        <button
          type="button"
          onClick={handleStartPractice}
          disabled={startPracticeMutation.isPending}
          data-testid="start-practice-button"
          style={{
            padding: "0.75rem 1rem",
            backgroundColor: startPracticeMutation.isPending ? "var(--color-primary-disabled)" : "var(--color-primary)",
            color: "white",
            border: "none",
            borderRadius: "0.375rem",
            cursor: startPracticeMutation.isPending ? "not-allowed" : "pointer",
            fontWeight: 600,
          }}
        >
          {startPracticeMutation.isPending ? "Starting..." : "Start Practice"}
        </button>
      </div>

      {statusMessage && (
        <div
          style={{
            padding: "1rem",
            backgroundColor: "var(--color-warning-bg)",
            border: "1px solid var(--color-warning-border)",
            borderRadius: "0.5rem",
            marginBottom: "1rem",
          }}
          data-testid="status-message"
        >
          {statusMessage}
        </div>
      )}

      {isLoading ? (
        <div data-testid="loading">Loading practice history...</div>
      ) : error ? (
        <div style={{ color: "var(--color-text-danger)" }} data-testid="error">
          Error loading history: {(error as Error).message}
        </div>
      ) : items.length === 0 ? (
        <div
          style={{
            padding: "2rem",
            textAlign: "center",
            backgroundColor: "var(--color-surface-muted)",
            border: "1px solid var(--color-border)",
            borderRadius: "0.5rem",
          }}
          data-testid="empty-state"
        >
          No practice history yet
        </div>
      ) : (
        <div>
          {items.map((item) => (
            <HistoryRow
              key={item.problemId}
              item={item}
              isExpanded={expandedId === item.problemId}
              onToggle={() =>
                setExpandedId((current) =>
                  current === item.problemId ? null : item.problemId
                )
              }
            />
          ))}
        </div>
      )}
    </main>
  );
}
