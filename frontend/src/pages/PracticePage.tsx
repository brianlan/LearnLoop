import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { PracticeHistoryItem, PracticeNextResponse } from "@/types/practice";

interface PracticeHistoryResponse {
  items: PracticeHistoryItem[];
}

function formatDate(dateString?: string) {
  if (!dateString) return "—";
  return new Date(dateString).toLocaleString();
}

function getResultStyle(result: string) {
  switch (result) {
    case "correct":
      return { backgroundColor: "#dcfce7", color: "#166534" };
    case "incorrect":
      return { backgroundColor: "#fee2e2", color: "#991b1b" };
    default:
      return { backgroundColor: "#fef3c7", color: "#92400e" };
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
  const resultStyle = getResultStyle(item.summary.lastResult || "");

  return (
    <div
      style={{
        border: "1px solid #e5e7eb",
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
          textAlign: "left",
          backgroundColor: isExpanded ? "#f9fafb" : "white",
          border: "none",
          padding: "1rem",
          cursor: "pointer",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            gap: "1rem",
            flexWrap: "wrap",
          }}
        >
          <div style={{ flex: "1 1 200px", minWidth: 0 }}>
            <div
              style={{
                fontWeight: 600,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {item.problemText}
            </div>
            <div style={{ color: "#6b7280", fontSize: "0.875rem" }}>
              {item.problemType}
            </div>
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(80px, 1fr))",
              gap: "0.75rem",
              flex: "1 1 auto",
              minWidth: 0,
            }}
          >
            <div>
              <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>Total</div>
              <div>{item.summary.totalAttempts}</div>
            </div>
            <div>
              <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>Correct</div>
              <div style={{ color: "#16a34a" }}>{item.summary.correctCount}</div>
            </div>
            <div>
              <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>Wrong</div>
              <div style={{ color: "#dc2626" }}>{item.summary.wrongCount}</div>
            </div>
            <div>
              <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>Last</div>
              <div>{formatDate(item.summary.lastPracticedAt)}</div>
            </div>
            <div>
              <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>Result</div>
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
        </div>
      </button>
      {isExpanded && (
        <div
          style={{
            padding: "1rem",
            borderTop: "1px solid #e5e7eb",
            backgroundColor: "#f9fafb",
          }}
          data-testid={`attempts-${item.problemId}`}
        >
          {item.attempts.length === 0 ? (
            <div style={{ color: "#6b7280" }}>No attempts recorded</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
              {item.attempts.map((attempt, idx) => (
                <div
                  key={idx}
                  style={{
                    padding: "0.75rem",
                    backgroundColor: "white",
                    border: "1px solid #e5e7eb",
                    borderRadius: "0.375rem",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      marginBottom: "0.25rem",
                    }}
                  >
                    <span style={{ fontWeight: 500 }}>{attempt.gradingStatus}</span>
                    <span style={{ color: "#6b7280", fontSize: "0.875rem" }}>
                      {formatDate(attempt.createdAt)}
                    </span>
                  </div>
                  <div style={{ color: "#374151" }}>
                    Answer: {attempt.submittedAnswer}
                  </div>
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

  const startPracticeMutation = useMutation({
    mutationFn: () => api.post<PracticeNextResponse>("/practice/next", {}),
    onSuccess: (response) => {
      if (response.status === "ok" && response.problem) {
        navigate("/practice/active");
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
          <p style={{ color: "#6b7280", margin: "0.5rem 0 0" }}>
            Review your practice history or start a new session.
          </p>
        </div>
        <button
          type="button"
          onClick={handleStartPractice}
          disabled={startPracticeMutation.isPending}
          data-testid="start-practice-button"
          style={{
            padding: "0.75rem 1rem",
            backgroundColor: startPracticeMutation.isPending ? "#93c5fd" : "#2563eb",
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

      {isLoading ? (
        <div data-testid="loading">Loading practice history...</div>
      ) : error ? (
        <div style={{ color: "#dc2626" }} data-testid="error">
          Error loading history: {(error as Error).message}
        </div>
      ) : items.length === 0 ? (
        <div
          style={{
            padding: "2rem",
            textAlign: "center",
            backgroundColor: "#f9fafb",
            border: "1px solid #e5e7eb",
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