import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BulkBatch, BulkDraft, BulkItem } from "@/types/bulkIngestion";
import { TagInput } from "./TagInput";
import { GraphSandbox } from "./GraphSandbox";
import { LatexText } from "./LatexText";

const POLL_INTERVAL_MS = 2500;
const BASE_RETRY_MS = 500;
const MAX_RETRY_MS = 4000;
const ACTION_REQUIRED_BORDER = "2px solid var(--color-error, #dc2626)";

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

function serializeDraft(draft: BulkDraft): string {
  return JSON.stringify(draft);
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

function getRequiredFieldGaps(draft: BulkDraft) {
  return {
    text: !draft.text || draft.text.trim() === "",
    problemType: !draft.problemType,
    correctAnswer: !draft.correctAnswer || draft.correctAnswer.trim() === "",
  };
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
  onContinue: () => void;
  tagSuggestions?: string[];
}

export function BulkReviewStep({
  batch,
  isLoading,
  onRefresh,
  onUpdateDraft,
  onRetry,
  onDelete,
  onUndoDelete,
  onContinue,
  tagSuggestions = [],
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
  const serverDraftRefs = useRef<Record<string, string>>({});

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

  const reviewTagSuggestions = useMemo(() => {
    const seen = new Set<string>();
    const merged: string[] = [];

    const addTag = (tag: string) => {
      const trimmed = tag.trim();
      if (!trimmed || seen.has(trimmed)) return;
      seen.add(trimmed);
      merged.push(trimmed);
    };

    for (const tag of tagSuggestions) {
      addTag(tag);
    }
    for (const item of items) {
      for (const tag of getItemDraft(item).tags ?? []) {
        addTag(tag);
      }
    }

    return merged;
  }, [getItemDraft, items, tagSuggestions]);

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
    setLocalDrafts((prev) => {
      let next = prev;
      for (const item of items) {
        const itemId = item.itemId;
        const serverDraft = defaultDraft(item);
        const serializedServerDraft = serializeDraft(serverDraft);
        const previousServerDraft = serverDraftRefs.current[itemId];
        serverDraftRefs.current[itemId] = serializedServerDraft;

        if (previousServerDraft === serializedServerDraft) continue;
        if (prev[itemId] === undefined) continue;
        if (dirtyRefs.current.has(itemId)) continue;
        if (savingItems.has(itemId)) continue;
        if (saveFailuresRef.current[itemId] !== undefined) continue;
        if (serializeDraft(prev[itemId]) === serializedServerDraft) continue;

        if (next === prev) {
          next = { ...prev };
        }
        next[itemId] = serverDraft;
      }

      if (next !== prev) {
        draftRefs.current = next;
      }
      return next;
    });
  }, [items, savingItems]);

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
  const isActionWorking = isLoading || savingItems.has(selectedItem.itemId);
  const isFieldDisabled = !isEditable || isLoading;
  const saveFailureCount = saveFailures[selectedItem.itemId] ?? 0;
  const hasSaveFailed = saveFailureCount > 0;
  const activeItems = items.filter((item) => item.status !== "deleted");
  const itemValidation = activeItems.map((item) => {
    const reasons: string[] = [];
    const draft = getItemDraft(item);
    const requiredFieldGaps = getRequiredFieldGaps(draft);
    if (item.status === "queued" || item.status === "extracting") {
      reasons.push(`Item ${item.order + 1}: Extraction is still running`);
    } else if (item.status === "failed") {
      reasons.push(`Item ${item.order + 1}: Extraction failed`);
    } else if (item.status !== "ready" && item.status !== "submit-failed") {
      reasons.push(`Item ${item.order + 1}: Item is not ready`);
    }
    if (!draft.text || draft.text.trim() === "") {
      reasons.push(`Item ${item.order + 1}: Question text is required`);
    }
    if (!draft.problemType) {
      reasons.push(`Item ${item.order + 1}: Problem type is required`);
    }
    if (!draft.correctAnswer || draft.correctAnswer.trim() === "") {
      reasons.push(`Item ${item.order + 1}: Correct answer is required`);
    }
    return { itemId: item.itemId, reasons, requiredFieldGaps };
  });
  const itemValidationById = new Map(
    itemValidation.map((validation) => [validation.itemId, validation]),
  );
  const selectedValidation = itemValidationById.get(selectedItem.itemId);
  const selectedRequiredFieldGaps =
    selectedValidation?.requiredFieldGaps ?? getRequiredFieldGaps(currentDraft);
  const continueDisabledReasons = itemValidation.flatMap(
    (validation) => validation.reasons,
  );
  if (activeItems.length === 0) {
    continueDisabledReasons.push("No items to submit");
  }
  if (dirtyItems.size > 0 || savingItems.size > 0) {
    continueDisabledReasons.push("Draft changes are still saving");
  }
  if (Object.keys(saveFailures).length > 0) {
    continueDisabledReasons.push("Draft save failed, retrying");
  }
  const canContinue = continueDisabledReasons.length === 0;

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
        data-testid="bulk-review-layout"
        style={{
          display: "grid",
          gridTemplateColumns: "120px 1fr",
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
                  data-action-required={
                    (itemValidationById.get(item.itemId)?.reasons.length ?? 0) > 0
                      ? "true"
                      : "false"
                  }
                  onClick={() => setSelectedItemId(item.itemId)}
                  disabled={item.itemId === selectedItem.itemId}
                  style={{
                    width: "100%",
                    textAlign: "left",
                    border:
                      (itemValidationById.get(item.itemId)?.reasons.length ?? 0) > 0
                        ? ACTION_REQUIRED_BORDER
                        : "2px solid transparent",
                    borderRadius: "6px",
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
                  disabled={isActionWorking}
                >
                  Retry extraction
                </button>
              )}
              {isDeleted ? (
                <button
                  type="button"
                  data-testid="bulk-review-undo"
                  onClick={() => onUndoDelete(selectedItem.itemId)}
                  disabled={isActionWorking}
                >
                  Undo delete
                </button>
              ) : (
                <button
                  type="button"
                  data-testid="bulk-review-delete"
                  onClick={() => onDelete(selectedItem.itemId)}
                  disabled={isActionWorking}
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
                disabled={isFieldDisabled}
                rows={4}
                style={{
                  width: "100%",
                  border: selectedRequiredFieldGaps.text
                    ? ACTION_REQUIRED_BORDER
                    : undefined,
                }}
              />
            </label>

            <div>
              <div
                style={{
                  fontSize: "0.85em",
                  fontWeight: 600,
                  marginBottom: "6px",
                }}
              >
                Text preview
              </div>
              <div
                data-testid="bulk-review-text-preview"
                style={{
                  border: "1px solid var(--color-border)",
                  borderRadius: "6px",
                  padding: "12px",
                  minHeight: "64px",
                  backgroundColor: "var(--color-surface-muted)",
                }}
              >
                <LatexText
                  text={currentDraft.text ?? ""}
                  style={{ whiteSpace: "pre-wrap" }}
                />
              </div>
            </div>

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
                  disabled={isFieldDisabled}
                  style={{
                    width: "100%",
                    border: selectedRequiredFieldGaps.problemType
                      ? ACTION_REQUIRED_BORDER
                      : undefined,
                  }}
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
                  disabled={isFieldDisabled}
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
                disabled={isFieldDisabled}
                style={{
                  width: "100%",
                  border: selectedRequiredFieldGaps.correctAnswer
                    ? ACTION_REQUIRED_BORDER
                    : undefined,
                }}
              />
            </label>

            <label>
              Graph DSL
              <textarea
                data-testid="bulk-review-graphdsl"
                value={currentDraft.graphDsl ?? ""}
                onChange={(event) =>
                  updateDraft(selectedItem.itemId, {
                    graphDsl: event.target.value,
                  })
                }
                disabled={isFieldDisabled}
                rows={10}
                style={{
                  width: "100%",
                  minHeight: "180px",
                  resize: "vertical",
                  fontFamily: "monospace",
                  fontSize: "0.9em",
                  lineHeight: 1.4,
                }}
              />
            </label>

            {currentDraft.graphDsl?.trim() && (
              <div>
                <div
                  style={{
                    fontSize: "0.85em",
                    fontWeight: 600,
                    marginBottom: "6px",
                  }}
                >
                  Graph preview
                </div>
                <GraphSandbox dsl={currentDraft.graphDsl} height={300} />
              </div>
            )}

            <TagInput
              tags={currentDraft.tags ?? []}
              onChange={(tags) =>
                updateDraft(selectedItem.itemId, { tags })
              }
              suggestions={reviewTagSuggestions}
              placeholder="Add a tag..."
              disabled={isFieldDisabled}
              label="Tags"
              testId="bulk-review-tags"
            />
          </div>
        </div>
      </div>

      <div style={{ marginTop: "16px", textAlign: "right" }}>
        <button
          type="button"
          data-testid="bulk-review-continue"
          onClick={onContinue}
          disabled={!canContinue || isLoading}
        >
          Continue to submit
        </button>
      </div>
    </div>
  );
}
