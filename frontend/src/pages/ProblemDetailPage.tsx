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

interface CorrectAnswer {
  display: string;
  normalizedText: string;
  normalizedSet: string[];
  format: string;
}

interface Problem {
  id: string;
  problemType: string;
  text: string;
  tags: string[];
  graphDsl?: string;
  imageUrl?: string;
  correctAnswer?: CorrectAnswer;
  isDeleted: boolean;
  createdAt: string;
  updatedAt: string;
}

interface ProblemResponse {
  problem: Problem;
}

interface TrackingData {
  problemId: string;
  tracking: {
  exposureCount: number;
  correctCount: number;
  failedCount: number;
  lastTestedAt?: string;
    lastAttemptCorrect?: boolean;
  };
}

interface UpdateProblemInput {
  text?: string;
  problemType?: string;
  tags?: string[];
  graphDsl?: string;
  correctAnswer?: string;
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
      return response.tracking;
    },
    enabled: !!problemId,
  });

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
    backgroundColor: "var(--color-surface-muted)",
    color: "var(--color-text)",
    padding: "1rem",
  };

  if (isLoadingProblem) {
    return (
      <main style={pageCanvasStyle}>
        <div>Loading...</div>
      </main>
    );
  }

  if (problemError || !problem) {
    return (
      <main style={pageCanvasStyle}>
        <div style={{ color: "var(--color-text-danger)" }}>
          Error loading problem: {problemError?.message || "Problem not found"}
        </div>
        <button onClick={() => navigate("/problems")}>Back to Problems</button>
      </main>
    );
  }

  return (
    <main style={pageCanvasStyle}>
      <div style={{ marginBottom: "1rem" }}>
        <button onClick={() => navigate("/problems")}>Back to Problems</button>
      </div>

      {error && (
        <div style={{ color: "var(--color-text-danger)", marginBottom: "1rem" }} role="alert">
          {error}
        </div>
      )}

      <div
        style={{
          border: "1px solid var(--color-border)",
          borderRadius: "4px",
          padding: "1rem",
          marginBottom: "1rem",
          opacity: problem.isDeleted ? 0.5 : 1,
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: "1rem",
          }}
        >
          <div>
            <h1 style={{ margin: 0 }} title={problem.id}>
              Problem {formatProblemReference(problem.id)}
            </h1>
            <div style={{ color: "var(--color-text-muted)", fontSize: "0.875rem", marginTop: "0.25rem" }}>
              Reference: {problem.id}
            </div>
          </div>
          <div>
            {problem.isDeleted && (
              <span
                style={{
                  padding: "0.25rem 0.5rem",
                  background: "var(--color-danger-bg)",
                  borderRadius: "4px",
                }}
              >
                Deleted
              </span>
            )}
          </div>
        </div>

        {problem.imageUrl && (
          <CollapsibleImage
            src={problem.imageUrl}
            alt="Problem"
            style={{ maxWidth: "100%", maxHeight: "400px" }}
          />
        )}

        <div style={{ marginBottom: "1rem" }}>
          <label style={{ fontWeight: "bold" }}>Text:</label>
          {isEditing ? (
            <textarea
              value={editForm.text || ""}
              onChange={(e) =>
                setEditForm((prev) => ({ ...prev, text: e.target.value }))
              }
              style={{ width: "100%", minHeight: "100px", marginTop: "0.5rem" }}
            />
          ) : (
            <LatexText text={problem.text} style={{ whiteSpace: "pre-wrap" }} />
          )}
        </div>

        <div style={{ marginBottom: "1rem" }}>
          <label style={{ fontWeight: "bold" }}>Problem Type:</label>
          {isEditing ? (
            <div>
              <select
                value={editForm.problemType || ""}
                onChange={(e) =>
                  setEditForm((prev) => ({ ...prev, problemType: e.target.value }))
                }
                style={{
                  width: "100%",
                  padding: "0.375rem 0.75rem",
                  border: "1px solid var(--color-border)",
                  borderRadius: "0.25rem",
                  fontSize: "0.875rem",
                  marginTop: "0.5rem",
                  boxSizing: "border-box",
                }}
                data-testid="problem-type-input"
              >
                <option value="">Select a problem type…</option>
                <option value="single-choice">Single Choice</option>
                <option value="multi-choice">Multi Choice</option>
                <option value="fill-in-the-blank">Fill in the Blank</option>
                <option value="short-answer">Short Answer</option>
              </select>
              <div style={{ marginTop: "0.25rem", fontSize: "0.75rem", color: "var(--color-text-muted)" }}>
                The existing correct answer will be interpreted using the selected type.
              </div>
            </div>
          ) : (
            <span
              style={{
                padding: "0.25rem 0.5rem",
                background: "var(--color-surface-muted)",
                borderRadius: "4px",
                marginLeft: "0.5rem",
              }}
            >
              {problem.problemType}
            </span>
          )}
        </div>

        <div style={{ marginBottom: "1rem" }}>
          <label style={{ fontWeight: "bold" }}>Tags:</label>
          {isEditing ? (
            <TagInput
              tags={editForm.tags || []}
              onChange={(tags) => setEditForm((prev) => ({ ...prev, tags }))}
              suggestions={tagSuggestions}
              placeholder="Add a tag..."
              testId="edit-tags-input"
            />
          ) : problem.tags.length > 0 ? (
            <TagList tags={problem.tags} />
          ) : (
            <div style={{ marginTop: "0.5rem", color: "var(--color-text-muted)" }}>
              No tags
            </div>
          )}
        </div>

        {(problem.graphDsl || isEditing) && (
          <div style={{ marginBottom: "1rem" }}>
            <label style={{ fontWeight: "bold" }}>Graph DSL:</label>
            {isEditing ? (
              <textarea
                value={editForm.graphDsl || ""}
                onChange={(e) =>
                  setEditForm((prev) => ({ ...prev, graphDsl: e.target.value }))
                }
                style={{ width: "100%", minHeight: "80px", marginTop: "0.5rem" }}
              />
            ) : (
              <div style={{ marginTop: "0.5rem" }}>
                <GraphSandbox dsl={problem.graphDsl ?? ""} />
              </div>
            )}
          </div>
        )}

        {(problem.correctAnswer || isEditing) && (
          <div style={{ marginBottom: "1rem" }}>
            <label style={{ fontWeight: "bold" }}>Correct Answer:</label>
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
                  style={{ width: "100%", marginTop: "0.5rem" }}
                  data-testid="edit-answer-input"
                />
              ) : (
                <button
                  type="button"
                  onClick={() => setShowTeacherPasswordModal(true)}
                  style={{
                    padding: "0.375rem 0.75rem",
                    backgroundColor: "var(--color-info-bg)",
                    border: "1px solid var(--color-info-border)",
                    borderRadius: "0.25rem",
                    cursor: "pointer",
                    marginTop: "0.5rem",
                  }}
                  data-testid="edit-answer-button"
                >
                  Edit Answer
                </button>
              )
            ) : (
              <div style={{ marginTop: "0.5rem" }}>
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
                  style={{
                    padding: "0.375rem 0.75rem",
                    backgroundColor: showAnswer ? "var(--color-danger-bg)" : "var(--color-info-bg)",
                    border: "1px solid",
                    borderColor: showAnswer ? "var(--color-danger-border)" : "var(--color-info-border)",
                    borderRadius: "0.25rem",
                    cursor: "pointer",
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
                      padding: "0.5rem",
                      backgroundColor: "var(--color-surface-muted)",
                      border: "1px solid var(--color-border)",
                      borderRadius: "0.25rem",
                    }}
                  >
                    {problem.correctAnswer?.display}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <div style={{ display: "flex", gap: "0.5rem" }}>
          {isEditing ? (
            <>
              <button
                onClick={handleSave}
                disabled={updateMutation.isPending}
              >
                {updateMutation.isPending ? "Saving..." : "Save"}
              </button>
              <button onClick={handleCancel}>Cancel</button>
            </>
          ) : (
            <>
              <button onClick={handleEdit}>Edit</button>
              <button
                onClick={handleDelete}
                disabled={deleteMutation.isPending || problem.isDeleted}
              >
                {deleteMutation.isPending ? "Deleting..." : "Delete"}
              </button>
            </>
          )}
        </div>
      </div>

      <div
        style={{
          border: "1px solid var(--color-border)",
          borderRadius: "4px",
          padding: "1rem",
        }}
      >
        <h2>Tracking Statistics</h2>
        {isLoadingTracking ? (
          <div>Loading tracking data...</div>
        ) : tracking ? (
          <div>
            <div style={{ display: "flex", gap: "2rem", flexWrap: "wrap" }}>
              <div>
                <label style={{ fontWeight: "bold" }}>Exposures:</label>
                <div style={{ fontSize: "1.5rem" }}>{tracking.exposureCount}</div>
              </div>
              <div>
                <label style={{ fontWeight: "bold" }}>Correct:</label>
                <div style={{ fontSize: "1.5rem", color: "var(--color-success)" }}>
                  {tracking.correctCount}
                </div>
              </div>
              <div>
                <label style={{ fontWeight: "bold" }}>Failed:</label>
                <div style={{ fontSize: "1.5rem", color: "var(--color-text-danger)" }}>
                  {tracking.failedCount}
                </div>
              </div>
              {tracking.lastTestedAt && (
                <div>
                  <label style={{ fontWeight: "bold" }}>Last Tested:</label>
                  <div>{new Date(tracking.lastTestedAt).toLocaleString()}</div>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div>No tracking data available</div>
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
