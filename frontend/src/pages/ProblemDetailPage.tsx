import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

interface Problem {
  id: number;
  type: string;
  text: string;
  tags: string[];
  graphDsl?: string;
  imagePath?: string;
  correctAnswer?: string;
  isDeleted: boolean;
  createdAt: string;
  updatedAt: string;
}

interface TrackingData {
  exposureCount: number;
  correctCount: number;
  failedCount: number;
  lastTestedAt?: string;
}

interface UpdateProblemInput {
  text?: string;
  tags?: string[];
  graphDsl?: string;
  correctAnswer?: string;
}

export function ProblemDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const problemId = Number(id);

  const [isEditing, setIsEditing] = useState(false);
  const [editForm, setEditForm] = useState<UpdateProblemInput>({});
  const [error, setError] = useState<string | null>(null);

  const {
    data: problem,
    isLoading: isLoadingProblem,
    error: problemError,
  } = useQuery({
    queryKey: ["problem", problemId],
    queryFn: () => api.get<Problem>(`/problems/${problemId}`),
  });

  const { data: tracking, isLoading: isLoadingTracking } = useQuery({
    queryKey: ["tracking", problemId],
    queryFn: () => api.get<TrackingData>(`/problems/${problemId}/tracking`),
  });

  const updateMutation = useMutation({
    mutationFn: (data: UpdateProblemInput) =>
      api.put<Problem>(`/problems/${problemId}`, data),
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
    mutationFn: () => api.delete<void>(`/problems/${problemId}`),
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
        tags: [...problem.tags],
        graphDsl: problem.graphDsl || "",
        correctAnswer: problem.correctAnswer || "",
      });
      setIsEditing(true);
    }
  };

  const handleSave = () => {
    updateMutation.mutate(editForm);
  };

  const handleCancel = () => {
    setIsEditing(false);
    setEditForm({});
    setError(null);
  };

  const handleDelete = () => {
    if (window.confirm("Are you sure you want to delete this problem?")) {
      deleteMutation.mutate();
    }
  };

  if (isLoadingProblem) {
    return (
      <main style={{ padding: "1rem" }}>
        <div>Loading...</div>
      </main>
    );
  }

  if (problemError || !problem) {
    return (
      <main style={{ padding: "1rem" }}>
        <div style={{ color: "red" }}>
          Error loading problem: {problemError?.message || "Problem not found"}
        </div>
        <button onClick={() => navigate("/problems")}>Back to Problems</button>
      </main>
    );
  }

  return (
    <main style={{ padding: "1rem" }}>
      <div style={{ marginBottom: "1rem" }}>
        <button onClick={() => navigate("/problems")}>Back to Problems</button>
      </div>

      {error && (
        <div style={{ color: "red", marginBottom: "1rem" }} role="alert">
          {error}
        </div>
      )}

      <div
        style={{
          border: "1px solid #ccc",
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
          <h1 style={{ margin: 0 }}>Problem #{problem.id}</h1>
          <div>
            <span
              style={{
                padding: "0.25rem 0.5rem",
                background: "#e0e0e0",
                borderRadius: "4px",
                marginRight: "0.5rem",
              }}
            >
              {problem.type}
            </span>
            {problem.isDeleted && (
              <span
                style={{
                  padding: "0.25rem 0.5rem",
                  background: "#ffcccc",
                  borderRadius: "4px",
                }}
              >
                Deleted
              </span>
            )}
          </div>
        </div>

        {problem.imagePath && (
          <div style={{ marginBottom: "1rem" }}>
            <img
              src={`/api/v1/problems/${problemId}/image`}
              alt="Problem"
              style={{ maxWidth: "100%", maxHeight: "400px" }}
            />
          </div>
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
            <div style={{ whiteSpace: "pre-wrap" }}>{problem.text}</div>
          )}
        </div>

        <div style={{ marginBottom: "1rem" }}>
          <label style={{ fontWeight: "bold" }}>Tags:</label>
          {isEditing ? (
            <input
              type="text"
              value={(editForm.tags || []).join(", ")}
              onChange={(e) =>
                setEditForm((prev) => ({
                  ...prev,
                  tags: e.target.value.split(",").map((t) => t.trim()),
                }))
              }
              placeholder="Comma-separated tags"
              style={{ width: "100%", marginTop: "0.5rem" }}
            />
          ) : problem.tags.length > 0 ? (
            <div style={{ marginTop: "0.5rem" }}>
              {problem.tags.map((tag) => (
                <span
                  key={tag}
                  style={{
                    marginRight: "0.25rem",
                    padding: "0.125rem 0.375rem",
                    background: "#f0f0f0",
                    borderRadius: "4px",
                    fontSize: "0.75rem",
                  }}
                >
                  {tag}
                </span>
              ))}
            </div>
          ) : (
            <div style={{ marginTop: "0.5rem", color: "#666" }}>
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
              <pre
                style={{
                  background: "#f5f5f5",
                  padding: "0.5rem",
                  borderRadius: "4px",
                  overflow: "auto",
                }}
              >
                {problem.graphDsl}
              </pre>
            )}
          </div>
        )}

        {(problem.correctAnswer || isEditing) && (
          <div style={{ marginBottom: "1rem" }}>
            <label style={{ fontWeight: "bold" }}>Correct Answer:</label>
            {isEditing ? (
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
              />
            ) : (
              <div>{problem.correctAnswer}</div>
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
          border: "1px solid #ccc",
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
                <div style={{ fontSize: "1.5rem", color: "green" }}>
                  {tracking.correctCount}
                </div>
              </div>
              <div>
                <label style={{ fontWeight: "bold" }}>Failed:</label>
                <div style={{ fontSize: "1.5rem", color: "red" }}>
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
    </main>
  );
}
