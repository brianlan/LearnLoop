import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Modal } from "@/components/Modal";
import Pagination from "@/components/Pagination";
import { getSelectionCandidates } from "@/api/exams";
import type {
  CreateExamRequest,
  SelectionCandidate,
  SelectionCandidateSortBy,
  SelectionCandidateSortOrder,
} from "@/types/exam";

const EXAM_PROBLEM_COUNT_MIN = 1;
const EXAM_PROBLEM_COUNT_MAX = 30;
const EXAM_PROBLEM_COUNT_DEFAULT = 5;
const PICKER_PAGE_SIZE = 10;

const SORT_COLUMNS: Array<{ key: SelectionCandidateSortBy; label: string }> = [
  { key: "selectionScore", label: "Score" },
  { key: "addDate", label: "Added" },
  { key: "successCount", label: "Success" },
  { key: "failureCount", label: "Failure" },
];

interface CreateExamModalProps {
  isOpen: boolean;
  isCreating: boolean;
  onClose: () => void;
  onCreate: (request: CreateExamRequest) => void;
  /** Ids reported by a 422 INELIGIBLE_PROBLEMS rejection, marked in the picker (#682). */
  staleProblemIds?: string[];
  ineligibleMessage?: string | null;
}

function sortValue(row: SelectionCandidate, sortBy: SelectionCandidateSortBy): string | number {
  // Native timestamp value: the API emits mixed whole/fractional-second ISO
  // forms, so raw strings sort by spelling instead of chronology (#682).
  if (sortBy === "addDate") return Date.parse(row.createdAt);
  if (sortBy === "failureCount") return row.failedCount;
  return row[sortBy];
}

export function CreateExamModal({
  isOpen,
  isCreating,
  onClose,
  onCreate,
  staleProblemIds = [],
  ineligibleMessage = null,
}: CreateExamModalProps) {
  const [mode, setMode] = useState<"random" | "manual">("random");
  const [problemCountInput, setProblemCountInput] = useState(String(EXAM_PROBLEM_COUNT_DEFAULT));
  const [keyword, setKeyword] = useState("");
  const [sortBy, setSortBy] = useState<SelectionCandidateSortBy>("selectionScore");
  const [sortOrder, setSortOrder] = useState<SelectionCandidateSortOrder>("desc");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Map<string, SelectionCandidate>>(new Map());

  useEffect(() => {
    if (isOpen) {
      setMode("random");
      setProblemCountInput(String(EXAM_PROBLEM_COUNT_DEFAULT));
      setKeyword("");
      setSortBy("selectionScore");
      setSortOrder("desc");
      setPage(1);
      setSelected(new Map());
    }
  }, [isOpen]);

  const candidatesQuery = useQuery({
    queryKey: ["selection-candidates", keyword, sortBy, sortOrder, page],
    queryFn: () =>
      getSelectionCandidates({
        q: keyword,
        sortBy,
        sortOrder,
        page,
        pageSize: PICKER_PAGE_SIZE,
      }),
    enabled: isOpen && mode === "manual",
  });

  const parsedProblemCount = /^\d+$/.test(problemCountInput) ? Number(problemCountInput) : null;
  const validProblemCount =
    parsedProblemCount !== null &&
    parsedProblemCount >= EXAM_PROBLEM_COUNT_MIN &&
    parsedProblemCount <= EXAM_PROBLEM_COUNT_MAX
      ? parsedProblemCount
      : null;
  const problemCountError =
    mode === "random" && validProblemCount === null
      ? `Enter a whole number from ${EXAM_PROBLEM_COUNT_MIN} to ${EXAM_PROBLEM_COUNT_MAX}.`
      : null;

  const staleSet = useMemo(() => new Set(staleProblemIds), [staleProblemIds]);

  // Drop rejected ids from the selection so a retry cannot resubmit them
  // invisibly after the eligible-only refetch removes their rows (#682).
  useEffect(() => {
    if (staleProblemIds.length === 0) return;
    setSelected((previous) => {
      const next = new Map(previous);
      for (const id of staleProblemIds) next.delete(id);
      return next;
    });
  }, [staleProblemIds]);

  const handleSortClick = (column: SelectionCandidateSortBy) => {
    if (sortBy === column) {
      setSortOrder(sortOrder === "desc" ? "asc" : "desc");
    } else {
      setSortBy(column);
      setSortOrder("desc");
    }
    setPage(1);
  };

  const toggleRow = (row: SelectionCandidate) => {
    setSelected((previous) => {
      const next = new Map(previous);
      if (next.has(row.id)) {
        next.delete(row.id);
      } else if (next.size < EXAM_PROBLEM_COUNT_MAX) {
        next.set(row.id, row);
      }
      return next;
    });
  };

  const handleCreate = () => {
    if (isCreating) return;
    if (mode === "random") {
      if (validProblemCount === null) return;
      onCreate({ mode: "random", maxProblemCount: validProblemCount });
      return;
    }
    if (selected.size === 0) return;
    // Item order = displayed order at confirm. Direction applies to the
    // primary column only; tied ids stay ascending in both directions,
    // mirroring the backend's stable two-pass sort (#682).
    const ordered = [...selected.values()].sort((a, b) => {
      const av = sortValue(a, sortBy);
      const bv = sortValue(b, sortBy);
      if (av !== bv) {
        const compared = av < bv ? -1 : 1;
        return sortOrder === "desc" ? -compared : compared;
      }
      return a.id < b.id ? -1 : 1;
    });
    onCreate({ mode: "manual", problemIds: ordered.map((row) => row.id) });
  };

  const handleClose = () => {
    if (!isCreating) {
      onClose();
    }
  };

  const items = candidatesQuery.data?.items ?? [];
  const total = candidatesQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PICKER_PAGE_SIZE));
  const capReached = selected.size >= EXAM_PROBLEM_COUNT_MAX;

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      ariaLabelledby="create-exam-title"
      // ponytail: bounded card with vertical scroll; shared Modal overlay stays untouched
      cardStyle={{ maxWidth: "640px", maxHeight: "calc(100vh - 2rem)", overflowY: "auto" }}
    >
      <div style={{ padding: "0.5rem" }}>
        <h2 id="create-exam-title" style={{ marginTop: 0, fontWeight: 800, fontSize: "1.35rem", letterSpacing: "-0.01em" }}>Start New Exam</h2>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            handleCreate();
          }}
          style={{ display: "flex", flexDirection: "column", gap: "1.25rem", marginTop: "1rem" }}
        >
          <fieldset
            style={{ border: "none", margin: 0, padding: 0, display: "flex", gap: "1.5rem" }}
          >
            <legend
              style={{
                fontSize: "0.75rem",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.05em",
                color: "var(--color-text-muted)",
                padding: 0,
              }}
            >
              Mode
            </legend>
            <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontWeight: 600, fontSize: "0.9rem" }}>
              <input
                type="radio"
                name="exam-mode"
                value="random"
                checked={mode === "random"}
                onChange={() => setMode("random")}
              />
              Random
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontWeight: 600, fontSize: "0.9rem" }}>
              <input
                type="radio"
                name="exam-mode"
                value="manual"
                checked={mode === "manual"}
                onChange={() => setMode("manual")}
              />
              Manual
            </label>
          </fieldset>

          {mode === "random" ? (
            <>
              <p style={{ color: "var(--color-text-muted)", margin: 0, fontSize: "0.9rem", fontWeight: 500 }}>
                Choose how many problems to include.
              </p>
              <div>
                <label
                  htmlFor="exam-problem-count"
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
                  Problem count
                </label>
                <input
                  id="exam-problem-count"
                  type="number"
                  min={EXAM_PROBLEM_COUNT_MIN}
                  max={EXAM_PROBLEM_COUNT_MAX}
                  step={1}
                  value={problemCountInput}
                  onChange={(event) => setProblemCountInput(event.target.value)}
                  aria-invalid={problemCountError ? "true" : "false"}
                  aria-describedby={problemCountError ? "exam-problem-count-error" : undefined}
                  style={{
                    width: "100%",
                    padding: "0.6rem 0.8rem",
                    border: `1px solid ${problemCountError ? "var(--color-danger-border)" : "var(--color-border)"}`,
                    borderRadius: "var(--radius-md)",
                    backgroundColor: "var(--color-bg)",
                    color: "var(--color-text)",
                    outline: "none",
                    fontSize: "0.9rem",
                    boxSizing: "border-box"
                  }}
                />
              </div>
              <div>
                <label
                  htmlFor="exam-problem-count-slider"
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
                  Problem count slider
                </label>
                <input
                  id="exam-problem-count-slider"
                  type="range"
                  min={EXAM_PROBLEM_COUNT_MIN}
                  max={EXAM_PROBLEM_COUNT_MAX}
                  step={1}
                  value={validProblemCount ?? EXAM_PROBLEM_COUNT_DEFAULT}
                  onChange={(event) => setProblemCountInput(event.target.value)}
                  style={{ width: "100%", accentColor: "var(--color-primary)" }}
                />
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    color: "var(--color-text-muted)",
                    fontSize: "0.75rem",
                    fontWeight: 600,
                    marginTop: "0.25rem",
                  }}
                >
                  <span>{EXAM_PROBLEM_COUNT_MIN}</span>
                  <span>{EXAM_PROBLEM_COUNT_MAX}</span>
                </div>
              </div>
              {problemCountError && (
                <div
                  id="exam-problem-count-error"
                  role="alert"
                  className="badge badge-danger"
                  style={{
                    padding: "0.5rem 0.75rem",
                    fontSize: "0.8125rem",
                    textTransform: "none",
                    letterSpacing: "normal",
                    fontWeight: 500,
                    display: "block",
                  }}
                >
                  {problemCountError}
                </div>
              )}
            </>
          ) : (
            <>
              <div>
                <label
                  htmlFor="exam-candidate-filter"
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
                  Filter problems
                </label>
                <input
                  id="exam-candidate-filter"
                  type="search"
                  value={keyword}
                  onChange={(event) => {
                    setKeyword(event.target.value);
                    setPage(1);
                  }}
                  placeholder="Search text or tags"
                  style={{
                    width: "100%",
                    padding: "0.6rem 0.8rem",
                    border: "1px solid var(--color-border)",
                    borderRadius: "var(--radius-md)",
                    backgroundColor: "var(--color-bg)",
                    color: "var(--color-text)",
                    outline: "none",
                    fontSize: "0.9rem",
                    boxSizing: "border-box"
                  }}
                />
              </div>

              {ineligibleMessage && (
                <div
                  role="alert"
                  className="badge badge-danger"
                  style={{
                    padding: "0.5rem 0.75rem",
                    fontSize: "0.8125rem",
                    textTransform: "none",
                    letterSpacing: "normal",
                    fontWeight: 500,
                    display: "block",
                  }}
                >
                  {ineligibleMessage}
                </div>
              )}
              {capReached && (
                <div style={{ color: "var(--color-text-muted)", fontSize: "0.8125rem", fontWeight: 600 }}>
                  Limit reached: at most {EXAM_PROBLEM_COUNT_MAX} problems per exam.
                </div>
              )}

              <div style={{ overflowX: "auto", border: "1px solid var(--color-border)", borderRadius: "var(--radius-md)" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
                  <thead>
                    <tr>
                      <th aria-label="Select" style={{ padding: "0.5rem", width: "2.5rem" }} />
                      <th style={{ padding: "0.5rem", textAlign: "left" }}>Problem</th>
                      {SORT_COLUMNS.map((column) => (
                        <th
                          key={column.key}
                          aria-sort={
                            sortBy === column.key
                              ? sortOrder === "asc"
                                ? "ascending"
                                : "descending"
                              : "none"
                          }
                          style={{ padding: "0.5rem", textAlign: "left" }}
                        >
                          <button
                            type="button"
                            onClick={() => handleSortClick(column.key)}
                            style={{
                              background: "none",
                              border: "none",
                              cursor: "pointer",
                              padding: 0,
                              font: "inherit",
                              fontWeight: 700,
                              color: sortBy === column.key ? "var(--color-primary)" : "inherit",
                            }}
                          >
                            {column.label}
                            {sortBy === column.key ? (sortOrder === "asc" ? " ▲" : " ▼") : ""}
                          </button>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((row) => {
                      const checked = selected.has(row.id);
                      return (
                        <tr
                          key={row.id}
                          style={
                            staleSet.has(row.id)
                              ? { backgroundColor: "var(--color-danger-bg)" }
                              : undefined
                          }
                        >
                          <td style={{ padding: "0.5rem" }}>
                            <input
                              type="checkbox"
                              aria-label={`Select ${row.text}`}
                              checked={checked}
                              disabled={isCreating || (!checked && capReached)}
                              onChange={() => toggleRow(row)}
                            />
                          </td>
                          <td style={{ padding: "0.5rem", maxWidth: "260px" }}>
                            <div
                              style={{
                                whiteSpace: "nowrap",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                              }}
                              title={row.text}
                            >
                              {row.text}
                              {staleSet.has(row.id) && (
                                <span
                                  className="badge badge-danger"
                                  style={{ marginLeft: "0.5rem", fontSize: "0.7rem" }}
                                >
                                  no longer eligible
                                </span>
                              )}
                            </div>
                          </td>
                          <td style={{ padding: "0.5rem" }}>{row.selectionScore.toFixed(2)}</td>
                          <td style={{ padding: "0.5rem" }}>
                            {new Date(row.createdAt).toLocaleDateString()}
                          </td>
                          <td style={{ padding: "0.5rem" }}>{row.successCount}</td>
                          <td style={{ padding: "0.5rem" }}>{row.failedCount}</td>
                        </tr>
                      );
                    })}
                    {items.length === 0 && (
                      <tr>
                        <td colSpan={6} style={{ padding: "1rem", textAlign: "center", color: "var(--color-text-muted)" }}>
                          {candidatesQuery.isLoading ? "Loading..." : "No eligible problems found."}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>

              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "0.8125rem", fontWeight: 600, color: "var(--color-text-muted)" }}>
                  {selected.size} selected
                </span>
                <Pagination page={page} totalPages={totalPages} onPageChange={setPage} />
              </div>
            </>
          )}

          <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem", borderTop: "1px solid var(--color-border)", paddingTop: "1rem", marginTop: "0.5rem" }}>
            <button
              type="button"
              onClick={handleClose}
              disabled={isCreating}
              className="btn btn-secondary"
              style={{
                padding: "0.5rem 1.25rem",
                fontSize: "0.875rem",
                fontWeight: 700,
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={
                isCreating ||
                (mode === "random"
                  ? validProblemCount === null
                  : selected.size === 0)
              }
              className="btn btn-primary"
              style={{
                padding: "0.5rem 1.25rem",
                fontSize: "0.875rem",
                fontWeight: 700,
              }}
            >
              {isCreating
                ? "Creating..."
                : mode === "manual"
                  ? `Create Exam (${selected.size} selected)`
                  : "Create Exam"}
            </button>
          </div>
        </form>
      </div>
    </Modal>
  );
}
