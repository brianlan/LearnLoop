import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

interface Problem {
  id: string;
  problemType: string;
  text: string;
  tags: string[];
  imageUrl?: string;
  tracking: {
    exposureCount: number;
    correctCount: number;
    failedCount: number;
    lastTestedAt?: string;
    lastAttemptCorrect?: boolean;
  };
  isDeleted: boolean;
  createdAt: string;
  updatedAt: string;
}

interface ProblemsResponse {
  items: Problem[];
  total: number;
  page: number;
  pageSize: number;
}

import type { TagsResponse } from "@/types/tag";

const PROBLEM_TYPE_OPTIONS = [
  { value: "", label: "All Types" },
  { value: "single-choice", label: "Single Choice" },
  { value: "multi-choice", label: "Multi Choice" },
  { value: "fill-in-the-blank", label: "Fill in the Blank" },
  { value: "short-answer", label: "Short Answer" },
];

function formatProblemReference(problemId: string): string {
  return problemId.length > 8 ? problemId.slice(0, 8) : problemId;
}

export function ProblemsPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [selectedTag, setSelectedTag] = useState<string>("");
  const [selectedProblemType, setSelectedProblemType] = useState<string>("");
  const pageSize = 20;

  const { data: problemsData, isLoading: isLoadingProblems } = useQuery({
    queryKey: ["problems", page, selectedTag, selectedProblemType],
    queryFn: async () => {
      const params = new URLSearchParams({
        page: String(page),
        pageSize: String(pageSize),
      });
      if (selectedTag) params.append("tag", selectedTag);
      if (selectedProblemType) params.append("type", selectedProblemType);
      return api.get<ProblemsResponse>(`/problems?${params.toString()}`);
    },
  });

  const { data: tags = [] } = useQuery({
    queryKey: ["tags"],
    queryFn: async () => {
      const response = await api.get<TagsResponse>("/tags");
      return response.items.map((item) => item.name);
    },
  });

  const problems = problemsData?.items || [];
  const total = problemsData?.total || 0;
  const totalPages = Math.ceil(total / pageSize);

  return (
    <main style={{ padding: "1rem" }}>
      <h1>Problems</h1>

      <div
        style={{
          marginBottom: "1rem",
          display: "flex",
          gap: "1rem",
          alignItems: "end",
          flexWrap: "wrap",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
        <div>
          <label htmlFor="tag-filter">Filter by Tag: </label>
          <select
            id="tag-filter"
            value={selectedTag}
            onChange={(e) => {
              setSelectedTag(e.target.value);
              setPage(1);
            }}
          >
            <option value="">All Tags</option>
            {tags.map((tag) => (
              <option key={tag} value={tag}>
                {tag}
              </option>
            ))}
          </select>
        </div>
          <div>
            <label htmlFor="type-filter">Filter by Type: </label>
            <select
              id="type-filter"
              value={selectedProblemType}
              onChange={(e) => {
                setSelectedProblemType(e.target.value);
                setPage(1);
              }}
            >
              {PROBLEM_TYPE_OPTIONS.map((option) => (
                <option key={option.value || "all"} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div style={{ color: "#4b5563", fontSize: "0.875rem" }}>
          {total === 0
            ? "No problems found"
            : `Showing ${problems.length} of ${total} problem${total === 1 ? "" : "s"}`}
        </div>
      </div>

      {isLoadingProblems ? (
        <div>Loading...</div>
      ) : (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
              gap: "1rem",
            }}
          >
            {problems.map((problem) => (
              <div
                key={problem.id}
                onClick={() => navigate(`/problems/${problem.id}`)}
                style={{
                  border: "1px solid #ccc",
                  borderRadius: "4px",
                  padding: "1rem",
                  cursor: "pointer",
                  opacity: problem.isDeleted ? 0.5 : 1,
                }}
              >
                <div style={{ marginBottom: "0.5rem" }}>
                  <strong title={problem.id}>Problem {formatProblemReference(problem.id)}</strong>
                  <span
                    style={{
                      marginLeft: "0.5rem",
                      padding: "0.25rem 0.5rem",
                      background: "#e0e0e0",
                      borderRadius: "4px",
                      fontSize: "0.875rem",
                    }}
                  >
                    {problem.problemType}
                  </span>
                  {problem.isDeleted && (
                    <span
                      style={{
                        marginLeft: "0.5rem",
                        padding: "0.25rem 0.5rem",
                        background: "#ffcccc",
                        borderRadius: "4px",
                        fontSize: "0.875rem",
                      }}
                    >
                      Deleted
                    </span>
                  )}
                </div>
                <div
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {problem.text}
                </div>
                {problem.tags.length > 0 && (
                  <div
                    style={{
                      marginTop: "0.5rem",
                      display: "flex",
                      flexWrap: "wrap",
                      gap: "0.375rem",
                    }}
                  >
                    {problem.tags.map((tag) => (
                      <span
                        key={tag}
                        style={{
                          padding: "0.125rem 0.375rem",
                          background: "#f0f0f0",
                          borderRadius: "4px",
                          fontSize: "0.75rem",
                          display: "inline-flex",
                        }}
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>

          {totalPages > 1 && (
            <div
              style={{
                marginTop: "1rem",
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                gap: "0.5rem",
              }}
            >
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
              >
                Previous
              </button>
              <span>
                Page {page} of {totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
              >
                Next
              </button>
            </div>
          )}

          {problems.length === 0 && (
            <div style={{ textAlign: "center", marginTop: "2rem" }}>
              No problems found
            </div>
          )}
        </>
      )}
    </main>
  );
}
