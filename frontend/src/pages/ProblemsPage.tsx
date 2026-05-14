import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

interface Problem {
  id: number;
  type: string;
  text: string;
  tags: string[];
  isDeleted: boolean;
  createdAt: string;
  updatedAt: string;
}

interface ProblemsResponse {
  problems: Problem[];
  total: number;
  page: number;
  pageSize: number;
}

export function ProblemsPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [selectedTag, setSelectedTag] = useState<string>("");
  const [selectedType, setSelectedType] = useState<string>("");
  const pageSize = 20;

  const { data: problemsData, isLoading: isLoadingProblems } = useQuery({
    queryKey: ["problems", page, selectedTag, selectedType],
    queryFn: async () => {
      const params = new URLSearchParams({
        page: String(page),
        pageSize: String(pageSize),
      });
      if (selectedTag) params.append("tag", selectedTag);
      if (selectedType) params.append("type", selectedType);
      return api.get<ProblemsResponse>(`/problems?${params.toString()}`);
    },
  });

  const { data: tags = [] } = useQuery({
    queryKey: ["tags"],
    queryFn: () => api.get<string[]>("/problems/tags"),
  });

  const { data: types = [] } = useQuery({
    queryKey: ["types"],
    queryFn: () => api.get<string[]>("/problems/types"),
  });

  const problems = problemsData?.problems || [];
  const total = problemsData?.total || 0;
  const totalPages = Math.ceil(total / pageSize);

  return (
    <main style={{ padding: "1rem" }}>
      <h1>Problems</h1>

      <div style={{ marginBottom: "1rem", display: "flex", gap: "1rem" }}>
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
            value={selectedType}
            onChange={(e) => {
              setSelectedType(e.target.value);
              setPage(1);
            }}
          >
            <option value="">All Types</option>
            {types.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
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
                  <strong>ID: {problem.id}</strong>
                  <span
                    style={{
                      marginLeft: "0.5rem",
                      padding: "0.25rem 0.5rem",
                      background: "#e0e0e0",
                      borderRadius: "4px",
                      fontSize: "0.875rem",
                    }}
                  >
                    {problem.type}
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
