import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation } from "@tanstack/react-query";

import { api } from "@/api/client";
import { formatDate } from "@/utils/format";
import { LatexText } from "@/components/LatexText";
import type { PracticeHistoryItem, PracticeHistoryResponse, PracticeNextResponse } from "@/types/practice";

interface PracticeStatsResponse {
  practiceableCount: number;
}

function getResultStyleClass(result: string) {
  switch (result) {
    case "correct":
      return "badge-success";
    case "incorrect":
      return "badge-danger";
    default:
      return "badge-warning";
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
  const resultClass = getResultStyleClass(item.summary.lastResult || "");
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
          borderRadius: "var(--radius-md)",
          fontSize: "0.75rem",
          fontWeight: 700,
          whiteSpace: "nowrap",
          transition: "all 0.2s ease",
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
                }
              : {
                  background: "linear-gradient(135deg, var(--color-link), var(--color-primary))",
                  color: "white",
                  border: "none",
                  cursor: "pointer",
                  boxShadow: "0 2px 4px rgba(99, 102, 241, 0.2)",
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
      className="card-premium"
      style={{
        border: "1px solid var(--color-border)",
        marginBottom: "0.75rem",
        overflow: "hidden",
        padding: 0,
        backgroundColor: "var(--color-surface)",
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
          alignItems: "center",
          gap: "1.5rem",
          textAlign: "left",
          backgroundColor: isExpanded ? "var(--color-surface-muted)" : "transparent",
          border: "none",
          padding: "1.25rem",
          cursor: "pointer",
          transition: "background-color 0.2s ease",
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              fontWeight: 700,
              fontSize: "1.05rem",
              color: "var(--color-text)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            <LatexText text={item.problemText} />
          </div>
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.8125rem", marginTop: "0.25rem", fontWeight: 500 }}>
            {item.problemType}
          </div>
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(64px, 1fr))",
            gap: "1rem",
            minWidth: 0,
            alignItems: "center"
          }}
        >
          <div>
            <div style={{ color: "var(--color-text-muted)", fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "0.15rem" }}>Total</div>
            <div style={{ fontWeight: 600 }}>{item.summary.totalAttempts}</div>
          </div>
          <div>
            <div style={{ color: "var(--color-text-muted)", fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "0.15rem" }}>Correct</div>
            <div style={{ color: "var(--color-success)", fontWeight: 700 }}>{item.summary.correctCount}</div>
          </div>
          <div>
            <div style={{ color: "var(--color-text-muted)", fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "0.15rem" }}>Wrong</div>
            <div style={{ color: "var(--color-text-danger)", fontWeight: 700 }}>{item.summary.wrongCount}</div>
          </div>
          <div>
            <div style={{ color: "var(--color-text-muted)", fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "0.15rem" }}>Last</div>
            <div style={{ fontSize: "0.875rem", fontWeight: 500 }}>{formatDate(item.summary.lastPracticedAt)}</div>
          </div>
          <div>
            <div style={{ color: "var(--color-text-muted)", fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "0.15rem" }}>Result</div>
            <span
              className={`badge ${resultClass}`}
              style={{
                padding: "0.2rem 0.6rem",
                borderRadius: "var(--radius-full)",
                fontSize: "0.75rem",
                fontWeight: 600,
                textTransform: "none",
                letterSpacing: "normal",
                display: "inline-block",
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
            padding: "1.25rem",
            borderTop: "1px solid var(--color-border)",
            backgroundColor: "var(--color-surface-muted)",
          }}
          data-testid={`attempts-${item.problemId}`}
        >
          {explainInfoMessage && (
            <div
              className="badge badge-warning"
              style={{
                padding: "0.5rem 0.75rem",
                fontSize: "0.875rem",
                textTransform: "none",
                letterSpacing: "normal",
                fontWeight: 500,
                display: "block",
                marginBottom: "0.75rem",
              }}
              data-testid={`explain-info-message-${item.problemId}`}
            >
              {explainInfoMessage}
            </div>
          )}
          {item.attempts.length === 0 ? (
            <div style={{ color: "var(--color-text-muted)", fontSize: "0.9rem" }}>No attempts recorded</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
              {item.attempts.map((attempt, idx) => (
                <div
                  key={idx}
                  style={{
                    padding: "1rem",
                    backgroundColor: "var(--color-surface)",
                    border: "1px solid var(--color-border)",
                    borderRadius: "var(--radius-md)",
                    boxShadow: "0 2px 4px rgba(0,0,0,0.02)"
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: "0.75rem",
                      marginBottom: "0.5rem",
                      flexWrap: "wrap",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
                      <span
                        className={`badge ${attempt.gradingStatus === "correct" ? "badge-success" : "badge-danger"}`}
                        style={{
                          padding: "0.2rem 0.6rem",
                          borderRadius: "var(--radius-full)",
                          fontSize: "0.75rem",
                          fontWeight: 600,
                          textTransform: "none",
                          letterSpacing: "normal",
                        }}
                      >
                        {attempt.gradingStatus}
                      </span>
                      {idx === 0 && renderExplainButton()}
                    </div>
                    <span style={{ color: "var(--color-text-muted)", fontSize: "0.8125rem", fontWeight: 500 }}>
                      {formatDate(attempt.createdAt)}
                    </span>
                  </div>
                  <div style={{ color: "var(--color-text)", fontWeight: 500, fontSize: "0.95rem" }}>
                    Answer: {attempt.submittedAnswer}
                  </div>
                  {attempt.feedback && (
                    <div style={{ marginTop: "0.75rem", padding: "0.75rem 1rem", backgroundColor: "var(--color-primary-bg)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-primary-border)" }}>
                      <div style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-primary-text)", marginBottom: "0.25rem" }}>Feedback:</div>
                      <div style={{ fontSize: "0.95rem", color: "var(--color-primary-text)", fontWeight: 500 }}>{attempt.feedback}</div>
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
    <main style={{ minHeight: "calc(100vh - 60px)", backgroundColor: "var(--color-bg)", color: "var(--color-text)", padding: "2rem 1.5rem" }}>
      <div style={{ maxWidth: "800px", margin: "0 auto" }}>
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
            <h1 style={{ margin: 0, fontSize: "2rem", fontWeight: 800, letterSpacing: "-0.02em" }}>Practice</h1>
            <p style={{ color: "var(--color-text-muted)", margin: "0.375rem 0 0", fontSize: "0.95rem", fontWeight: 500 }}>
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
            className="btn btn-primary"
            style={{
              padding: "0.6rem 1.25rem",
              fontSize: "0.875rem",
              fontWeight: 700,
            }}
          >
            {startPracticeMutation.isPending ? "Starting..." : "Start Practice"}
          </button>
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

        {isLoading ? (
          <div style={{ textAlign: "center", color: "var(--color-text-muted)", padding: "3rem 0", fontWeight: 600 }} data-testid="loading">Loading practice history...</div>
        ) : error ? (
          <div className="badge badge-danger" style={{ display: "block", padding: "1rem", fontSize: "0.9rem", textTransform: "none", letterSpacing: "normal", fontWeight: 500 }} data-testid="error">
            Error loading history: {(error as Error).message}
          </div>
        ) : items.length === 0 ? (
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
            data-testid="empty-state"
          >
            No practice history yet
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
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
      </div>
    </main>
  );
}
