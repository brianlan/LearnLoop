import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Modal } from "@/components/Modal";
import Pagination from "@/components/Pagination";
import { api } from "@/api/client";
import { getSelectionCandidates } from "@/api/exams";
import { flattenFolders } from "@/pages/ProblemsPage.folderHelpers";
import type { FolderTreeResponse } from "@/types/folder";
import type { TagsResponse } from "@/types/tag";
import type {
  CreateExamRequest,
  SelectionCandidate,
  SelectionCandidateSortBy,
  SelectionCandidateSortCriterion,
} from "@/types/exam";

const EXAM_PROBLEM_COUNT_MIN = 1;
const EXAM_PROBLEM_COUNT_MAX = 30;
const EXAM_PROBLEM_COUNT_DEFAULT = 5;
const PICKER_PAGE_SIZE = 10;

const SORT_COLUMNS: Array<{ key: SelectionCandidateSortBy; label: string }> = [
  { key: "selectionScore", label: "Score" },
  { key: "addDate", label: "Added" },
  { key: "correctStreak", label: "Correct streak" },
  { key: "failureCount", label: "Failure" },
];

const DEFAULT_SORT: SelectionCandidateSortCriterion[] = [
  { field: "selectionScore", direction: "desc" },
];

interface CreateExamModalProps {
  isOpen: boolean;
  isCreating: boolean;
  onClose: () => void;
  onCreate: (request: CreateExamRequest) => void;
  /** Ids reported by a 422 INELIGIBLE_PROBLEMS rejection, marked in the picker (#682). */
  staleProblemIds?: string[];
  ineligibleMessage?: string | null;
  createError?: (Error & { code?: string }) | null;
}

function sortValue(row: SelectionCandidate, field: SelectionCandidateSortBy): number | null {
  // Native timestamp value: the API emits mixed whole/fractional-second ISO
  // forms, so raw strings sort by spelling instead of chronology (#682).
  if (field === "addDate") return Date.parse(row.createdAt);
  if (field === "failureCount") return row.failedCount;
  if (field === "correctStreak") return row.correctStreak;
  return row.selectionScore;
}

/** Mirrors the backend ranking: criteria-major, nulls last, id-asc ties (#708). */
function compareCandidates(
  a: SelectionCandidate,
  b: SelectionCandidate,
  criteria: SelectionCandidateSortCriterion[],
): number {
  for (const { field, direction } of criteria) {
    const av = sortValue(a, field);
    const bv = sortValue(b, field);
    if (av === null && bv === null) continue;
    if (av === null) return 1;
    if (bv === null) return -1;
    if (av !== bv) {
      const compared = av < bv ? -1 : 1;
      return direction === "desc" ? -compared : compared;
    }
  }
  return a.id < b.id ? -1 : 1;
}

export function CreateExamModal({
  isOpen,
  isCreating,
  onClose,
  onCreate,
  staleProblemIds = [],
  ineligibleMessage = null,
  createError = null,
}: CreateExamModalProps) {
  const [mode, setMode] = useState<"random" | "manual">("random");
  const [problemCountInput, setProblemCountInput] = useState(String(EXAM_PROBLEM_COUNT_DEFAULT));
  const [folderIds, setFolderIds] = useState<string[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [tagSearch, setTagSearch] = useState("");
  const [keyword, setKeyword] = useState("");
  const [sortCriteria, setSortCriteria] = useState<SelectionCandidateSortCriterion[]>([]);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Map<string, SelectionCandidate>>(new Map());

  useEffect(() => {
    if (isOpen) {
      setMode("random");
      setProblemCountInput(String(EXAM_PROBLEM_COUNT_DEFAULT));
      setFolderIds([]);
      setTags([]);
      setTagSearch("");
      setKeyword("");
      setSortCriteria([]);
      setPage(1);
      setSelected(new Map());
    }
  }, [isOpen]);

  const foldersQuery = useQuery({
    queryKey: ["folders"],
    queryFn: () => api.get<FolderTreeResponse>("/folders"),
    enabled: isOpen && mode === "random",
  });
  const tagsQuery = useQuery({
    queryKey: ["tagSuggestions"],
    queryFn: async () => {
      const response = await api.get<TagsResponse>("/tags");
      return response.items.map((item) => item.name);
    },
    enabled: isOpen && mode === "random",
    staleTime: 30_000,
  });
  const folderLabels = useMemo(() => {
    const labels = new Map<string, string>([["unfiled", "Unfiled"]]);
    for (const folder of flattenFolders(foldersQuery.data?.items ?? [])) {
      labels.set(folder.id, folder.parentId
        ? `${labels.get(folder.parentId)} / ${folder.name}`
        : folder.name);
    }
    return labels;
  }, [foldersQuery.data]);
  const matchingTags = (tagsQuery.data ?? []).filter((name) =>
    name.toLowerCase().includes(tagSearch.trim().toLowerCase()),
  );

  // Empty custom criteria IS the default Score-descending state (#708); the
  // effective list drives both the request and the confirm-time ordering.
  const effectiveSort =
    sortCriteria.length > 0 ? sortCriteria : DEFAULT_SORT;

  const candidatesQuery = useQuery({
    queryKey: ["selection-candidates", keyword, effectiveSort, page],
    queryFn: () =>
      getSelectionCandidates({
        q: keyword,
        sort: effectiveSort,
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

  // Header cycle (#708): inactive -> descending -> ascending -> inactive.
  // Joining appends at the end (lowest priority); direction flips keep the
  // criterion's priority; removal renumbers the rest.
  const handleSortClick = (column: SelectionCandidateSortBy) => {
    setSortCriteria((previous) => {
      const index = previous.findIndex((criterion) => criterion.field === column);
      if (index === -1) {
        return [...previous, { field: column, direction: "desc" }];
      }
      if (previous[index].direction === "desc") {
        return previous.map((criterion, i) =>
          i === index ? { ...criterion, direction: "asc" as const } : criterion,
        );
      }
      return previous.filter((criterion) => criterion.field !== column);
    });
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
      onCreate({
        mode: "random", maxProblemCount: validProblemCount,
        ...(folderIds.length > 0 ? { folderIds } : {}),
        ...(tags.length > 0 ? { tags } : {}),
      });
      return;
    }
    if (selected.size === 0) return;
    // Item order = displayed order at confirm: the same effective criteria
    // the backend applies, across ALL retained rows (#682, #708).
    const ordered = [...selected.values()].sort((a, b) =>
      compareCandidates(a, b, effectiveSort),
    );
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
                Up to {validProblemCount ?? EXAM_PROBLEM_COUNT_DEFAULT} problems. Fewer will be created
                if there are fewer eligible matches; filters are never widened to fill the exam.
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
              <fieldset
                disabled={isCreating}
                aria-describedby="exam-folder-help"
                style={{ border: "1px solid var(--color-border)", borderRadius: "var(--radius-md)", padding: "0.75rem" }}
              >
                <legend style={{ fontWeight: 700 }}>Folders</legend>
                <p id="exam-folder-help" style={{ margin: "0 0 0.5rem", fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                  Match any selected folder, including its descendants. None selected means all folders, including Unfiled.
                </p>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "0.25rem", marginBottom: "0.5rem" }}>
                  {folderIds.map((id) => (
                    <button
                      key={id}
                      type="button"
                      className="btn btn-secondary"
                      aria-label={`Remove folder ${folderLabels.get(id) ?? id}`}
                      onClick={() => setFolderIds(folderIds.filter((value) => value !== id))}
                    >
                      {folderLabels.get(id) ?? id} ×
                    </button>
                  ))}
                </div>
                {foldersQuery.isLoading && <p role="status">Loading folders...</p>}
                {foldersQuery.isError && (
                  <p role="alert">
                    Could not load folders. Your selections are unchanged.{" "}
                    <button type="button" className="btn btn-secondary" onClick={() => void foldersQuery.refetch()}>Retry folders</button>
                  </p>
                )}
                <div style={{ maxHeight: "144px", overflowY: "auto" }}>
                  {[...folderLabels].map(([id, label]) => (
                    <label key={id} style={{ display: "flex", gap: "0.5rem", padding: "0.25rem" }}>
                      <input
                        type="checkbox"
                        checked={folderIds.includes(id)}
                        onChange={() => setFolderIds((previous) => previous.includes(id)
                          ? previous.filter((value) => value !== id)
                          : [...previous, id])}
                      />
                      {label}
                    </label>
                  ))}
                </div>
              </fieldset>
              <fieldset
                disabled={isCreating}
                aria-describedby="exam-tag-help"
                style={{ border: "1px solid var(--color-border)", borderRadius: "var(--radius-md)", padding: "0.75rem" }}
              >
                <legend style={{ fontWeight: 700 }}>Tags</legend>
                <p id="exam-tag-help" style={{ margin: "0 0 0.5rem", fontSize: "0.8125rem", color: "var(--color-text-muted)" }}>
                  Match all selected tags exactly. None selected means no tag restriction. Folder and tag groups must both match.
                </p>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "0.25rem", marginBottom: "0.5rem" }}>
                  {tags.map((tag) => (
                    <button
                      key={tag}
                      type="button"
                      className="btn btn-secondary"
                      aria-label={`Remove tag ${tag}`}
                      onClick={() => setTags(tags.filter((value) => value !== tag))}
                    >
                      {tag} ×
                    </button>
                  ))}
                </div>
                <label htmlFor="exam-tag-search">Search existing tags</label>
                <input
                  id="exam-tag-search"
                  type="search"
                  value={tagSearch}
                  onChange={(event) => setTagSearch(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") event.preventDefault();
                  }}
                  style={{
                    display: "block", width: "100%", boxSizing: "border-box",
                    margin: "0.5rem 0", padding: "0.5rem",
                    backgroundColor: "var(--color-bg)", color: "var(--color-text)",
                    border: "1px solid var(--color-border)", borderRadius: "var(--radius-md)",
                  }}
                />
                {tagsQuery.isLoading && <p role="status">Loading tags...</p>}
                {tagsQuery.isError && (
                  <p role="alert">
                    Could not load tags. Your selections are unchanged.{" "}
                    <button type="button" className="btn btn-secondary" onClick={() => void tagsQuery.refetch()}>Retry tags</button>
                  </p>
                )}
                <div style={{ maxHeight: "144px", overflowY: "auto" }}>
                  {matchingTags.map((tag) => (
                    <label key={tag} style={{ display: "flex", gap: "0.5rem", padding: "0.25rem" }}>
                      <input
                        type="checkbox"
                        checked={tags.includes(tag)}
                        onChange={() => setTags((previous) => previous.includes(tag)
                          ? previous.filter((value) => value !== tag)
                          : [...previous, tag])}
                      />
                      {tag}
                    </label>
                  ))}
                </div>
                {tagsQuery.isSuccess && matchingTags.length === 0 && <p>No existing tags match.</p>}
              </fieldset>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={isCreating || (folderIds.length === 0 && tags.length === 0)}
                onClick={() => {
                  setFolderIds([]);
                  setTags([]);
                  setTagSearch("");
                }}
              >
                Clear filters
              </button>
              {createError && (
                <p role="alert" style={{ color: "var(--color-text-danger)", margin: 0 }}>
                  {createError.code === "NO_ELIGIBLE_PROBLEMS"
                    ? "No eligible problems match. Try adjusting or clearing the filters; cooldown and minimum age still apply."
                    : createError.message}
                </p>
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

              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: "0.5rem",
                }}
              >
                {sortCriteria.length === 0 ? (
                  <span
                    role="status"
                    style={{ fontSize: "0.8125rem", fontWeight: 600, color: "var(--color-text-muted)" }}
                  >
                    Default: Score ↓
                  </span>
                ) : null}
                <button
                  type="button"
                  onClick={() => {
                    setSortCriteria([]);
                    setPage(1);
                  }}
                  disabled={sortCriteria.length === 0}
                  className="btn btn-secondary"
                  style={{ padding: "0.25rem 0.75rem", fontSize: "0.8125rem", fontWeight: 700 }}
                >
                  Reset sorting
                </button>
              </div>

              <div style={{ overflowX: "auto", border: "1px solid var(--color-border)", borderRadius: "var(--radius-md)" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
                  <thead>
                    <tr>
                      <th aria-label="Select" style={{ padding: "0.5rem", width: "2.5rem" }} />
                      <th style={{ padding: "0.5rem", textAlign: "left" }}>Problem</th>
                      {SORT_COLUMNS.map((column) => {
                        const priority = sortCriteria.findIndex(
                          (criterion) => criterion.field === column.key,
                        );
                        const active = priority !== -1;
                        const isDefaultPrimary =
                          !active && column.key === effectiveSort[0].field;
                        const direction = active ? sortCriteria[priority].direction : null;
                        const arrow = direction === "asc" ? "↑" : "↓";
                        // Only the primary criterion gets aria-sort; buttons describe secondary priorities.
                        const ariaSort =
                          column.key === effectiveSort[0].field
                            ? effectiveSort[0].direction === "asc"
                              ? "ascending"
                              : "descending"
                            : "none";
                        const ariaLabel = active
                          ? direction === "desc"
                            ? `${column.label}, descending, priority ${priority + 1}. Activate to switch to ascending.`
                            : `${column.label}, ascending, priority ${priority + 1}. Activate to remove this sort.`
                          : isDefaultPrimary
                            ? `${column.label}, default order ${effectiveSort[0].direction === "asc" ? "ascending" : "descending"}. Activate to sort by ${column.label} explicitly (descending, priority 1).`
                            : `${column.label}, not sorted. Activate to sort descending.`;
                        return (
                          <th
                            key={column.key}
                            aria-sort={ariaSort}
                            style={{ padding: "0.5rem", textAlign: "left" }}
                          >
                            <button
                              type="button"
                              onClick={() => handleSortClick(column.key)}
                              aria-label={ariaLabel}
                              style={{
                                background: "none",
                                border: "none",
                                cursor: "pointer",
                                padding: 0,
                                font: "inherit",
                                fontWeight: 700,
                                color: active ? "var(--color-primary)" : "inherit",
                              }}
                            >
                              {active ? `${column.label} ${arrow} ${priority + 1}` : column.label}
                            </button>
                          </th>
                        );
                      })}
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
                          <td style={{ padding: "0.5rem" }}>
                            {row.correctStreak === null ? "—" : row.correctStreak}
                          </td>
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

              <p style={{ color: "var(--color-text-muted)", fontSize: "0.75rem", margin: 0, fontWeight: 500 }}>
                Correct streak counts consecutive correct results from saved, judged practice and exam
                history only. “—” means older history is missing, so the count covers saved records only.
              </p>

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
