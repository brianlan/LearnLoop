import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BulkBatch, BulkDraft, BulkItem } from "@/types/bulkIngestion";
import { TagInput } from "./TagInput";

const POLL_INTERVAL_MS = 2500;
const DEBOUNCE_MS = 500;
const BASE_RETRY_MS = 500;
const MAX_RETRY_MS = 4000;

function retryDelayMs(failureCount: number): number {
  return Math.min(BASE_RETRY_MS * 2 ** failureCount, MAX_RETRY_MS);
}

const PROBLEM_TYPES = [
  { value: "single-choice", label: "Single choice" },
  { value: "multi-choice", label: "Multiple choice" },
  { value: "fill-in-the-blank", label: "Fill in the blank" },
  { value: "short-answer", label: "Short answer" },
];

const SUBJECTS = [
  { value: "math", label: "Math" },
  { value: "english", label: "English" },
];

function defaultDraft(item: BulkItem): BulkDraft {
  return {
    text: item.draft.text ?? "",
    problemType: item.draft.problemType ?? "short-answer",
    graphDsl: item.draft.graphDsl ?? "",
    correctAnswer: item.draft.correctAnswer ?? "",
    tags: item.draft.tags ?? [],
    subject: item.draft.subject ?? "math",
  };
}

function statusLabel(status: string): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "extracting":
      return "Extracting...";
    case "ready":
      return "Ready";
    case "failed":
      return "Extraction failed";
    case "submit-failed":
      return "Submit failed";
    case "deleted":
      return "Deleted";
    case "submitted":
      return "Submitted";
    default:
      return status;
  }
}

export interface BulkReviewStepProps {
  batch: BulkBatch;
  isLoading: boolean;
  onRefresh: (batchId: string) => void | Promise<void>;
  onUpdateDraft: (
    itemId: string,
    draft: Partial<BulkDraft>,
  ) => void | Promise<void>;
  onRetry: (itemId: string) => void | Promise<void>;
  onDelete: (itemId: string) => void | Promise<void>;
  onUndoDelete: (itemId: string) => void | Promise<void>;
}

export function BulkReviewStep({
  batch,
  isLoading,
  onRefresh,
  onUpdateDraft,
  onRetry,
  onDelete,
  onUndoDelete,
}: BulkReviewStepProps) {
  const items = useMemo(
    () => [...batch.items].sort((a, b) => a.order - b.order),
    [batch.items],
  );
  const [selectedItemId, setSelectedItemId] = useState<string>(() => {
    const firstActionable = items.find((item) => item.status !== "deleted");
    return firstActionable?.itemId ?? items[0]?.itemId ?? "";
  });
  const [localDrafts, setLocalDrafts] = useState<Record<string, BulkDraft>>({});
  const [dirtyItems, setDirtyItems] = useState<Set<string>>(new Set());
  const [savingItems, setSavingItems] = useState<Set<string>>(new Set());
  const [saveFailures, setSaveFailures] = useState<Record<string, number>>({});
  const draftRefs = useRef<Record<string, BulkDraft>>({});
  const dirtyRefs = useRef<Set<string>>(new Set());
  const saveFailuresRef = useRef<Record<string, number>>({});

  const selectedItem = useMemo(
    () => items.find((item) => item.itemId === selectedItemId) || items[0],
    [items, selectedItemId],
  );

  const getItemDraft = useCallback(
    (item: BulkItem): BulkDraft => {
      return localDrafts[item.itemId] ?? defaultDraft(item);
    },
    [localDrafts],
  );

  const updateDraft = useCallback(
    (itemId: string, next: Partial<BulkDraft>) => {
      setLocalDrafts((prev) => {
        const updated = { ...prev[itemId], ...next };
        const merged = { ...prev, [itemId]: updated };
        draftRefs.current = merged;
        return merged;
      });
      setDirtyItems((prev) => {
        const nextSet = new Set(prev);
        nextSet.add(itemId);
        dirtyRefs.current = nextSet;
        return nextSet;
      });
    },
    [],
  );

  useEffect(() => {
    if (!selectedItem) return;
    setLocalDrafts((prev) => {
      if (prev[selectedItem.itemId] !== undefined) return prev;
      const initial = defaultDraft(selectedItem);
      const merged = { ...prev, [selectedItem.itemId]: initial };
      draftRefs.current = merged;
      return merged;
    });
  }, [selectedItem]);

  useEffect(() => {
    const timeoutIds: Record<string, number> = {};

    const scheduleSave = (itemId: string) => {
      window.clearTimeout(timeoutIds[itemId]);
      const failures = saveFailuresRef.current[itemId] ?? 0;
      timeoutIds[itemId] = window.setTimeout(() => {
        const draft = draftRefs.current[itemId];
        if (!draft) return;
        const sentDraft = JSON.parse(JSON.stringify(draft)) as BulkDraft;
        setSavingItems((prev) => {
          const next = new Set(prev);
          next.add(itemId);
          return next;
        });
        Promise.resolve(onUpdateDraft(itemId, draft))
          .then(() => {
            setSaveFailures((prev) => {
              const next = { ...prev };
              delete next[itemId];
              saveFailuresRef.current = next;
              return next;
            });
            setSavingItems((prev) => {
              const next = new Set(prev);
              next.delete(itemId);
              return next;
            });
            setDirtyItems((prevDirty) => {
              const nextDirty = new Set(prevDirty);
              if (
                JSON.stringify(draftRefs.current[itemId]) ===
                JSON.stringify(sentDraft)
              ) {
                nextDirty.delete(itemId);
              }
              dirtyRefs.current = nextDirty;
              return nextDirty;
            });
          })
          .catch(() => {
            setSaveFailures((prev) => {
              const next = { ...prev, [itemId]: (prev[itemId] ?? 0) + 1 };
              saveFailuresRef.current = next;
              return next;
            });
            setSavingItems((prev) => {
              const next = new Set(prev);
              next.delete(itemId);
              return next;
            });
          });
      }, retryDelayMs(failures));
    };

    dirtyItems.forEach((itemId) => {
      if (!savingItems.has(itemId)) {
        scheduleSave(itemId);
      }
    });

    return () => {
      Object.values(timeoutIds).forEach((id) => window.clearTimeout(id));
    };
  }, [dirtyItems, savingItems, onUpdateDraft]);

  useEffect(() => {
    const hasActiveExtraction = items.some(
      (item) => item.status === "queued" || item.status === "extracting",
    );
    if (!hasActiveExtraction || batch.status !== "active") return;

    const id = window.setInterval(() => {
      onRefresh(batch.id);
    }, POLL_INTERVAL_MS);

    return () => window.clearInterval(id);
  }, [batch.id, batch.status, items, onRefresh]);

  const selectedIndex = items.findIndex(
    (item) => item.itemId === selectedItem?.itemId,
  );

  const navigate = useCallback(
    (direction: -1 | 1) => {
      const nextIndex = selectedIndex + direction;
      if (nextIndex >= 0 && nextIndex < items.length) {
        setSelectedItemId(items[nextIndex].itemId);
      }
    },
    [items, selectedIndex],
  );

  if (!selectedItem) {
    return (
      <div data-testid="bulk-wizard-review-step">
        <h2>Review extracted items</h2>
        <p>No items to review.</p>
      </div>
    );
  }

  const sourceImage = batch.images.find(
    (image) => image.imageId === selectedItem.imageId,
  );
  const previewUrl = selectedItem.crop?.mediaUrl || sourceImage?.sourceImage?.mediaUrl || "";
  const isDeleted = selectedItem.status === "deleted";
  const isEditable =
    !isDeleted &&
    selectedItem.status !== "queued" &&
    selectedItem.status !== "extracting" &&
    selectedItem.status !== "submitted";
  const currentDraft = getItemDraft(selectedItem);
  const isWorking = isLoading || savingItems.has(selectedItem.itemId);
  const saveFailureCount = saveFailures[selectedItem.itemId] ?? 0;
  const hasSaveFailed = saveFailureCount > 0;

  return (
    <div data-testid="bulk-wizard-review-step">
      <h2>Review extracted items</h2>

      <div
        style={{
          display: "flex",
          gap: "16px",
          marginBottom: "16px",
        }}
      >
        <button
          type="button"
          data-testid="bulk-review-prev"
          onClick={() => navigate(-1)}
          disabled={selectedIndex <= 0}
        >
          Previous
        </button>
        <span data-testid="bulk-review-position">
          {selectedIndex + 1} / {items.length}
        </span>
        <button
          type="button"
          data-testid="bulk-review-next"
          onClick={() => navigate(1)}
          disabled={selectedIndex >= items.length - 1}
        >
          Next
        </button>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "240px 1fr",
          gap: "16px",
        }}
      >
        <div>
          <h3>Items</h3>
          <ul data-testid="bulk-review-queue" style={{ padding: 0, listStyle: "none" }}>
            {items.map((item) => (
              <li key={item.itemId}>
                <button
                  type="button"
                  data-testid={`bulk-review-item-${item.itemId}`}
                  onClick={() => setSelectedItemId(item.itemId)}
                  disabled={item.itemId === selectedItem.itemId}
                  style={{
                    width: "100%",
                    textAlign: "left",
                    background:
                      item.itemId === selectedItem.itemId
                        ? "var(--color-primary)"
                        : "transparent",
                    color:
                      item.itemId === selectedItem.itemId ? "white" : "inherit",
                  }}
                >
                  {item.order + 1}. {statusLabel(item.status)}
                  {saveFailures[item.itemId] !== undefined && (
                    <span style={{ fontSize: "0.85em", opacity: 0.8 }}>
                      {" "}
                      (save failed)
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "12px",
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              <span data-testid="bulk-review-status">
                {statusLabel(selectedItem.status)}
              </span>
              {hasSaveFailed && (
                <span
                  data-testid="bulk-review-save-status"
                  style={{ color: "var(--color-error, #dc2626)", fontSize: "0.85em" }}
                >
                  Save failed, retrying...
                </span>
              )}
            </div>
            <div style={{ display: "flex", gap: "8px" }}>
              {selectedItem.status === "failed" && (
                <button
                  type="button"
                  data-testid="bulk-review-retry"
                  onClick={() => onRetry(selectedItem.itemId)}
                  disabled={isWorking}
                >
                  Retry extraction
                </button>
              )}
              {isDeleted ? (
                <button
                  type="button"
                  data-testid="bulk-review-undo"
                  onClick={() => onUndoDelete(selectedItem.itemId)}
                  disabled={isWorking}
                >
                  Undo delete
                </button>
              ) : (
                <button
                  type="button"
                  data-testid="bulk-review-delete"
                  onClick={() => onDelete(selectedItem.itemId)}
                  disabled={isWorking}
                >
                  Delete
                </button>
              )}
            </div>
          </div>

          {selectedItem.extraction.failureMessage && (
            <div
              data-testid="bulk-review-failure"
              style={{ color: "var(--color-error, #dc2626)", marginBottom: "12px" }}
            >
              {selectedItem.extraction.failureMessage}
            </div>
          )}

          {previewUrl && (
            <img
              src={previewUrl}
              alt="Crop preview"
              data-testid="bulk-review-preview"
              style={{
                maxWidth: "100%",
                maxHeight: "200px",
                marginBottom: "12px",
                border: "1px solid var(--color-border)",
              }}
            />
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            <label>
              Text
              <textarea
                data-testid="bulk-review-text"
                value={currentDraft.text ?? ""}
                onChange={(event) =>
                  updateDraft(selectedItem.itemId, { text: event.target.value })
                }
                disabled={!isEditable || isWorking}
                rows={4}
                style={{ width: "100%" }}
              />
            </label>

            <div style={{ display: "flex", gap: "12px" }}>
              <label style={{ flex: 1 }}>
                Problem type
                <select
                  data-testid="bulk-review-type"
                  value={currentDraft.problemType ?? "short-answer"}
                  onChange={(event) =>
                    updateDraft(selectedItem.itemId, {
                      problemType: event.target.value,
                    })
                  }
                  disabled={!isEditable || isWorking}
                  style={{ width: "100%" }}
                >
                  {PROBLEM_TYPES.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              <label style={{ flex: 1 }}>
                Subject
                <select
                  data-testid="bulk-review-subject"
                  value={currentDraft.subject ?? "math"}
                  onChange={(event) =>
                    updateDraft(selectedItem.itemId, {
                      subject: event.target.value,
                    })
                  }
                  disabled={!isEditable || isWorking}
                  style={{ width: "100%" }}
                >
                  {SUBJECTS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label>
              Correct answer
              <input
                type="text"
                data-testid="bulk-review-answer"
                value={currentDraft.correctAnswer ?? ""}
                onChange={(event) =>
                  updateDraft(selectedItem.itemId, {
                    correctAnswer: event.target.value,
                  })
                }
                disabled={!isEditable || isWorking}
                style={{ width: "100%" }}
              />
            </label>

            <label>
              Graph DSL
              <input
                type="text"
                data-testid="bulk-review-graphdsl"
                value={currentDraft.graphDsl ?? ""}
                onChange={(event) =>
                  updateDraft(selectedItem.itemId, {
                    graphDsl: event.target.value,
                  })
                }
                disabled={!isEditable || isWorking}
                style={{ width: "100%" }}
              />
            </label>

            <TagInput
              tags={currentDraft.tags ?? []}
              onChange={(tags) =>
                updateDraft(selectedItem.itemId, { tags })
              }
              disabled={!isEditable || isWorking}
              label="Tags"
              testId="bulk-review-tags"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
