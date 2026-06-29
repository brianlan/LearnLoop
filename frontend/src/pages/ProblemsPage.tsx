import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { formatProblemReference } from "@/utils/format";
import { useTagSuggestions } from "@/hooks/useTagSuggestions";
import Pagination from "@/components/Pagination";
import { TagList } from "@/components/TagPill";
import type { ProblemListItem, ProblemsResponse } from "@/types/problem";
import type { FolderNode, FolderTreeResponse } from "@/types/folder";
import { PROBLEM_TYPE_FILTER_OPTIONS } from "@/constants/problemTypes";

const SIDEBAR_COLLAPSED_KEY = "problems.folderSidebarCollapsed";
const UNFILED_FOLDER_ID = "unfiled";
const EMPTY_FOLDERS: FolderNode[] = [];

type Feedback = { type: "success" | "error"; message: string } | null;
type ProblemSortBy = "" | "selectionScore" | "addDate" | "lastTestDate";
type ProblemSortOrder = "asc" | "desc";

const SORT_BY_OPTIONS: Array<{ value: ProblemSortBy; label: string }> = [
  { value: "", label: "Default" },
  { value: "selectionScore", label: "Selection score" },
  { value: "addDate", label: "Add date" },
  { value: "lastTestDate", label: "Last test date" },
];

const SORT_ORDER_OPTIONS: Array<{ value: ProblemSortOrder; label: string }> = [
  { value: "desc", label: "Descending" },
  { value: "asc", label: "Ascending" },
];

type ProblemsPagePreferences = {
  selectedFolderId: string;
  selectedTag: string;
  selectedProblemType: string;
  searchQuery: string;
  sortBy: ProblemSortBy;
  sortOrder: ProblemSortOrder;
};

const DEFAULT_PREFERENCES: ProblemsPagePreferences = {
  selectedFolderId: "",
  selectedTag: "",
  selectedProblemType: "",
  searchQuery: "",
  sortBy: "",
  sortOrder: "desc",
};

let problemsPagePreferences: ProblemsPagePreferences = { ...DEFAULT_PREFERENCES };

// Test-only helper to reset the in-memory preferences between test runs.
export function _resetProblemsPagePreferencesForTests() {
  problemsPagePreferences = { ...DEFAULT_PREFERENCES };
}

function flattenFolders(folders: FolderNode[], depth = 0): Array<FolderNode & { depth: number }> {
  return folders.flatMap((folder) => [
    { ...folder, depth },
    ...flattenFolders(folder.children, depth + 1),
  ]);
}

function getDescendantIds(folder: FolderNode): Set<string> {
  const ids = new Set<string>();
  for (const child of folder.children) {
    ids.add(child.id);
    for (const id of getDescendantIds(child)) ids.add(id);
  }
  return ids;
}

function findFolder(folders: FolderNode[], folderId: string): FolderNode | undefined {
  for (const folder of folders) {
    if (folder.id === folderId) return folder;
    const found = findFolder(folder.children, folderId);
    if (found) return found;
  }
  return undefined;
}

function buildAncestorMap(folders: FolderNode[]) {
  const map = new Map<string, string[]>();
  const visit = (folder: FolderNode, ancestors: string[]) => {
    map.set(folder.id, ancestors);
    folder.children.forEach((child) => visit(child, [...ancestors, folder.id]));
  };
  folders.forEach((folder) => visit(folder, []));
  return map;
}

function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Request failed";
}

export function ProblemsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [selectedFolderId, setSelectedFolderId] = useState<string>(problemsPagePreferences.selectedFolderId);
  const [selectedTag, setSelectedTag] = useState<string>(problemsPagePreferences.selectedTag);
  const [selectedProblemType, setSelectedProblemType] = useState<string>(problemsPagePreferences.selectedProblemType);
  const [searchQuery, setSearchQuery] = useState<string>(problemsPagePreferences.searchQuery);
  const [sortBy, setSortBy] = useState<ProblemSortBy>(problemsPagePreferences.sortBy);
  const [sortOrder, setSortOrder] = useState<ProblemSortOrder>(problemsPagePreferences.sortOrder);
  const [selectedProblemIds, setSelectedProblemIds] = useState<Set<string>>(new Set());
  const [isSelectionMode, setIsSelectionMode] = useState(false);
  const [bulkTarget, setBulkTarget] = useState<string>(UNFILED_FOLDER_ID);
  const longPressTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const suppressNavigationRef = useRef(false);
  const [movingFolderId, setMovingFolderId] = useState<string | null>(null);
  const [movingParentId, setMovingParentId] = useState<string>("root");
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [expandedFolderIds, setExpandedFolderIds] = useState<Set<string>>(new Set());
  const [openFolderActionsId, setOpenFolderActionsId] = useState<string | null>(null);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() => {
    return window.sessionStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
  });
  const pageSize = 20;

  const { data: problemsData, isLoading: isLoadingProblems } = useQuery({
    queryKey: ["problems", page, selectedTag, selectedProblemType, searchQuery, selectedFolderId, sortBy, sortOrder],
    queryFn: async () => {
      const params = new URLSearchParams({
        page: String(page),
        pageSize: String(pageSize),
      });
      if (selectedTag) params.append("tag", selectedTag);
      if (selectedProblemType) params.append("type", selectedProblemType);
      if (searchQuery.trim()) params.append("q", searchQuery.trim());
      if (selectedFolderId) params.append("folderId", selectedFolderId);
      if (sortBy) {
        params.append("sortBy", sortBy);
        params.append("sortOrder", sortOrder);
      }
      return api.get<ProblemsResponse>(`/problems?${params.toString()}`);
    },
  });

  const { data: folderTree } = useQuery({
    queryKey: ["folders"],
    queryFn: async () => api.get<FolderTreeResponse>("/folders"),
  });

  const tags = useTagSuggestions();

  const problems = problemsData?.items || [];
  const total = problemsData?.total || 0;
  const totalPages = Math.ceil(total / pageSize);
  const folders = folderTree?.items ?? EMPTY_FOLDERS;
  const flatFolders = useMemo(() => flattenFolders(folders), [folders]);
  const ancestorMap = useMemo(() => buildAncestorMap(folders), [folders]);
  const selectedFolder = selectedFolderId && selectedFolderId !== UNFILED_FOLDER_ID
    ? findFolder(folders, selectedFolderId)
    : undefined;
  const activeFolderLabel = selectedFolderId === UNFILED_FOLDER_ID
    ? "Unfiled"
    : selectedFolder?.name ?? "All Problems";
  const movingFolder = movingFolderId ? findFolder(folders, movingFolderId) : undefined;
  const invalidMoveTargets = movingFolder ? getDescendantIds(movingFolder) : new Set<string>();
  if (movingFolderId) invalidMoveTargets.add(movingFolderId);

  useEffect(() => {
    window.sessionStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(isSidebarCollapsed));
  }, [isSidebarCollapsed]);

  useEffect(() => {
    setExpandedFolderIds((current) => {
      const next = new Set(current);
      folders.forEach((folder) => next.add(folder.id));
      if (selectedFolderId && selectedFolderId !== UNFILED_FOLDER_ID) {
        ancestorMap.get(selectedFolderId)?.forEach((folderId) => next.add(folderId));
      }
      if (next.size === current.size && Array.from(next).every((folderId) => current.has(folderId))) {
        return current;
      }
      return next;
    });
  }, [ancestorMap, folders, selectedFolderId]);

  const refreshProblemsAndFolders = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["problems"] }),
      queryClient.invalidateQueries({ queryKey: ["folders"] }),
    ]);
  };

  const folderMutation = useMutation({
    mutationFn: async (operation: () => Promise<unknown>) => operation(),
    onSuccess: async () => {
      await refreshProblemsAndFolders();
    },
  });

  const bulkMoveMutation = useMutation({
    mutationFn: async ({ problemIds, folderId }: { problemIds: string[]; folderId: string | null }) => {
      return api.patch<{ ok: boolean }>("/problems/bulk-folder", { problemIds, folderId });
    },
    onSuccess: async () => {
      exitSelectionMode();
      setPage(1);
      setFeedback({ type: "success", message: "Problems moved" });
      await refreshProblemsAndFolders();
    },
    onError: (error) => {
      setFeedback({ type: "error", message: getErrorMessage(error) });
    },
  });

  const updateFolderFilter = (folderId: string) => {
    setOpenFolderActionsId(null);
    setSelectedFolderId(folderId);
    problemsPagePreferences.selectedFolderId = folderId;
    setPage(1);
    exitSelectionMode();
  };

  const updateSortBy = (value: ProblemSortBy) => {
    setSortBy(value);
    problemsPagePreferences.sortBy = value;
    setPage(1);
    exitSelectionMode();
  };

  const updateSortOrder = (value: ProblemSortOrder) => {
    setSortOrder(value);
    problemsPagePreferences.sortOrder = value;
    setPage(1);
    exitSelectionMode();
  };

  const runFolderOperation = async (
    operation: () => Promise<unknown>,
    successMessage: string,
  ) => {
    setFeedback(null);
    try {
      await folderMutation.mutateAsync(operation);
      setFeedback({ type: "success", message: successMessage });
    } catch (error) {
      setFeedback({ type: "error", message: getErrorMessage(error) });
    }
  };

  const createFolder = (parentId: string | null = null) => {
    const name = window.prompt(parentId ? "New child folder name" : "New folder name");
    if (!name?.trim()) return;
    void runFolderOperation(
      () => api.post("/folders", { name: name.trim(), parentId }),
      "Folder created",
    );
  };

  const renameFolder = (folder: FolderNode) => {
    const name = window.prompt("Rename folder", folder.name);
    if (!name?.trim() || name.trim() === folder.name) return;
    void runFolderOperation(
      () => api.patch(`/folders/${folder.id}`, { name: name.trim() }),
      "Folder renamed",
    );
  };

  const deleteFolder = (folder: FolderNode) => {
    if (!window.confirm(`Delete "${folder.name}"?`)) return;
    void runFolderOperation(
      () => api.delete(`/folders/${folder.id}`),
      "Folder deleted",
    );
  };

  const openMoveFolder = (folder: FolderNode) => {
    setMovingFolderId(folder.id);
    setMovingParentId(folder.parentId ?? "root");
  };

  const moveFolder = () => {
    if (!movingFolderId) return;
    const parentId = movingParentId === "root" ? null : movingParentId;
    void runFolderOperation(
      () => api.patch(`/folders/${movingFolderId}`, { parentId }),
      "Folder moved",
    );
    setMovingFolderId(null);
  };

  const toggleProblemSelection = (problemId: string) => {
    setSelectedProblemIds((current) => {
      const next = new Set(current);
      if (next.has(problemId)) next.delete(problemId);
      else next.add(problemId);
      return next;
    });
  };

  const exitSelectionMode = () => {
    setSelectedProblemIds(new Set());
    setIsSelectionMode(false);
  };

  const handleProblemPointerDown = (problemId: string) => {
    suppressNavigationRef.current = false;
    longPressTimeoutRef.current = setTimeout(() => {
      suppressNavigationRef.current = true;
      setIsSelectionMode(true);
      setSelectedProblemIds((current) => {
        const next = new Set(current);
        next.add(problemId);
        return next;
      });
    }, 500);
  };

  const handleProblemPointerUpOrCancel = () => {
    if (longPressTimeoutRef.current) {
      clearTimeout(longPressTimeoutRef.current);
      longPressTimeoutRef.current = null;
    }
  };

  const handleProblemCardClick = (problemId: string) => {
    if (suppressNavigationRef.current) {
      suppressNavigationRef.current = false;
      return;
    }
    if (isSelectionMode) {
      toggleProblemSelection(problemId);
    } else {
      navigate(`/problems/${problemId}`);
    }
  };

  const moveSelectedProblems = () => {
    if (selectedProblemIds.size === 0) return;
    bulkMoveMutation.mutate({
      problemIds: Array.from(selectedProblemIds),
      folderId: bulkTarget === UNFILED_FOLDER_ID ? null : bulkTarget,
    });
  };

  const folderButtonStyle = (active: boolean): React.CSSProperties => ({
    width: "100%",
    minWidth: 0,
    flex: "1 1 auto",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: "0.5rem",
    padding: "0.5rem 0.75rem",
    borderRadius: "var(--radius-md)",
    border: active ? "1px solid var(--color-primary)" : "1px solid transparent",
    background: active ? "var(--color-primary-bg)" : "transparent",
    color: active ? "var(--color-primary-text)" : "var(--color-text-muted)",
    cursor: "pointer",
    textAlign: "left",
    fontWeight: active ? 700 : 500,
    fontSize: "0.875rem",
    transition: "all var(--transition-fast)",
  });

  const renderFolderNode = (folder: FolderNode, depth = 0): JSX.Element => {
    const isExpanded = expandedFolderIds.has(folder.id);
    const hasChildren = folder.children.length > 0;
    return (
      <li key={folder.id}>
        <div style={{ position: "relative", display: "flex", alignItems: "center", gap: "0.25rem", paddingLeft: `${depth * 0.75}rem` }}>
          <button
            type="button"
            aria-label={isExpanded ? `Collapse ${folder.name}` : `Expand ${folder.name}`}
            disabled={!hasChildren}
            onClick={() => {
              setExpandedFolderIds((current) => {
                const next = new Set(current);
                if (next.has(folder.id)) next.delete(folder.id);
                else next.add(folder.id);
                return next;
              });
            }}
            style={{
              width: "1.5rem",
              height: "1.5rem",
              border: "1px solid transparent",
              background: "transparent",
              color: "var(--color-text-muted)",
              cursor: hasChildren ? "pointer" : "default",
            }}
          >
            {hasChildren ? (isExpanded ? "v" : ">") : ""}
          </button>
          <button
            type="button"
            aria-label={`Select folder ${folder.name}`}
            onClick={() => updateFolderFilter(folder.id)}
            style={folderButtonStyle(selectedFolderId === folder.id)}
          >
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{folder.name}</span>
            <span aria-label={`${folder.name} count`} style={{ flex: "0 0 auto" }}>{folder.problemCount}</span>
          </button>
          <button
            type="button"
            aria-label={`Folder actions for ${folder.name}`}
            aria-haspopup="menu"
            aria-expanded={openFolderActionsId === folder.id}
            onClick={() => setOpenFolderActionsId((current) => (current === folder.id ? null : folder.id))}
            style={{
              flex: "0 0 auto",
              width: "1.75rem",
              height: "1.75rem",
              border: "1px solid var(--color-border)",
              borderRadius: "4px",
              background: "var(--color-surface)",
              color: "var(--color-text)",
              cursor: "pointer",
              lineHeight: 1,
            }}
          >
            ...
          </button>
          {openFolderActionsId === folder.id && (
            <div
              role="menu"
              aria-label={`Actions for ${folder.name}`}
              style={{
                position: "absolute",
                top: "calc(100% + 0.25rem)",
                right: 0,
                zIndex: 2,
                display: "grid",
                gap: "0.25rem",
                minWidth: "8rem",
                padding: "0.35rem",
                border: "1px solid var(--color-border)",
                borderRadius: "4px",
                background: "var(--color-surface)",
                boxShadow: "0 8px 24px rgba(0, 0, 0, 0.12)",
              }}
            >
              <button
                type="button"
                role="menuitem"
                aria-label={`New child folder in ${folder.name}`}
                onClick={() => {
                  setOpenFolderActionsId(null);
                  createFolder(folder.id);
                }}
              >
                New child
              </button>
              <button
                type="button"
                role="menuitem"
                aria-label={`Rename ${folder.name}`}
                onClick={() => {
                  setOpenFolderActionsId(null);
                  renameFolder(folder);
                }}
              >
                Rename
              </button>
              <button
                type="button"
                role="menuitem"
                aria-label={`Move ${folder.name}`}
                onClick={() => {
                  setOpenFolderActionsId(null);
                  openMoveFolder(folder);
                }}
              >
                Move
              </button>
              <button
                type="button"
                role="menuitem"
                aria-label={`Delete ${folder.name}`}
                onClick={() => {
                  setOpenFolderActionsId(null);
                  deleteFolder(folder);
                }}
              >
                Delete
              </button>
            </div>
          )}
        </div>
        {hasChildren && isExpanded && (
          <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {folder.children.map((child) => renderFolderNode(child, depth + 1))}
          </ul>
        )}
      </li>
    );
  };
  return (
    <main style={{ padding: "2rem", minHeight: "calc(100vh - 60px)", backgroundColor: "var(--color-bg)", color: "var(--color-text)", transition: "background var(--transition-normal)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.5rem", gap: "1rem", flexWrap: "wrap" }}>
        <h1 style={{ fontSize: "2rem", fontWeight: 800, margin: 0, letterSpacing: "-0.03em" }}>Problems</h1>
      </div>

      {feedback && (
        <div
          role="status"
          className={`badge badge-${feedback.type === "success" ? "success" : "danger"}`}
          style={{
            display: "block",
            marginBottom: "1.5rem",
            padding: "1rem",
            borderRadius: "var(--radius-md)",
            fontSize: "0.875rem",
            textTransform: "none",
            fontWeight: 500,
            letterSpacing: "normal",
            lineHeight: "1.4"
          }}
        >
          {feedback.message}
        </div>
      )}

      {movingFolderId && (
        <div
          role="dialog"
          aria-label="Move folder"
          className="card-premium"
          style={{
            marginBottom: "1.5rem",
            padding: "1.25rem",
            display: "flex",
            alignItems: "center",
            gap: "1rem",
            flexWrap: "wrap"
          }}
        >
          <label htmlFor="folder-parent-picker" style={{ fontWeight: 600, fontSize: "0.9rem" }}>Move {movingFolder?.name ?? "folder"} to: </label>
          <select
            id="folder-parent-picker"
            value={movingParentId}
            onChange={(event) => setMovingParentId(event.target.value)}
            style={{ width: "auto", minWidth: "180px", padding: "0.5rem", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border)", backgroundColor: "var(--color-bg)" }}
          >
            <option value="root">Root</option>
            {flatFolders
              .filter((folder) => !invalidMoveTargets.has(folder.id))
              .map((folder) => (
                <option key={folder.id} value={folder.id}>
                  {"- ".repeat(folder.depth)}{folder.name}
                </option>
              ))}
          </select>
          <button type="button" className="btn btn-primary" style={{ padding: "0.5rem 1rem", fontSize: "0.875rem" }} onClick={moveFolder}>Save move</button>
          <button type="button" className="btn btn-secondary" style={{ padding: "0.5rem 1rem", fontSize: "0.875rem" }} onClick={() => setMovingFolderId(null)}>Cancel</button>
        </div>
      )}

      <div
        style={{
          marginBottom: "1.5rem",
          padding: "1.25rem 1.5rem",
          borderRadius: "var(--radius-xl)",
          backgroundColor: "var(--color-surface)",
          border: "1px solid var(--color-border)",
          boxShadow: "var(--shadow-sm)",
          display: "flex",
          gap: "1.25rem",
          alignItems: "center",
          flexWrap: "wrap",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", gap: "1.25rem", flexWrap: "wrap", alignItems: "center" }}>
          <div>
            <label htmlFor="search-input" style={{ fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", display: "block", marginBottom: "0.375rem" }}>Search problems: </label>
            <input
              id="search-input"
              type="text"
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                problemsPagePreferences.searchQuery = e.target.value;
                setPage(1);
                exitSelectionMode();
              }}
              placeholder="Search text or tag..."
              style={{
                padding: "0.5rem 0.75rem",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-surface-muted)",
                color: "var(--color-text)",
                fontSize: "0.875rem",
                minWidth: "220px",
                outline: "none"
              }}
            />
          </div>
          <div>
            <label htmlFor="tag-filter" style={{ fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", display: "block", marginBottom: "0.375rem" }}>Filter by Tag: </label>
            <select
              id="tag-filter"
              value={selectedTag}
              onChange={(e) => {
                setSelectedTag(e.target.value);
                problemsPagePreferences.selectedTag = e.target.value;
                setPage(1);
                exitSelectionMode();
              }}
              style={{
                padding: "0.5rem 0.75rem",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-surface-muted)",
                color: "var(--color-text)",
                fontSize: "0.875rem",
                minWidth: "150px",
                outline: "none"
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
            <label htmlFor="type-filter" style={{ fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", display: "block", marginBottom: "0.375rem" }}>Filter by Type: </label>
            <select
              id="type-filter"
              value={selectedProblemType}
              onChange={(e) => {
                setSelectedProblemType(e.target.value);
                problemsPagePreferences.selectedProblemType = e.target.value;
                setPage(1);
                exitSelectionMode();
              }}
              style={{
                padding: "0.5rem 0.75rem",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-surface-muted)",
                color: "var(--color-text)",
                fontSize: "0.875rem",
                minWidth: "160px",
                outline: "none"
              }}
            >
              {PROBLEM_TYPE_FILTER_OPTIONS.map((option) => (
                <option key={option.value || "all"} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="sort-by" style={{ fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", display: "block", marginBottom: "0.375rem" }}>Sort by: </label>
            <select
              id="sort-by"
              value={sortBy}
              onChange={(e) => updateSortBy(e.target.value as ProblemSortBy)}
              style={{
                padding: "0.5rem 0.75rem",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: "var(--color-surface-muted)",
                color: "var(--color-text)",
                fontSize: "0.875rem",
                minWidth: "140px",
                outline: "none"
              }}
            >
              {SORT_BY_OPTIONS.map((option) => (
                <option key={option.value || "default"} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="sort-order" style={{ fontSize: "0.725rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)", display: "block", marginBottom: "0.375rem" }}>Order: </label>
            <select
              id="sort-order"
              value={sortOrder}
              onChange={(e) => updateSortOrder(e.target.value as ProblemSortOrder)}
              disabled={!sortBy}
              style={{
                padding: "0.5rem 0.75rem",
                borderRadius: "var(--radius-md)",
                border: "1px solid var(--color-border)",
                backgroundColor: !sortBy ? "var(--color-disabled-bg)" : "var(--color-surface-muted)",
                color: !sortBy ? "var(--color-disabled-text)" : "var(--color-text)",
                fontSize: "0.875rem",
                minWidth: "130px",
                outline: "none"
              }}
            >
              {SORT_ORDER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div style={{ color: "var(--color-text-muted)", fontSize: "0.875rem", fontWeight: 600 }}>
          {total === 0
            ? selectedFolderId
              ? `No problems found in ${activeFolderLabel}`
              : "No problems found"
            : `Showing ${problems.length} of ${total} problem${total === 1 ? "" : "s"}`}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: isSidebarCollapsed ? "minmax(0, 1fr)" : "280px minmax(0, 1fr)", gap: "1.5rem" }}>
        {isSidebarCollapsed ? (
          <div
            className="card-premium"
            style={{
              marginBottom: "1.5rem",
              display: "flex",
              alignItems: "center",
              gap: "1rem",
              padding: "0.75rem 1.25rem",
            }}
          >
            <button type="button" className="btn btn-secondary" style={{ padding: "0.4rem 0.75rem", fontSize: "0.825rem" }} onClick={() => setIsSidebarCollapsed(false)}>Show folders</button>
            <span style={{ fontWeight: 600, fontSize: "0.9rem" }}>Folder: {activeFolderLabel}</span>
          </div>
        ) : (
          <aside
            aria-label="Problem folders"
            className="card-premium"
            style={{
              padding: "1.25rem",
              alignSelf: "start",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.5rem", marginBottom: "1rem" }}>
              <strong style={{ fontSize: "1.125rem", fontWeight: 800 }}>Folders</strong>
              <button type="button" className="btn btn-secondary" style={{ padding: "0.25rem 0.5rem", fontSize: "0.75rem" }} onClick={() => setIsSidebarCollapsed(true)}>Hide</button>
            </div>
            <button type="button" className="btn btn-primary" onClick={() => createFolder(null)} style={{ width: "100%", marginBottom: "1rem", padding: "0.5rem 1rem", fontSize: "0.875rem" }}>
              Create folder
            </button>
            <nav aria-label="Folder filters" style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
              <button
                type="button"
                aria-label="Show All Problems"
                onClick={() => updateFolderFilter("")}
                style={folderButtonStyle(!selectedFolderId)}
              >
                <span>All Problems</span>
                <span aria-label="All Problems count" className="badge badge-muted" style={{ padding: "0.1rem 0.4rem", fontSize: "0.7rem", borderRadius: "var(--radius-sm)" }}>{folderTree?.allProblemsCount ?? 0}</span>
              </button>
              <button
                type="button"
                aria-label="Show Unfiled"
                onClick={() => updateFolderFilter(UNFILED_FOLDER_ID)}
                style={folderButtonStyle(selectedFolderId === UNFILED_FOLDER_ID)}
              >
                <span>Unfiled</span>
                <span aria-label="Unfiled count" className="badge badge-muted" style={{ padding: "0.1rem 0.4rem", fontSize: "0.7rem", borderRadius: "var(--radius-sm)" }}>{folderTree?.unfiledCount ?? 0}</span>
              </button>
              <ul style={{ listStyle: "none", margin: "0.5rem 0 0", padding: 0 }}>
                {folders.map((folder) => renderFolderNode(folder))}
              </ul>
            </nav>
          </aside>
        )}

        <section>
          {isSelectionMode && (
            <div
              aria-label="Bulk actions"
              className="card-premium"
              style={{
                marginBottom: "1.5rem",
                padding: "0.75rem 1.25rem",
                border: "1px solid var(--color-primary)",
                backgroundColor: "var(--color-primary-bg)",
                display: "flex",
                gap: "1rem",
                alignItems: "center",
                flexWrap: "wrap",
                boxShadow: "var(--shadow-sm)"
              }}
            >
              <span style={{ color: "var(--color-primary-text)", fontWeight: 700, fontSize: "0.9rem" }}>{selectedProblemIds.size} selected</span>
              <button
                type="button"
                className="btn btn-secondary"
                style={{ padding: "0.4rem 0.75rem", fontSize: "0.825rem" }}
                onClick={() => {
                  const currentPageIds = problems.map((p) => p.id);
                  const allSelected = currentPageIds.every((id) => selectedProblemIds.has(id));
                  if (allSelected) {
                    setSelectedProblemIds((current) => {
                      const next = new Set(current);
                      currentPageIds.forEach((id) => next.delete(id));
                      return next;
                    });
                  } else {
                    setSelectedProblemIds((current) => {
                      const next = new Set(current);
                      currentPageIds.forEach((id) => next.add(id));
                      return next;
                    });
                  }
                }}
              >
                {problems.every((p) => selectedProblemIds.has(p.id)) ? "Deselect all" : "Select all"}
              </button>
              <label htmlFor="bulk-folder-picker" style={{ color: "var(--color-primary-text)", fontWeight: 600, fontSize: "0.9rem" }}>Move to: </label>
              <select
                id="bulk-folder-picker"
                value={bulkTarget}
                onChange={(event) => setBulkTarget(event.target.value)}
                style={{ width: "auto", minWidth: "150px", padding: "0.4rem 0.6rem", fontSize: "0.875rem", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border)", backgroundColor: "var(--color-surface)" }}
              >
                <option value={UNFILED_FOLDER_ID}>Unfiled</option>
                {flatFolders.map((folder) => (
                  <option key={folder.id} value={folder.id}>
                    {"- ".repeat(folder.depth)}{folder.name}
                  </option>
                ))}
              </select>
              <button type="button" className="btn btn-primary" style={{ padding: "0.4rem 0.75rem", fontSize: "0.825rem" }} onClick={moveSelectedProblems} disabled={selectedProblemIds.size === 0 || bulkMoveMutation.isPending}>
                Move selected
              </button>
              <button type="button" className="btn btn-secondary" style={{ padding: "0.4rem 0.75rem", fontSize: "0.825rem" }} onClick={() => setSelectedProblemIds(new Set())}>Clear selection</button>
              <button type="button" className="btn btn-secondary" style={{ padding: "0.4rem 0.75rem", fontSize: "0.825rem" }} onClick={exitSelectionMode}>Quit selection</button>
            </div>
          )}

      {isLoadingProblems ? (
        <div style={{ padding: "2rem", textAlign: "center", color: "var(--color-text-muted)", fontWeight: 600 }}>Loading problems...</div>
      ) : (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
              gap: "1.5rem",
            }}
          >
            {problems.map((problem) => (
              <div
                key={problem.id}
                onClick={() => handleProblemCardClick(problem.id)}
                onPointerDown={() => handleProblemPointerDown(problem.id)}
                onPointerUp={handleProblemPointerUpOrCancel}
                onPointerCancel={handleProblemPointerUpOrCancel}
                onPointerLeave={handleProblemPointerUpOrCancel}
                className="card-premium card-hover"
                style={{
                  cursor: "pointer",
                  opacity: problem.isDeleted ? 0.5 : 1,
                  display: "flex",
                  flexDirection: "column",
                  gap: "0.75rem"
                }}
              >
                {isSelectionMode && (
                  <label
                    onClick={(event) => event.stopPropagation()}
                    style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.25rem", fontWeight: 600, fontSize: "0.875rem", color: "var(--color-text-muted)" }}
                  >
                    <input
                      type="checkbox"
                      aria-label={`Select problem ${formatProblemReference(problem.id)}`}
                      checked={selectedProblemIds.has(problem.id)}
                      onChange={() => toggleProblemSelection(problem.id)}
                      style={{ width: "auto", cursor: "pointer" }}
                    />
                    Select problem
                  </label>
                )}
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "0.5rem" }}>
                  <strong title={problem.id} style={{ fontSize: "1rem", color: "var(--color-primary)", fontWeight: 700 }}>
                    Problem {formatProblemReference(problem.id)}
                  </strong>
                  <div style={{ display: "flex", gap: "0.375rem" }}>
                    <span
                      className="badge badge-muted"
                      style={{
                        fontSize: "0.75rem",
                        padding: "0.15rem 0.5rem",
                        borderRadius: "var(--radius-sm)",
                        fontWeight: 600,
                        textTransform: "none",
                        letterSpacing: "normal"
                      }}
                    >
                      {problem.problemType}
                    </span>
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
                  </div>
                </div>
                <div
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    fontSize: "0.925rem",
                    color: "var(--color-text)",
                    lineHeight: "1.4"
                  }}
                >
                  {problem.text}
                </div>
                <TagList tags={problem.tags} />
              </div>
            ))}
          </div>

          <Pagination
            page={page}
            totalPages={totalPages}
            onPageChange={setPage}
          />

          {problems.length === 0 && (
            <div style={{ textAlign: "center", padding: "3rem 1rem", color: "var(--color-text-muted)" }}>
              {selectedFolderId ? `No problems found in ${activeFolderLabel}` : "No problems found"}
            </div>
          )}
        </>
      )}
        </section>
      </div>
    </main>
  );
}
