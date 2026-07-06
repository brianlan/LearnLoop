import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { formatProblemReference } from "@/utils/format";
import { GraphSandbox } from "@/components/GraphSandbox";
import { CollapsibleImage } from "@/components/CollapsibleImage";
import { LatexText } from "@/components/LatexText";
import { TagInput } from "@/components/TagInput";
import { TagList } from "@/components/TagPill";
import { TeacherPasswordModal } from "@/components/TeacherPasswordModal";
import { useTagSuggestions } from "@/hooks/useTagSuggestions";
import type { ProblemDetail, ProblemResponse, PracticeWeight } from "@/types/problem";
import { PROBLEM_TYPE_OPTIONS } from "@/constants/problemTypes";

interface TrackingData {
  problemId: string;
  tracking: {
    exposureCount: number;
    correctCount: number;
    failedCount: number;
    lastTestedAt?: string;
    lastAttemptCorrect?: boolean;
  };
  practiceWeight?: PracticeWeight;
}

interface UpdateProblemInput {
  text?: string;
  problemType?: string;
  tags?: string[];
  graphDsl?: string;
  correctAnswer?: string;
}

function WeightBreakdown({ weight }: { weight: PracticeWeight }) {
  const [showDetails, setShowDetails] = useState(false);

  const lastWrongDisplay = weight.lastWrong.toFixed(2);
  const failureDisplay = weight.failure.toFixed(2);
  const recencyDisplay = weight.recency.toFixed(2);
  const derivedTotal = (
    parseFloat(lastWrongDisplay) +
    parseFloat(failureDisplay) +
    parseFloat(recencyDisplay)
  ).toFixed(2);

  return (
    <div
      style={{ position: "relative" }}
      onMouseEnter={() => setShowDetails(true)}
      onMouseLeave={() => setShowDetails(false)}
      onFocus={() => setShowDetails(true)}
      onBlur={() => setShowDetails(false)}
      tabIndex={0}
      role="button"
      aria-label={`Practice weight: ${derivedTotal}. Focus for breakdown.`}
      data-testid="practice-weight"
    >
      <div>
        <label style={{ fontWeight: "bold" }}>Practice Weight:</label>
        <div style={{ fontSize: "1.5rem" }}>{derivedTotal}</div>
      </div>
      {showDetails && (
        <div
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            zIndex: 10,
            background: "var(--color-surface)",
            border: "1px solid var(--color-border)",
            borderRadius: "4px",
            padding: "0.5rem 0.75rem",
            marginTop: "0.25rem",
            minWidth: "180px",
            boxShadow: "0 2px 8px rgba(0,0,0,0.15)",
          }}
          data-testid="practice-weight-breakdown"
        >
          <div style={{ fontWeight: "bold", marginBottom: "0.25rem" }}>Components</div>
          <div>last_wrong: {lastWrongDisplay}</div>
          <div>failure: {failureDisplay}</div>
          <div>recency: {recencyDisplay}</div>
          <div style={{ marginTop: "0.25rem", fontWeight: "bold" }}>
            total: {derivedTotal}
          </div>
        </div>
      )}
    </div>
  );
}

export function ProblemDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const problemId = id ?? "";

  const [isEditing, setIsEditing] = useState(false);
  const [showAnswer, setShowAnswer] = useState(false);
  const [showTeacherPasswordModal, setShowTeacherPasswordModal] = useState(false);
  const [showAnswerEdit, setShowAnswerEdit] = useState(false);
  const [editForm, setEditForm] = useState<UpdateProblemInput>({});
  const [error, setError] = useState<string | null>(null);
  const tagSuggestions = useTagSuggestions();

  const {
    data: problem,
    isLoading: isLoadingProblem,
    error: problemError,
  } = useQuery({
    queryKey: ["problem", problemId],
    queryFn: async () => {
      const data = await api.get<ProblemResponse>(`/problems/${problemId}`);
      return data.problem;
    },
    enabled: !!problemId,
  });

  const { data: tracking, isLoading: isLoadingTracking } = useQuery({
    queryKey: ["tracking", problemId],
    queryFn: async () => {
      const response = await api.get<TrackingData>(`/problems/${problemId}/tracking`);
      return { ...response.tracking, practiceWeight: response.practiceWeight };
    },
    enabled: !!problemId,
  });

  const { data: solutionStatusData } = useQuery({
    queryKey: ["solution-status", problemId],
    queryFn: () => api.getSolutionStatus(problemId),
    enabled: !!problemId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return (status === "pending" || status === "generating") ? 2000 : false;
    },
  });

  const solutionStatus = solutionStatusData?.status;

  const solutionStatusLabel: Record<string, string> = {
    ready: "Solution Generated",
    pending: "Solution Pending",
    generating: "Solution Generating",
    failed: "Solution Failed",
    none: "Solution Not Started",
  };

  const updateMutation = useMutation({
    mutationFn: (data: UpdateProblemInput) =>
      api.patch<ProblemResponse>(`/problems/${problemId}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["problem", problemId] });
      setIsEditing(false);
      setError(null);
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : "Update failed");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.delete<{ ok: true }>(`/problems/${problemId}`),
    onSuccess: () => {
      navigate("/problems");
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : "Delete failed");
    },
  });

  const regenerateMutation = useMutation({
    mutationFn: () => api.regenerateSolution(problemId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["solution-status", problemId] });
      setError(null);
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : "Regeneration failed");
    },
  });

  const handleEdit = () => {
    if (problem) {
        setEditForm({
          text: problem.text,
          problemType: problem.problemType,
          tags: [...problem.tags],
          graphDsl: problem.graphDsl || "",
        });
        setShowAnswerEdit(false);
        setIsEditing(true);
    }
  };

  const handleSave = () => {
    updateMutation.mutate(editForm);
  };

  const handleCancel = () => {
    setIsEditing(false);
    setShowAnswerEdit(false);
    setEditForm({});
    setError(null);
  };

  const handleDelete = () => {
    if (window.confirm("Are you sure you want to delete this problem?")) {
      deleteMutation.mutate();
    }
  };

  const pageCanvasStyle: React.CSSProperties = {
    minHeight: "calc(100vh - 60px)",
    backgroundColor: "var(--color-bg)",
    color: "var(--color-text)",
    padding: "2rem",
  };

  if (isLoadingProblem) {
    return (
      <main style={pageCanvasStyle}>
        <div style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "200px", color: "var(--color-text-muted)", fontWeight: 600 }}>Loading...</div>
      </main>
    );
  }

  if (problemError || !problem) {
    return (
      <main style={pageCanvasStyle}>
        <div 
          className="badge badge-danger"
          style={{ 
            display: "block",
            padding: "1rem",
            marginBottom: "1rem",
            fontSize: "0.9rem",
            textTransform: "none",
            fontWeight: 500,
            letterSpacing: "normal"
          }}
        >
          Error loading problem: {problemError?.message || "Problem not found"}
        </div>
        <button onClick={() => navigate("/problems")} className="btn btn-secondary">Back to Problems</button>
      </main>
    );
  }

  return (
    <main style={pageCanvasStyle}>
      <div style={{ marginBottom: "1.5rem" }}>
        <button onClick={() => navigate("/problems")} className="btn btn-secondary" style={{ padding: "0.5rem 1rem", fontSize: "0.875rem" }}>
          Back to Problems
        </button>
      </div>

      {error && (
        <div 
          className="badge badge-danger" 
          style={{ 
            display: "block",
            padding: "1rem",
            marginBottom: "1.5rem",
            fontSize: "0.9rem",
            textTransform: "none",
            fontWeight: 500,
            letterSpacing: "normal"
          }}
          role="alert"
        >
          {error}
        </div>
      )}

      <div
        className="card-premium"
        style={{
          marginBottom: "1.5rem",
          opacity: problem.isDeleted ? 0.5 : 1,
          display: "flex",
          flexDirection: "column",
          gap: "1.5rem"
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "0.5rem",
            flexWrap: "wrap",
            gap: "1rem"
          }}
        >
          <div>
            <h1 style={{ margin: 0, fontSize: "1.5rem", fontWeight: 800, letterSpacing: "-0.02em" }} title={problem.id}>
              Problem {formatProblemReference(problem.id)}
            </h1>
            <div style={{ color: "var(--color-text-muted)", fontSize: "0.825rem", marginTop: "0.25rem", fontWeight: 500 }}>
              Reference: {problem.id}
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            {problem.isDeleted && (
              <span
                className="badge badge-danger"
                style={{
                  fontSize: "0.75rem",
                  padding: "0.15rem 0.5rem",
                  borderRadius: "var(--radius-sm)",
                  fontWeight: 600,
                  textTransform: "none",
                  letterSpacing: "normal"
                }}
              >
                Deleted
              </span>
            )}
            {solutionStatus && (
              <span
                data-testid="solution-status"
                className={`badge ${
                  solutionStatus === "ready"
                    ? "badge-success"
                    : solutionStatus === "failed"
                      ? "badge-danger"
                      : "badge-warning"
                }`}
                style={{
                  fontSize: "0.75rem",
                  padding: "0.15rem 0.5rem",
                  borderRadius: "var(--radius-sm)",
                  fontWeight: 600,
                  textTransform: "none",
                  letterSpacing: "normal"
                }}
              >
                {solutionStatusLabel[solutionStatus] ?? solutionStatus}
              </span>
            )}
            {solutionStatus === "ready" && (
              <button
                type="button"
                onClick={() => navigate(`/coaching/${problemId}`, { state: { from: `/problems/${problemId}` } })}
                data-testid="ai-explain-button"
                className="btn btn-primary"
                style={{
                  padding: "0.4rem 0.75rem",
                  borderRadius: "var(--radius-md)",
                  fontSize: "0.8125rem",
                  fontWeight: 700,
                }}
              >
                AI Explain
              </button>
            )}
            {(solutionStatus === "failed" || solutionStatus === "ready") && (
              <button
                type="button"
                onClick={() => regenerateMutation.mutate()}
                disabled={regenerateMutation.isPending}
                data-testid="regenerate-solution-button"
                className="btn btn-secondary"
                style={{
                  padding: "0.4rem 0.75rem",
                  borderRadius: "var(--radius-md)",
                  fontSize: "0.8125rem",
                  fontWeight: 700,
                }}
              >
                {regenerateMutation.isPending ? "Regenerating..." : "Re-generate solution"}
              </button>
            )}
          </div>
        </div>

        {problem.imageUrl && (
          <div style={{ display: "flex", justifyContent: "center", backgroundColor: "var(--color-surface-muted)", padding: "1rem", borderRadius: "var(--radius-lg)", border: "1px solid var(--color-border)" }}>
            <CollapsibleImage
              src={problem.imageUrl}
              alt="Problem"
              style={{ maxWidth: "100%", maxHeight: "400px", borderRadius: "var(--radius-md)" }}
            />
          </div>
        )}

        <div>
          <label style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", display: "block", marginBottom: "0.5rem" }}>Text</label>
          {isEditing ? (
            <textarea
              value={editForm.text || ""}
              onChange={(e) =>
                setEditForm((prev) => ({ ...prev, text: e.target.value }))
              }
              style={{ width: "100%", minHeight: "120px", marginTop: "0.25rem", fontSize: "0.95rem" }}
            />
          ) : (
            <div style={{ padding: "0.5rem 0", fontSize: "1.05rem", lineHeight: "1.5" }}>
              <LatexText text={problem.text} style={{ whiteSpace: "pre-wrap" }} />
            </div>
          )}
        </div>

        <div>
          <label style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", display: "block", marginBottom: "0.5rem" }}>Problem Type</label>
          {isEditing ? (
            <div>
              <select
                value={editForm.problemType || ""}
                onChange={(e) =>
                  setEditForm((prev) => ({ ...prev, problemType: e.target.value }))
                }
                style={{
                  width: "100%",
                  padding: "0.5rem 0.75rem",
                  border: "1px solid var(--color-border)",
                  borderRadius: "var(--radius-md)",
                  fontSize: "0.875rem",
                  marginTop: "0.25rem",
                  boxSizing: "border-box",
                  backgroundColor: "var(--color-bg)"
                }}
                data-testid="problem-type-input"
              >
                <option value="">Select a problem type…</option>
                {PROBLEM_TYPE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
              <div style={{ marginTop: "0.375rem", fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                The existing correct answer will be interpreted using the selected type.
              </div>
            </div>
          ) : (
            <span
              className="badge badge-muted"
              style={{
                padding: "0.2rem 0.6rem",
                borderRadius: "var(--radius-full)",
                fontSize: "0.75rem",
                fontWeight: 600,
                textTransform: "none",
                letterSpacing: "normal",
                marginTop: "0.25rem"
              }}
            >
              {problem.problemType}
            </span>
          )}
        </div>

        <div>
          <label style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", display: "block", marginBottom: "0.5rem" }}>Tags</label>
          {isEditing ? (
            <div style={{ marginTop: "0.25rem" }}>
              <TagInput
                tags={editForm.tags || []}
                onChange={(tags) => setEditForm((prev) => ({ ...prev, tags }))}
                suggestions={tagSuggestions}
                placeholder="Add a tag..."
                testId="edit-tags-input"
              />
            </div>
          ) : problem.tags.length > 0 ? (
            <TagList tags={problem.tags} />
          ) : (
            <div style={{ marginTop: "0.25rem", color: "var(--color-text-muted)", fontSize: "0.875rem" }}>
              No tags
            </div>
          )}
        </div>

        {(problem.graphDsl || isEditing) && (
          <div>
            <label style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", display: "block", marginBottom: "0.5rem" }}>Graph DSL</label>
            {isEditing ? (
              <textarea
                value={editForm.graphDsl || ""}
                onChange={(e) =>
                  setEditForm((prev) => ({ ...prev, graphDsl: e.target.value }))
                }
                style={{ width: "100%", minHeight: "100px", marginTop: "0.25rem", fontFamily: "monospace", fontSize: "0.875rem" }}
              />
            ) : (
              <div style={{ marginTop: "0.5rem" }}>
                <GraphSandbox dsl={problem.graphDsl ?? ""} height={300} />
              </div>
            )}
          </div>
        )}

        {(problem.correctAnswer || isEditing) && (
          <div>
            <label style={{ fontSize: "0.75rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", display: "block", marginBottom: "0.5rem" }}>Correct Answer</label>
            {isEditing ? (
              showAnswerEdit ? (
                <input
                  type="text"
                  value={editForm.correctAnswer || ""}
                  onChange={(e) =>
                    setEditForm((prev) => ({
                      ...prev,
                      correctAnswer: e.target.value,
                    }))
                  }
                  style={{ width: "100%", marginTop: "0.25rem", padding: "0.5rem 0.75rem", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border)", backgroundColor: "var(--color-bg)" }}
                  data-testid="edit-answer-input"
                />
              ) : (
                <button
                  type="button"
                  onClick={() => setShowTeacherPasswordModal(true)}
                  className="btn btn-secondary"
                  style={{
                    padding: "0.4rem 0.875rem",
                    fontSize: "0.8125rem",
                    borderRadius: "var(--radius-md)",
                    marginTop: "0.25rem",
                  }}
                  data-testid="edit-answer-button"
                >
                  Edit Answer
                </button>
              )
            ) : (
              <div style={{ marginTop: "0.25rem" }}>
                <button
                  type="button"
                  onClick={() => {
                    if (showAnswer) {
                      setShowAnswer(false);
                    } else {
                      setShowTeacherPasswordModal(true);
                    }
                  }}
                  aria-expanded={showAnswer}
                  aria-controls="answer-container"
                  className="btn btn-secondary"
                  style={{
                    padding: "0.4rem 0.875rem",
                    fontSize: "0.8125rem",
                    borderRadius: "var(--radius-md)",
                    marginBottom: showAnswer ? "0.5rem" : 0,
                  }}
                >
                  {showAnswer ? "Hide Answer" : "Show Answer"}
                </button>
                {showAnswer && (
                  <div
                    id="answer-container"
                    role="region"
                    aria-live="polite"
                    style={{
                      padding: "0.75rem 1rem",
                      backgroundColor: "var(--color-surface-muted)",
                      border: "1px solid var(--color-border)",
                      borderRadius: "var(--radius-md)",
                      fontSize: "0.95rem",
                      fontWeight: 600,
                      color: "var(--color-primary-text)"
                    }}
                  >
                    {problem.correctAnswer?.display}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <div style={{ display: "flex", gap: "0.75rem", borderTop: "1px solid var(--color-border)", paddingTop: "1.25rem", marginTop: "0.5rem" }}>
          {isEditing ? (
            <>
              <button
                onClick={handleSave}
                disabled={updateMutation.isPending}
                className="btn btn-primary"
                style={{ padding: "0.5rem 1.25rem", borderRadius: "var(--radius-md)", fontSize: "0.875rem" }}
              >
                {updateMutation.isPending ? "Saving..." : "Save"}
              </button>
              <button onClick={handleCancel} className="btn btn-secondary" style={{ padding: "0.5rem 1.25rem", borderRadius: "var(--radius-md)", fontSize: "0.875rem" }}>Cancel</button>
            </>
          ) : (
            <>
              <button onClick={handleEdit} className="btn btn-secondary" style={{ padding: "0.5rem 1.25rem", borderRadius: "var(--radius-md)", fontSize: "0.875rem" }}>Edit</button>
              <button
                onClick={handleDelete}
                disabled={deleteMutation.isPending || problem.isDeleted}
                className="btn btn-danger"
                style={{ padding: "0.5rem 1.25rem", borderRadius: "var(--radius-md)", fontSize: "0.875rem" }}
              >
                {deleteMutation.isPending ? "Deleting..." : "Delete"}
              </button>
            </>
          )}
        </div>
      </div>

      <div
        className="card-premium"
        style={{
          padding: "1.5rem",
          display: "flex",
          flexDirection: "column",
          gap: "1rem"
        }}
      >
        <h2 style={{ fontSize: "1.25rem", fontWeight: 800, margin: 0 }}>Tracking Statistics</h2>
        {isLoadingTracking ? (
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.875rem" }}>Loading tracking data...</div>
        ) : tracking ? (
          <div>
            <div style={{ display: "flex", gap: "2.5rem", flexWrap: "wrap", alignItems: "flex-start" }}>
              <div>
                <label style={{ fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", display: "block", marginBottom: "0.25rem" }}>Exposures</label>
                <div style={{ fontSize: "1.75rem", fontWeight: 800 }}>{tracking.exposureCount}</div>
              </div>
              <div>
                <label style={{ fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", display: "block", marginBottom: "0.25rem" }}>Correct</label>
                <div style={{ fontSize: "1.75rem", fontWeight: 800, color: "var(--color-success)" }}>
                  {tracking.correctCount}
                </div>
              </div>
              <div>
                <label style={{ fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", display: "block", marginBottom: "0.25rem" }}>Failed</label>
                <div style={{ fontSize: "1.75rem", fontWeight: 800, color: "var(--color-text-danger)" }}>
                  {tracking.failedCount}
                </div>
              </div>
              {tracking.lastTestedAt && (
                <div>
                  <label style={{ fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", display: "block", marginBottom: "0.25rem" }}>Last Tested</label>
                  <div style={{ fontSize: "0.95rem", fontWeight: 600, padding: "0.25rem 0" }}>{new Date(tracking.lastTestedAt).toLocaleString()}</div>
                </div>
              )}
              {tracking.practiceWeight && (
                <div style={{ borderLeft: "1px solid var(--color-border)", paddingLeft: "1.5rem" }}>
                  <WeightBreakdown weight={tracking.practiceWeight} />
                </div>
              )}
            </div>
          </div>
        ) : (
          <div style={{ color: "var(--color-text-muted)", fontSize: "0.875rem" }}>No tracking data available</div>
        )}
      </div>

      <TeacherPasswordModal
        isOpen={showTeacherPasswordModal}
        onClose={() => setShowTeacherPasswordModal(false)}
        onVerified={() => {
          if (isEditing) {
            setShowAnswerEdit(true);
            setEditForm((prev) => ({
              ...prev,
              correctAnswer: problem?.correctAnswer?.display || "",
            }));
          } else {
            setShowAnswer(true);
          }
          setShowTeacherPasswordModal(false);
        }}
      />
    </main>
  );
}
